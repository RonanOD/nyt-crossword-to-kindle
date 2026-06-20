#!/usr/bin/env python3
"""Render the daily solo-D&D workbook page as a full HTML document on stdout.

Deterministic: reads campaign.json (immutable) + state.json (mutable) and lays
out a single Kindle-Scribe page. No AI, no network. Mirrors process_ha.py: each
section is wrapped so a failure logs to stderr and renders empty rather than
killing the page. weasyprint (driven by download-dnd.sh) turns the stdout HTML
into the PDF.

Layout (top to bottom):
  - Header     : campaign, turn, character HP/AC line
  - Narrative  : current room description + echo-back of last interpreted move
  - Map        : fog-of-war SVG (discovered solid, frontier dashed "?")
  - Encounter  : SVG monster token + stat box for each living monster
  - Notes      : grid-lined freeform zone with exit + action checkboxes
"""

import html
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CELL = 90          # map grid cell size (px in the SVG viewBox)
GAP = 34           # gap between cells


def section(label):
    """Wrap a section renderer so a failure logs and returns ''."""
    def wrap(fn):
        def inner(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except Exception as e:  # noqa: BLE001 - fail-soft by design
                print(f'[dnd-section:{label}] {type(e).__name__}: {e}', file=sys.stderr)
                return ''
        return inner
    return wrap


def load_json(name, env_override=None):
    path = os.environ.get(env_override) if env_override else None
    if not path:
        path = os.path.join(SCRIPT_DIR, name)
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def esc(value):
    return html.escape(str(value))


# Weapon -> damage die, for the in-combat dice reminder. Looked up by substring
# against inventory item names; falls back to a generic die if nothing matches.
WEAPON_DICE = {
    'greatsword': ('Greatsword', '2d6'),
    'greataxe': ('Greataxe', '1d12'),
    'longsword': ('Longsword', '1d8'),
    'battleaxe': ('Battleaxe', '1d8'),
    'warhammer': ('Warhammer', '1d8'),
    'rapier': ('Rapier', '1d8'),
    'shortsword': ('Shortsword', '1d6'),
    'handaxe': ('Handaxe', '1d6'),
    'mace': ('Mace', '1d6'),
    'spear': ('Spear', '1d6'),
    'dagger': ('Dagger', '1d4'),
}


def proficiency_bonus(level):
    try:
        return 2 + max(0, (int(level) - 1) // 4)
    except (TypeError, ValueError):
        return 2


def _signed(n):
    return f'+{n}' if n >= 0 else str(n)


def combat_help(state, living):
    """Per-action dice/DC reminder shown during a fight, with modifiers."""
    ch = state.get('character', {})
    mods = ch.get('modifiers') or {}
    str_mod = mods.get('strength', 0)
    dex_mod = mods.get('dexterity', 0)
    prof = proficiency_bonus(ch.get('level', 1))
    atk = str_mod + prof

    die = '1d8'
    for item in ch.get('inventory') or []:
        match = next((v for k, v in WEAPON_DICE.items() if k in str(item).lower()), None)
        if match:
            die = match[1]
            break

    stealth_b = dex_mod
    if 'stealth' in [str(s).lower() for s in (ch.get('skill_proficiencies') or [])]:
        stealth_b += prof
    perc_dc = max((m.get('passive_perception', 10) for m in living), default=10)
    mon_init = max((10 + m.get('dex_mod', 0) for m in living), default=10)

    rows = [
        f'<div><strong>Attack:</strong> 1d20{_signed(atk)} vs AC '
        f'<span class="dim">(STR {_signed(str_mod)}, prof +{prof})</span> &middot; '
        f'dmg {die}{_signed(str_mod)}</div>',
        f'<div><strong>Sneak past:</strong> Stealth 1d20{_signed(stealth_b)} '
        f'vs passive Perception {perc_dc} <span class="dim">(+ tick an exit)</span></div>',
        '<div><strong>Dodge:</strong> enemy attacks at disadvantage '
        '<span class="dim">(roll its attack twice, take the lower)</span></div>',
        '<div><strong>Flee:</strong> retreat <span class="dim">(+ tick an exit; '
        'provokes one attack)</span></div>',
        f'<div><strong>Initiative:</strong> 1d20{_signed(dex_mod)} vs {mon_init} '
        '<span class="dim">(win &rarr; strike first)</span></div>',
    ]
    return '<div class="combathelp">' + ''.join(rows) + '</div>'


def living_monsters(node, gs):
    """Monsters in a node still standing per current monster_hp / defeated list."""
    defeated = set(gs.get('defeated_monsters') or [])
    monster_hp = gs.get('monster_hp') or {}
    out = []
    for m in node.get('monsters', []):
        mid = m.get('id')
        hp = monster_hp.get(mid, m.get('hp'))
        if mid in defeated or (hp is not None and hp <= 0):
            continue
        out.append(m)
    return out


# --- SVG monster tokens (simple line-art silhouettes, no external assets) -----

def _token_svg(name):
    """Return a 60x60 inline SVG silhouette keyed loosely off the monster name."""
    n = name.lower()
    stroke = 'stroke="black" stroke-width="2.5" fill="none"'
    if 'rat' in n:
        body = (
            f'<ellipse cx="30" cy="36" rx="18" ry="11" {stroke}/>'
            f'<circle cx="46" cy="30" r="7" {stroke}/>'
            f'<path d="M12 38 q-9 4 -7 12" {stroke}/>'  # tail
            f'<circle cx="48" cy="28" r="1.5" fill="black"/>'
        )
    elif 'goblin' in n:
        body = (
            f'<circle cx="30" cy="22" r="11" {stroke}/>'
            f'<path d="M19 22 L11 14 M41 22 L49 14" {stroke}/>'  # ears
            f'<path d="M22 33 L22 50 M38 33 L38 50 M22 50 L38 50" {stroke}/>'
            f'<circle cx="26" cy="22" r="1.5" fill="black"/>'
            f'<circle cx="34" cy="22" r="1.5" fill="black"/>'
        )
    elif 'blight' in n or 'twig' in n:
        body = (
            f'<path d="M30 52 L30 18" {stroke}/>'
            f'<path d="M30 30 L18 18 M30 34 L44 22 M30 24 L22 12 M30 26 L40 14" {stroke}/>'
        )
    else:
        body = (
            f'<circle cx="30" cy="30" r="18" {stroke}/>'
            f'<text x="30" y="36" text-anchor="middle" font-family="serif" '
            f'font-size="20" fill="black">{esc(name[:1].upper())}</text>'
        )
    return (
        f'<svg width="56" height="56" viewBox="0 0 60 60" '
        f'xmlns="http://www.w3.org/2000/svg">{body}</svg>'
    )


# --- Sections -----------------------------------------------------------------

@section('header')
def render_header(campaign, state):
    ch = state.get('character', {})
    gs = state.get('game_state', {})
    name = esc(ch.get('name', 'Hero'))
    klass = esc(ch.get('class', ''))
    level = esc(ch.get('level', 1))
    hp = f"{ch.get('current_hp', '?')}/{ch.get('max_hp', '?')}"
    ac = esc(ch.get('ac', '?'))
    turn = esc(gs.get('turn_count', 0))
    inv = ch.get('inventory') or []
    inv_html = (
        f'<div class="inv">Carrying: {esc(", ".join(str(i) for i in inv))}</div>'
        if inv else ''
    )
    return (
        f'<div class="hdr">'
        f'<div class="title">{esc(campaign.get("campaign_name", "Solo Campaign"))}</div>'
        f'<div class="stats">{name} &middot; {klass} {level} &middot; '
        f'HP {esc(hp)} &middot; AC {ac} &middot; Turn {turn}</div>'
        f'{inv_html}'
        f'</div>'
    )


@section('narrative')
def render_narrative(campaign, state):
    gs = state.get('game_state', {})
    node = campaign['nodes'][gs['current_node']]
    log = state.get('ingestion', {}).get('turn_log', [])

    echo = ''
    if log:
        last = log[-1]
        # Engine stores read (OCR's understanding) + result (what it did);
        # older entries used a single 'summary'.
        read = esc(last.get('read') or last.get('summary', ''))
        result = esc(last.get('result', ''))
        result_html = f'<br><strong>Result:</strong> {result}' if result else ''
        echo = (
            '<div class="echo">'
            '<strong>Last move, as I read it:</strong> '
            f'{read}{result_html} '
            '<span class="echo-q">&nbsp;&#9744; &times; misread &mdash; correct in Notes</span>'
            '</div>'
        )
    else:
        echo = (
            '<div class="echo"><strong>New campaign.</strong> '
            'Make your first move and email the marked-up page back.</div>'
        )

    return (
        '<div class="narr">'
        f'<h2>{esc(node.get("title", ""))}</h2>'
        f'<p>{esc(node.get("description", ""))}</p>'
        f'{echo}'
        '</div>'
    )


@section('map')
def render_map(campaign, state):
    gs = state.get('game_state', {})
    nodes = campaign['nodes']
    discovered = set(gs.get('discovered_nodes', []))
    current = gs.get('current_node')

    # Frontier = undiscovered nodes reachable from a discovered node.
    frontier = set()
    for nid in discovered:
        for dest in nodes.get(nid, {}).get('exits', {}).values():
            if dest in nodes and dest not in discovered:
                frontier.add(dest)

    visible = discovered | frontier
    if not visible:
        return ''

    def cx(node):
        return GAP + node['x'] * (CELL + GAP) + CELL / 2

    def cy(node):
        return GAP + node['y'] * (CELL + GAP) + CELL / 2

    # Edges between two discovered nodes.
    edges = []
    drawn = set()
    for nid in discovered:
        n = nodes[nid]
        for dest in n.get('exits', {}).values():
            if dest in discovered and (dest, nid) not in drawn:
                drawn.add((nid, dest))
                edges.append(
                    f'<line x1="{cx(n):.0f}" y1="{cy(n):.0f}" '
                    f'x2="{cx(nodes[dest]):.0f}" y2="{cy(nodes[dest]):.0f}" '
                    f'stroke="#999" stroke-width="2"/>'
                )

    cells = []
    for nid in visible:
        n = nodes[nid]
        x = GAP + n['x'] * (CELL + GAP)
        y = GAP + n['y'] * (CELL + GAP)
        if nid in discovered:
            border = 'black'
            sw = 6 if nid == current else 2
            label = esc(n.get('title', '').replace('The ', ''))
            here = ('<text x="%d" y="%d" text-anchor="middle" font-family="serif" '
                    'font-size="13" fill="black">YOU</text>' % (x + CELL / 2, y + CELL - 10)
                    ) if nid == current else ''
            cells.append(
                f'<rect x="{x}" y="{y}" width="{CELL}" height="{CELL}" rx="6" '
                f'fill="#fff" stroke="{border}" stroke-width="{sw}"/>'
                f'<foreignObject x="{x}" y="{y + 8}" width="{CELL}" height="{CELL - 20}">'
                f'<div xmlns="http://www.w3.org/1999/xhtml" '
                f'style="font:600 12px serif;text-align:center;padding:0 4px;">{label}</div>'
                f'</foreignObject>{here}'
            )
        else:
            cells.append(
                f'<rect x="{x}" y="{y}" width="{CELL}" height="{CELL}" rx="6" '
                f'fill="#f4f4f4" stroke="#999" stroke-width="2" stroke-dasharray="6 5"/>'
                f'<text x="{x + CELL / 2}" y="{y + CELL / 2 + 9}" text-anchor="middle" '
                f'font-family="serif" font-size="30" fill="#999">?</text>'
            )

    # Crop the viewBox to the bounding box of visible cells (+ padding) so the
    # early game doesn't render a near-empty 5x5 grid.
    xs = [nodes[n]['x'] for n in visible]
    ys = [nodes[n]['y'] for n in visible]
    pad = GAP
    vb_x = GAP + min(xs) * (CELL + GAP) - pad
    vb_y = GAP + min(ys) * (CELL + GAP) - pad
    vb_w = (max(xs) - min(xs)) * (CELL + GAP) + CELL + 2 * pad
    vb_h = (max(ys) - min(ys)) * (CELL + GAP) + CELL + 2 * pad

    return (
        '<div class="map"><h3>Map</h3>'
        f'<svg width="100%" viewBox="{vb_x:.0f} {vb_y:.0f} {vb_w:.0f} {vb_h:.0f}" '
        f'preserveAspectRatio="xMidYMid meet" xmlns="http://www.w3.org/2000/svg">'
        f'{"".join(edges)}{"".join(cells)}</svg></div>'
    )


@section('encounter')
def render_encounter(campaign, state):
    gs = state.get('game_state', {})
    monster_hp = gs.get('monster_hp') or {}
    node = campaign['nodes'][gs['current_node']]
    alive = living_monsters(node, gs)
    if not alive:
        return (
            '<div class="enc"><h3>Encounter</h3>'
            '<p class="clear">No enemies here. Choose an exit.</p></div>'
        )

    boxes = []
    for m in alive:
        cur = monster_hp.get(m.get('id'), m.get('hp'))
        boxes.append(
            '<div class="mon">'
            f'<div class="tok">{_token_svg(m.get("name", "?"))}</div>'
            '<div class="mstat">'
            f'<div class="mname">{esc(m.get("name", "?"))}</div>'
            f'<div class="mline">AC <strong>{esc(m.get("ac", "?"))}</strong>'
            f' &middot; HP <strong>{esc(cur)}</strong>/{esc(m.get("hp", "?"))}</div>'
            f'<div class="mline">{esc(m.get("attack", ""))}</div>'
            '</div></div>'
        )
    # The per-action dice guide lives here, under the monster(s), to use the
    # otherwise-empty left column and keep the page to one sheet.
    return (
        f'<div class="enc"><h3>Encounter</h3>{"".join(boxes)}'
        f'{combat_help(state, alive)}</div>'
    )


@section('notes')
def render_notes(campaign, state):
    gs = state.get('game_state', {})
    ch = state.get('character', {})
    node = campaign['nodes'][gs['current_node']]
    exits = node.get('exits', {})
    has_potion = any('potion' in str(i).lower() for i in (ch.get('inventory') or []))
    in_combat = bool(living_monsters(node, gs))

    exit_boxes = ''.join(
        f'<label>&#9744; Go {esc(direction)}</label>'
        for direction in exits
    )
    potion_box = '<label>&#9744; Drink potion (2d4+2)</label>' if has_potion else ''

    if in_combat:
        action_boxes = (
            '<label>&#9744; Attack</label>'
            '<label>&#9744; Sneak past</label>'
            '<label>&#9744; Dodge</label>'
            '<label>&#9744; Flee</label>'
            '<label>&#9744; Roll initiative</label>'
            '<label>&#9744; Search</label>'
            f'{potion_box}'
            '<label>&#9744; &times; misread last move</label>'
        )
    else:
        action_boxes = (
            '<label>&#9744; Search</label>'
            f'{potion_box}'
            '<label>&#9744; &times; misread last move</label>'
        )

    # Distinct, labelled, single-purpose write-in boxes (the OCR test showed a
    # run-on line let numbers land in the wrong slot). Combat boxes only appear
    # in a fight; Heal only when a potion is carried. The row wraps if needed.
    labels = (['To hit', 'Damage', 'Dmg taken', 'Stealth', 'Init'] if in_combat else [])
    if has_potion:
        labels.append('Heal')
    roll_boxes = ''.join(
        f'<div class="rollbox"><span class="rolllbl">{lbl}</span>'
        '<span class="rollfill"></span></div>'
        for lbl in labels
    )
    rolls_html = f'<div class="rolls">{roll_boxes}</div>' if labels else ''
    # The freeform note pad is for exploration turns; in a fight the dice guide +
    # roll boxes fill the column, so drop it to keep the busiest page on one sheet.
    pad_html = '' if in_combat else '<div class="pad"></div>'

    return (
        '<div class="notes"><h3>Your Move</h3>'
        f'<div class="choices">{exit_boxes}{action_boxes}</div>'
        f'{rolls_html}'
        f'{pad_html}'
        '</div>'
    )


def render_terminal(campaign, state):
    """Render a campaign-over page (victory or defeat) with no move zone."""
    gs = state.get('game_state', {})
    ch = state.get('character', {})
    status = gs.get('status')
    log = state.get('ingestion', {}).get('turn_log', [])
    last_result = esc(log[-1].get('result', '')) if log else ''
    if status == 'won':
        banner, sub = 'Victory', 'You have conquered the Sunken Vault.'
    else:
        banner, sub = 'You Have Fallen', 'The Sunken Vault claims another adventurer.'
    return (
        f'<div class="term"><div class="banner">{banner}</div>'
        f'<p class="sub">{sub}</p>'
        f'<p class="termline">{last_result}</p>'
        f'<p class="termline">{esc(ch.get("name", "Hero"))} &middot; '
        f'{esc(gs.get("turn_count", 0))} turns &middot; '
        f'HP {esc(ch.get("current_hp", "?"))}/{esc(ch.get("max_hp", "?"))}</p></div>'
    )


CSS = """
@page { size: 156mm 208mm; margin: 9mm; }
* { box-sizing: border-box; }
body { font-family: 'DejaVu Serif', serif; color: #000; margin: 0; }
.hdr { border-bottom: 2px solid #000; padding-bottom: 4px; margin-bottom: 6px; }
.hdr .title { font-size: 17px; font-weight: 700; }
.hdr .stats { font-size: 12px; color: #333; }
.hdr .inv { font-size: 10px; color: #555; margin-top: 1px; }
h2 { font-size: 16px; margin: 4px 0; }
h3 { font-size: 13px; margin: 4px 0; text-transform: uppercase; letter-spacing: 1px; }
.narr p { font-size: 13px; line-height: 1.35; margin: 2px 0 6px 0; }
.echo { font-size: 11px; line-height: 1.3; border: 1px solid #888; border-radius: 4px;
        padding: 4px 6px; background: #f7f7f7; }
.echo-q { color: #444; }
.map { text-align: center; margin: 4px 0; }
.map svg { max-height: 140px; }
.lower { display: table; width: 100%; table-layout: fixed; margin-top: 6px; }
.enc, .notes { display: table-cell; vertical-align: top; width: 50%; padding: 0 6px; }
.enc { border-right: 1px dashed #aaa; }
.mon { display: flex; align-items: center; gap: 8px; border: 1px solid #000;
       border-radius: 5px; padding: 6px; margin-bottom: 6px; }
.mname { font-weight: 700; font-size: 13px; }
.mline { font-size: 11px; }
.hpboxes { font-size: 13px; margin-top: 2px; letter-spacing: 1px; }
.clear { font-size: 12px; font-style: italic; }
.choices { display: flex; flex-wrap: wrap; gap: 4px 12px; font-size: 12px;
           margin-bottom: 8px; }
.choices label { display: inline-block; }
.combathelp { font-size: 10px; margin: 0 0 5px 0; line-height: 1.3; }
.combathelp div { margin: 1px 0; }
.combathelp .dim { color: #666; }
.rolls { display: flex; flex-wrap: wrap; gap: 5px; margin-bottom: 6px; }
.rollbox { flex: 1 0 76px; border: 1px solid #000; border-radius: 4px; padding: 3px 4px; }
.rolllbl { display: block; font-size: 9px; text-transform: uppercase;
           letter-spacing: 1px; color: #444; }
.rollfill { display: block; height: 26px; }
.pad { height: 64px; border: 1px solid #ccc; border-radius: 4px;
       background-image: repeating-linear-gradient(to bottom, #fff, #fff 26px, #eee 26px, #eee 27px); }
.term { text-align: center; padding-top: 60px; }
.banner { font-size: 40px; font-weight: 700; letter-spacing: 2px; }
.term .sub { font-size: 15px; margin-top: 8px; }
.termline { font-size: 13px; color: #333; margin: 14px 0 0 0; }
"""


def main():
    campaign = load_json('campaign.json', 'DND_CAMPAIGN_FILE')
    state = load_json('state.json', 'DND_STATE_FILE')

    if state.get('game_state', {}).get('status') in ('won', 'dead'):
        body = render_header(campaign, state) + render_terminal(campaign, state)
        print(
            '<!DOCTYPE html><html><head><meta charset="utf-8">'
            f'<style>{CSS}</style></head><body>{body}</body></html>'
        )
        return

    top = '\n'.join(s for s in (
        render_header(campaign, state),
        render_narrative(campaign, state),
        render_map(campaign, state),
    ) if s)

    lower = (
        '<div class="lower">'
        f'{render_encounter(campaign, state)}'
        f'{render_notes(campaign, state)}'
        '</div>'
    )

    print(
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        f'<style>{CSS}</style></head><body>'
        f'{top}{lower}'
        '</body></html>'
    )


if __name__ == '__main__':
    main()
