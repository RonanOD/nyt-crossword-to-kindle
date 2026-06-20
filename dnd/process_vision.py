#!/usr/bin/env python3
"""Interpret a marked-up Kindle Scribe page into a strict move JSON payload.

Sends the exported page (PDF or image) to Gemini with a data-extraction prompt:
the model is told to translate handwriting/checkboxes into structured data only,
never to invent narrative. Gemini accepts PDFs natively, so no rasterisation
step is needed. Prints the parsed move JSON on stdout.

The engine (later slice) consumes this; the renderer echoes `actions_summary`
back on the next page so the player can flag misreads. Treat the output as a
draft read of the handwriting, not ground truth.

Usage:
  python process_vision.py <path-to-pdf-or-image>

Env:
  GEMINI_API_KEY   required
  GEMINI_MODEL     optional, default 'gemini-2.5-flash'
"""

import json
import os
import sys

from dotenv import load_dotenv

MIME_BY_EXT = {
    '.pdf': 'application/pdf',
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
}

SYSTEM_INSTRUCTION = (
    'You are a data-extraction layer for a solo tabletop RPG played on a Kindle '
    'Scribe. You are given one page that the player has annotated by hand: ticked '
    'checkboxes, dice totals written on blank lines, and short notes. Translate '
    'ONLY what is physically marked on the page into structured data. Do NOT '
    'invent narrative, do NOT resolve combat, do NOT add anything not written. '
    'If a value is blank or you cannot read it, leave it null and lower your '
    'confidence. Report exactly what you see.'
)

PROMPT = (
    'Extract the player\'s move from this page. Respond with JSON only, matching '
    'this shape:\n'
    '{\n'
    '  "dice_rolls": [{"label": "to hit"|"damage"|"damage taken"|<other>, '
    '"total": <int or null>}],\n'
    '  "damage_dealt": <int or null>,\n'
    '  "damage_taken": <int or null>,\n'
    '  "heal_amount": <int total written in the Heal box for a potion, or null>,\n'
    '  "stealth_roll": <int total in the Stealth box for a sneak attempt, or null>,\n'
    '  "initiative_roll": <int total in the Initiative box, or null>,\n'
    '  "chosen_exit": <string direction like "north"/"down" or null>,\n'
    '  "checkboxes_marked": [<labels of ticked checkboxes, verbatim>],\n'
    '  "misread_flag": <true if the "x misread last move" box is ticked, else false>,\n'
    '  "actions_summary": <one short factual sentence describing what is marked>,\n'
    '  "confidence": "high"|"medium"|"low"\n'
    '}'
)


def load_part(path, client_types):
    ext = os.path.splitext(path)[1].lower()
    mime = MIME_BY_EXT.get(ext)
    if not mime:
        raise RuntimeError(f'Unsupported attachment type: {ext or "(none)"}')
    with open(path, 'rb') as f:
        data = f.read()
    return client_types.Part.from_bytes(data=data, mime_type=mime)


def parse_json(text):
    """Gemini is asked for raw JSON; strip code fences defensively."""
    text = text.strip()
    if text.startswith('```'):
        text = text.split('\n', 1)[1] if '\n' in text else text
        text = text.rsplit('```', 1)[0]
        text = text.removeprefix('json').strip()
    return json.loads(text)


def main():
    load_dotenv()
    if len(sys.argv) < 2:
        print('Usage: python process_vision.py <path>', file=sys.stderr)
        sys.exit(1)
    path = sys.argv[1]
    if not os.path.isfile(path):
        print(f'File not found: {path}', file=sys.stderr)
        sys.exit(1)

    api_key = (os.environ.get('GEMINI_API_KEY') or '').strip().strip('"').strip("'")
    if not api_key:
        print('GEMINI_API_KEY not set.', file=sys.stderr)
        sys.exit(1)
    model = (os.environ.get('GEMINI_MODEL') or 'gemini-2.5-flash').strip()

    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    part = load_part(path, types)

    resp = client.models.generate_content(
        model=model,
        contents=[PROMPT, part],
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            response_mime_type='application/json',
            temperature=0,
        ),
    )

    move = parse_json(resp.text)
    print(json.dumps(move, indent=2))


if __name__ == '__main__':
    main()
