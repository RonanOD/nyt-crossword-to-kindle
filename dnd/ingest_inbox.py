#!/usr/bin/env python3
"""Pull the newest unprocessed D&D reply from the Gmail inbox over IMAP.

The Kindle Scribe exports the marked-up page via "Send to email" to the burner
Gmail account (CROSSWORD_SENDER_EMAIL_*). This reads that inbox over IMAP using
the *same app password* already used for sending (mutt) — no OAuth, no Google
deps. Gmail app passwords work for IMAP as well as SMTP.

Finds the newest message (optionally filtered by sender/subject) that carries a
PDF or image attachment and whose Message-ID differs from the one last
processed. Saves the attachment to a temp file and prints a JSON descriptor on
stdout. Prints {"status": "no_new_reply"} when there is nothing new.

Usage:
  python ingest_inbox.py            # find + download newest unprocessed reply
  python ingest_inbox.py --check    # connectivity only: login, count, exit
  python ingest_inbox.py --since <message-id>   # override last-processed id

Reads the last-processed id from dnd/state.json by default.
"""

import email
import html as htmlmod
import imaplib
import json
import os
import re
import sys
import tempfile
import time
import urllib.request
from email.header import decode_header, make_header

from dotenv import load_dotenv

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
IMAP_HOST = 'imap.gmail.com'
IMAP_PORT = 993
ATTACH_EXTS = ('.pdf', '.png', '.jpg', '.jpeg')
ATTACH_MIMES = ('application/pdf', 'image/png', 'image/jpeg', 'image/jpg')
MAX_SCAN = 25  # only look this far back from the newest message


def log(msg):
    print(f'[ingest] {msg}', file=sys.stderr)


def env(name, default=None):
    val = os.environ.get(name, default)
    if isinstance(val, str):
        val = val.strip().strip('"').strip("'")
    return val


def account():
    prefix = env('CROSSWORD_SENDER_EMAIL_ADDRESS_PREFIX')
    domain = env('CROSSWORD_SENDER_EMAIL_ADDRESS_DOMAIN')
    password = env('CROSSWORD_SENDER_EMAIL_APP_PASSWORD')
    if not (prefix and domain and password):
        raise RuntimeError(
            'CROSSWORD_SENDER_EMAIL_ADDRESS_PREFIX/_DOMAIN/_APP_PASSWORD must be set.'
        )
    # App passwords are often shown with spaces; IMAP wants them stripped.
    return f'{prefix}@{domain}', password.replace(' ', '')


def last_processed_id():
    try:
        with open(os.path.join(SCRIPT_DIR, 'state.json'), encoding='utf-8') as f:
            return json.load(f).get('ingestion', {}).get('last_processed_message_id')
    except Exception as e:  # noqa: BLE001
        log(f'could not read state.json ({e}); treating all mail as new')
        return None


def hdr(value):
    if not value:
        return ''
    try:
        return str(make_header(decode_header(value)))
    except Exception:  # noqa: BLE001
        return value


def first_attachment(msg):
    """Return (filename, bytes) of the first PDF/image attachment, or None."""
    for part in msg.walk():
        if part.get_content_maintype() == 'multipart':
            continue
        filename = hdr(part.get_filename())
        ctype = (part.get_content_type() or '').lower()
        disp = (part.get('Content-Disposition') or '').lower()
        is_attach = 'attachment' in disp or bool(filename)
        looks_right = ctype in ATTACH_MIMES or (
            filename and filename.lower().endswith(ATTACH_EXTS)
        )
        if is_attach and looks_right:
            payload = part.get_payload(decode=True)
            if payload:
                return filename or f'attachment{_ext_for(ctype)}', payload
    return None


def _ext_for(ctype):
    if 'pdf' in ctype:
        return '.pdf'
    if 'png' in ctype:
        return '.png'
    return '.jpg'


def html_body(msg):
    parts = []
    for part in msg.walk():
        if part.get_content_type() == 'text/html':
            try:
                parts.append(part.get_payload(decode=True).decode('utf-8', 'ignore'))
            except Exception:  # noqa: BLE001
                pass
    return '\n'.join(parts)


