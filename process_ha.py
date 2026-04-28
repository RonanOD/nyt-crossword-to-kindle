#!/usr/bin/env python3
"""Render the Home Status section of the morning briefing PDF.

Emits a single <div id="ha-daily-update"> ... </div> on stdout, suitable for
inclusion at the top of the CBC News HTML before weasyprint converts to PDF.

Fully deterministic — pulls live state from Home Assistant REST and renders
sections individually. A failing section logs to stderr and renders empty;
the rest of the briefing still shows.
"""

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

WRAPPER_STYLE = (
    'font-family: sans-serif; padding: 10px; '
    'border: 1px solid #ccc; border-radius: 5px;'
)
LOW_BATTERY_THRESHOLD = 20
FORECAST_HOURS = 12
WEATHER_ENTITY = 'weather.forecast_home'
WASTE_CALENDAR = 'calendar.halifax_ns'
SHOPPING_LIST = 'todo.shopping_list'


class HA:
    def __init__(self, base_url, token):
        self.base = base_url.rstrip('/')
        self.headers = {
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json',
        }
        self._states = None

    def _request(self, method, path, body=None, query=None):
        url = f'{self.base}{path}'
        if query:
            url += '?' + urlencode(query)
        data = json.dumps(body).encode('utf-8') if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers=self.headers)
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode('utf-8'))

    def states(self):
        if self._states is None:
            self._states = self._request('GET', '/api/states')
        return self._states

    def state(self, entity_id):
        return next(
            (s for s in self.states() if s.get('entity_id') == entity_id),
            None,
        )

    def calendar_events(self, entity_id, start, end):
        return self._request(
            'GET',
            f'/api/calendars/{entity_id}',
            query={'start': start.isoformat(), 'end': end.isoformat()},
        )

    def forecast(self, entity_id, ftype='hourly'):
        result = self._request(
            'POST',
            '/api/services/weather/get_forecasts',
            body={'entity_id': entity_id, 'type': ftype},
            query={'return_response': 'true'},
        )
        return (
            result.get('service_response', {})
            .get(entity_id, {})
            .get('forecast', [])
        )

    def todo_items(self, entity_id, status='needs_action'):
        result = self._request(
            'POST',
            '/api/services/todo/get_items',
            body={'entity_id': entity_id, 'status': status},
            query={'return_response': 'true'},
        )
        return (
            result.get('service_response', {})
            .get(entity_id, {})
            .get('items', [])
        )