def download_share_link(body):
    """The Kindle Scribe's 'send a file' email carries a 'Download PDF' link
    (an amazon.* /gp/f.html wrapper) that redirects to a pre-signed S3 URL
    serving the PDF with no auth. Follow it and return (bytes, name) or None."""
    anchors = re.findall(r'<a\s[^>]*href="([^"]+)"[^>]*>(.*?)</a>', body, re.I | re.S)
    for href, text in anchors:
        label = re.sub(r'<[^>]+>', '', text).strip().lower()
        if 'download' not in label or 'amazon' not in href.lower():
            continue
        url = htmlmod.unescape(href)
        data = ctype = final = None
        for attempt in range(3):  # Amazon intermittently 503s these links
            try:
                req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(req, timeout=60) as resp:  # follows redirects
                    data = resp.read()
                    ctype = (resp.headers.get('Content-Type') or '').lower()
                    final = resp.geturl()
                break
            except Exception as e:  # noqa: BLE001
                log(f'share-link fetch attempt {attempt + 1}/3 failed: '
                    f'{type(e).__name__}: {e}')
                time.sleep(2 * (attempt + 1))
        if data is None:
            continue
        if data[:4] == b'%PDF' or 'pdf' in ctype:
            name = os.path.basename(final.split('?')[0]) or 'shared-page.pdf'
            return data, name
        log('share link did not resolve to a PDF; skipping')
    return None


def connect():
    addr, password = account()
    log(f'connecting to {IMAP_HOST} as {addr}')
    conn = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    conn.login(addr, password)
    return conn


def find_reply(conn, last_id, from_filter, subject_filter):
    conn.select('INBOX', readonly=True)
    typ, data = conn.uid('SEARCH', None, 'ALL')
    if typ != 'OK':
        raise RuntimeError(f'IMAP SEARCH failed: {typ}')
    uids = data[0].split()
    if not uids:
        return None

    for uid in reversed(uids[-MAX_SCAN:]):
        typ, raw = conn.uid('FETCH', uid, '(BODY.PEEK[])')
        if typ != 'OK' or not raw or not raw[0]:
            continue
        msg = email.message_from_bytes(raw[0][1])
        msg_id = (msg.get('Message-ID') or '').strip()

        # Everything older than the last-processed message is already handled.
        if last_id and msg_id == last_id:
            log('reached last-processed message; nothing newer to handle')
            return None

        sender = hdr(msg.get('From'))
        subject = hdr(msg.get('Subject'))
        if from_filter and from_filter.lower() not in sender.lower():
            continue
        if subject_filter and subject_filter.lower() not in subject.lower():
            continue

        # Prefer a real attachment; fall back to the Scribe's "Download PDF" link.
        source = 'attachment'
        att = first_attachment(msg)
        if not att:
            att = download_share_link(html_body(msg))
            source = 'share_link'
        if not att:
            continue

        filename, payload = att if source == 'attachment' else (att[1], att[0])
        suffix = os.path.splitext(filename)[1] or '.bin'
        fd, path = tempfile.mkstemp(prefix='dnd-reply-', suffix=suffix, dir='/crosswords/tmp')
        with os.fdopen(fd, 'wb') as f:
            f.write(payload)
        return {
            'status': 'new_reply',
            'message_id': msg_id,
            'from': sender,
            'subject': subject,
            'date': msg.get('Date'),
            'source': source,
            'attachment_path': path,
            'attachment_name': filename,
        }
    return None


def main():
    load_dotenv()
    argv = sys.argv[1:]

    if '--check' in argv:
        conn = connect()
        try:
            conn.select('INBOX', readonly=True)
            typ, data = conn.uid('SEARCH', None, 'ALL')
            count = len(data[0].split()) if typ == 'OK' and data and data[0] else 0
            log(f'login OK; INBOX has {count} message(s)')
            print(json.dumps({'status': 'ok', 'message_count': count}))
        finally:
            conn.logout()
        return

    last_id = None
    if '--since' in argv:
        last_id = argv[argv.index('--since') + 1]
    else:
        last_id = last_processed_id()

    from_filter = env('DND_REPLY_FROM')
    subject_filter = env('DND_REPLY_SUBJECT_CONTAINS')

    conn = connect()
    try:
        result = find_reply(conn, last_id, from_filter, subject_filter)
    finally:
        conn.logout()

    if not result:
        print(json.dumps({'status': 'no_new_reply'}))
        return
    log(f'downloaded attachment to {result["attachment_path"]}')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