def section(label):
    """Wrap a section renderer so a failure logs and returns ''."""
    def wrap(fn):
        def inner(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except Exception as e:
                print(f'[ha-section:{label}] {type(e).__name__}: {e}', file=sys.stderr)
                return ''
        return inner
    return wrap


def parse_iso(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def parse_event_time(event_side, tz):
    """HA REST returns event start/end as either an ISO string or a {date|dateTime}
    dict (varies by integration). Normalise to (datetime, is_all_day)."""
    if isinstance(event_side, dict):
        if 'dateTime' in event_side:
            return parse_iso(event_side['dateTime']).astimezone(tz), False
        if 'date' in event_side:
            return datetime.fromisoformat(event_side['date']).replace(tzinfo=tz), True
    elif isinstance(event_side, str):
        if len(event_side) == 10:
            return datetime.fromisoformat(event_side).replace(tzinfo=tz), True
        return parse_iso(event_side).astimezone(tz), False
    return None, False


def fmt_clock(dt):
    return dt.strftime('%-I:%M%p').lower().lstrip('0')


def fmt_num(value, suffix=''):
    if value is None:
        return '—'
    try:
        return f'{float(value):g}{suffix}'
    except (TypeError, ValueError):
        return f'{value}{suffix}'


@section('header')
def render_header(ha, tz):
    today = datetime.now(tz).strftime('%A, %B %-d, %Y')
    sun = ha.state('sun.sun') or {}
    attrs = sun.get('attributes', {})
    parts = [f'<strong>Home Status</strong> &middot; {today}']
    if attrs.get('next_setting'):
        set_dt = parse_iso(attrs['next_setting']).astimezone(tz)
        parts.append(f'Sunset {fmt_clock(set_dt)}')
    if attrs.get('next_rising'):
        rise_dt = parse_iso(attrs['next_rising']).astimezone(tz)
        label = 'Sunrise' if rise_dt.date() == datetime.now(tz).date() else "Tomorrow's sunrise"
        parts.append(f'{label} {fmt_clock(rise_dt)}')
    return f'<p style="margin: 0 0 10px 0;">{" &middot; ".join(parts)}</p>'


@section('weather_now')
def render_weather_now(ha, tz):
    w = ha.state(WEATHER_ENTITY)
    if not w:
        return ''
    attrs = w.get('attributes', {})
    condition = w.get('state', '').replace('_', ' ').replace('-', ' ').title()
    temp_unit = attrs.get('temperature_unit', '°C')
    bits = [f'<strong>{condition}</strong>']

    high = low = None
    try:
        daily = ha.forecast(WEATHER_ENTITY, ftype='daily')
        if daily:
            high = daily[0].get('temperature')
            low = daily[0].get('templow')
    except Exception as e:
        print(f'[ha-section:weather_now:daily] {type(e).__name__}: {e}', file=sys.stderr)

    if attrs.get('temperature') is not None:
        bits.append(f'now {fmt_num(attrs["temperature"], temp_unit)}')
    if high is not None and low is not None:
        bits.append(f'high {fmt_num(high, temp_unit)} / low {fmt_num(low, temp_unit)}')
    if attrs.get('humidity') is not None:
        bits.append(f'humidity {fmt_num(attrs["humidity"], "%")}')
    if attrs.get('wind_speed') is not None:
        bits.append(f'wind {fmt_num(attrs["wind_speed"], " " + attrs.get("wind_speed_unit", "km/h"))}')

    return f'<p style="margin: 0 0 10px 0;">{", ".join(bits)}</p>'


@section('hourly_forecast')
def render_hourly_forecast(ha, tz):
    forecast = ha.forecast(WEATHER_ENTITY, ftype='hourly')
    if not forecast:
        return ''
    points = []
    for entry in forecast[:FORECAST_HOURS]:
        try:
            dt = parse_iso(entry['datetime']).astimezone(tz)
            temp = float(entry['temperature'])
            precip = float(entry.get('precipitation_probability') or 0)
            points.append((dt, temp, precip))
        except (KeyError, TypeError, ValueError):
            continue
    if not points:
        return ''

    width, height = 600, 140
    pad_l, pad_r, pad_t, pad_b = 30, 10, 15, 30
    n = len(points)
    temps = [p[1] for p in points]
    precips = [p[2] for p in points]
    times = [p[0] for p in points]

    t_min, t_max = min(temps), max(temps)
    if t_max == t_min:
        t_max = t_min + 1

    def x_of(i):
        if n == 1:
            return pad_l + (width - pad_l - pad_r) / 2
        return pad_l + i * (width - pad_l - pad_r) / (n - 1)

    def y_of_temp(t):
        return pad_t + (t_max - t) / (t_max - t_min) * (height - pad_t - pad_b)

    bar_w = max(4, (width - pad_l - pad_r) / n - 4)
    bars = []
    for i, p in enumerate(precips):
        if p <= 0:
            continue
        bx = x_of(i) - bar_w / 2
        bh = (height - pad_t - pad_b) * (p / 100.0) * 0.4
        by = height - pad_b - bh
        bars.append(
            f'<rect x="{bx:.1f}" y="{by:.1f}" width="{bar_w:.1f}" '
            f'height="{bh:.1f}" fill="#bbb" />'
        )

    poly = ' '.join(f'{x_of(i):.1f},{y_of_temp(t):.1f}' for i, t in enumerate(temps))

    label_step = max(1, n // 4)
    x_labels = []
    for i in range(0, n, label_step):
        lbl = times[i].strftime('%-I%p').lower().replace('m', '')
        x_labels.append(
            f'<text x="{x_of(i):.1f}" y="{height - 8}" text-anchor="middle" '
            f'font-family="sans-serif" font-size="10" fill="#666">{lbl}</text>'
        )

    return f'''
    <div style="margin: 0 0 10px 0;">
        <svg width="100%" viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg">
            <line x1="{pad_l}" y1="{height - pad_b}" x2="{width - pad_r}" y2="{height - pad_b}" stroke="#ddd" stroke-width="1"/>
            {''.join(bars)}
            <polyline points="{poly}" fill="none" stroke="black" stroke-width="2" />
            <text x="{pad_l - 4}" y="{y_of_temp(t_max) + 4:.1f}" text-anchor="end" font-family="sans-serif" font-size="10" fill="black">{t_max:.0f}°</text>
            <text x="{pad_l - 4}" y="{y_of_temp(t_min) + 4:.1f}" text-anchor="end" font-family="sans-serif" font-size="10" fill="black">{t_min:.0f}°</text>
            {''.join(x_labels)}
        </svg>
        <p style="font-size: 10px; color: #666; margin: 0;">Next {FORECAST_HOURS}h: temperature line, precipitation chance bars.</p>
    </div>
    '''


@section('anomalies')
def render_anomalies(ha):
    states = ha.states()
    bullets = []

    for s in states:
        eid = s.get('entity_id', '')
        if eid.startswith('binary_sensor.') and 'leak' in eid and s.get('state') == 'on':
            name = s.get('attributes', {}).get('friendly_name', eid)
            bullets.append(f'<li><strong>WATER LEAK:</strong> {name}</li>')

    open_devices = []
    for s in states:
        if not s.get('entity_id', '').startswith('binary_sensor.'):
            continue
        attrs = s.get('attributes', {})
        if attrs.get('device_class') in ('door', 'window', 'opening', 'garage_door') and s.get('state') == 'on':
            open_devices.append(attrs.get('friendly_name', s['entity_id']))
    if open_devices:
        bullets.append(f'<li>Open: {", ".join(open_devices)}</li>')

    low = []
    for s in states:
        eid = s.get('entity_id', '')
        if not (eid.startswith('sensor.') and eid.endswith('_battery')):
            continue
        try:
            level = float(s['state'])
        except (KeyError, TypeError, ValueError):
            continue
        if level <= LOW_BATTERY_THRESHOLD:
            name = s.get('attributes', {}).get('friendly_name', eid).removesuffix(' Battery')
            low.append((level, name))
    if low:
        low.sort()
        rendered = ', '.join(f'{n} ({l:.0f}%)' for l, n in low[:6])
        bullets.append(f'<li>Low battery: {rendered}</li>')

    unlocked = [
        s.get('attributes', {}).get('friendly_name', s['entity_id'])
        for s in states
        if s.get('entity_id', '').startswith('lock.') and s.get('state') == 'unlocked'
    ]
    if unlocked:
        bullets.append(f'<li>Unlocked: {", ".join(unlocked)}</li>')

    if not bullets:
        return ''

    return (
        '<div style="margin: 0 0 10px 0;">'
        '<h3 style="margin: 0 0 4px 0;">Heads up</h3>'
        f'<ul style="margin: 0; padding-left: 20px;">{"".join(bullets)}</ul>'
        '</div>'
    )


@section('today_calendar')
def render_today_calendar(ha, tz):
    now = datetime.now(tz)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow = today_start + timedelta(days=1)
    waste_window_end = today_start + timedelta(days=2)

    rows = []

    try:
        waste_events = ha.calendar_events(WASTE_CALENDAR, today_start, waste_window_end)
        by_date = {}
        for e in waste_events:
            start_dt, _ = parse_event_time(e.get('start'), tz)
            if start_dt is None:
                continue
            by_date.setdefault(start_dt.date(), []).append(e.get('summary', '?'))
        if by_date:
            soonest = min(by_date)
            if soonest == today_start.date():
                label = 'Today'
            elif soonest == tomorrow.date():
                label = 'Tomorrow'
            else:
                label = soonest.strftime('%a %b %-d')
            rows.append(
                f'<li><strong>Waste {label}:</strong> {", ".join(by_date[soonest])}</li>'
            )
    except Exception as e:
        print(f'[ha-section:today_calendar:waste] {type(e).__name__}: {e}', file=sys.stderr)

    appointment_calendars = [
        s['entity_id']
        for s in ha.states()
        if s.get('entity_id', '').startswith('calendar.')
        and s['entity_id'] != WASTE_CALENDAR
    ]

    today_events = []
    for cal in appointment_calendars:
        try:
            events = ha.calendar_events(cal, today_start, tomorrow)
        except Exception as e:
            print(f'[ha-section:today_calendar:{cal}] {type(e).__name__}: {e}', file=sys.stderr)
            continue
        for ev in events:
            start_dt, all_day = parse_event_time(ev.get('start'), tz)
            if start_dt is None:
                continue
            summary = ev.get('summary', '')
            if all_day:
                today_events.append((today_start, f'All day: {summary}'))
            else:
                today_events.append((start_dt, f'{fmt_clock(start_dt)} {summary}'))

    today_events.sort(key=lambda x: x[0])
    seen = set()
    for _, label in today_events:
        if label in seen:
            continue
        seen.add(label)
        rows.append(f'<li>{label}</li>')

    if not rows:
        return ''

    return (
        '<div style="margin: 0 0 10px 0;">'
        '<h3 style="margin: 0 0 4px 0;">Today</h3>'
        f'<ul style="margin: 0; padding-left: 20px;">{"".join(rows)}</ul>'
        '</div>'
    )


@section('climate')
def render_climate_table(ha):
    rows = []
    for s in ha.states():
        if not s.get('entity_id', '').startswith('climate.'):
            continue
        attrs = s.get('attributes', {})
        name = attrs.get('friendly_name', s['entity_id'])
        cur = attrs.get('current_temperature')
        hum = attrs.get('current_humidity')
        target = attrs.get('temperature')
        action = attrs.get('hvac_action') or s.get('state', '—')
        rows.append(
            '<tr>'
            f'<td>{name}</td>'
            f'<td>{fmt_num(cur, "°")}</td>'
            f'<td>{fmt_num(hum, "%")}</td>'
            f'<td>{fmt_num(target, "°")}</td>'
            f'<td>{action}</td>'
            '</tr>'
        )

    if not rows:
        return ''

    return (
        '<div style="margin: 0 0 10px 0;">'
        '<h3 style="margin: 0 0 4px 0;">Indoor climate</h3>'
        '<table style="border-collapse: collapse; width: 100%; font-size: 13px;">'
        '<thead><tr style="text-align: left; border-bottom: 1px solid #ccc;">'
        '<th>Room</th><th>Temp</th><th>RH</th><th>Setpoint</th><th>Mode</th>'
        '</tr></thead>'
        f'<tbody>{"".join(rows)}</tbody>'
        '</table></div>'
    )


@section('shopping')
def render_shopping_list(ha):
    items = ha.todo_items(SHOPPING_LIST, status='needs_action')
    if not items:
        return ''
    bullets = ''.join(f'<li>{i.get("summary", "")}</li>' for i in items)
    return (
        '<div style="margin: 0 0 10px 0;">'
        '<h3 style="margin: 0 0 4px 0;">Shopping list</h3>'
        f'<ul style="margin: 0; padding-left: 20px;">{bullets}</ul>'
        '</div>'
    )


def main():
    load_dotenv()
    ha_url = os.getenv('HA_URL', 'http://192.168.68.104:8123')
    ha_token = os.getenv('HA_TOKEN')
    tz_name = os.getenv('TZ', 'America/Halifax')

    if not ha_token:
        print(
            f'<div id="ha-daily-update" style="{WRAPPER_STYLE}">'
            '<h2>Home Status</h2>'
            '<p>HA_TOKEN not set; skipping home status.</p>'
            '</div>'
        )
        return

    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo('America/Halifax')

    ha = HA(ha_url, ha_token)

    sections = [
        render_header(ha, tz),
        render_weather_now(ha, tz),
        render_hourly_forecast(ha, tz),
        render_anomalies(ha),
        render_today_calendar(ha, tz),
        render_climate_table(ha),
        render_shopping_list(ha),
    ]

    body = '\n'.join(s for s in sections if s)
    if not body:
        body = '<p>(No home data available right now.)</p>'

    print(f'<div id="ha-daily-update" style="{WRAPPER_STYLE}">\n{body}\n</div>')


if __name__ == '__main__':
    main()
