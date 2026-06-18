#!/usr/bin/env python3
"""Apply one interpreted move to the campaign state. Pure, deterministic.

Reads the move JSON produced by process_vision.py, resolves it against the
immutable campaign.json, and writes the updated state.json back in place. No
AI, no dice rolling — the *player* rolls physically and records the totals; the
engine only does bookkeeping (compare to-hit vs AC, subtract HP, move rooms,
detect win/lose). This keeps the rules honest and the campaign on-rails.

Usage:
  python engine.py <move.json> [--message-id <id>]

The move file is the JSON from process_vision.py. --message-id (or a
"message_id" key inside the move) is recorded so the same reply is never
applied twice. Prints a JSON summary of what changed on stdout.

Env: DND_CAMPAIGN_FILE / DND_STATE_FILE override the default dnd/*.json paths.
"""

import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def path_for(name, env_override):
    return os.environ.get(env_override) or os.path.join(SCRIPT_DIR, name)


def load(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def find_roll(move, keyword):
    for r in move.get('dice_rolls') or []:
        if keyword in (r.get('label') or '').lower():
            return r.get('total')
    return None


def living_in(node, monster_hp):
    return [
        m for m in node.get('monsters', [])
        if monster_hp.get(m['id'], m['hp']) > 0
    ]


def ensure_monsters(node, monster_hp):
    for m in node.get('monsters', []):
        monster_hp.setdefault(m['id'], m['hp'])


def apply_move(state, campaign, move, message_id):
    gs = state['game_state']
    ing = state.setdefault('ingestion', {})
    ch = state['character']
    events = []

    monster_hp = gs.setdefault('monster_hp', {})
    gs.setdefault('status', 'active')
    node = campaign['nodes'][gs['current_node']]
    ensure_monsters(node, monster_hp)

    if move.get('misread_flag'):
        events.append('You flagged the previous read as a misread.')

    # --- Player attack on the first living monster -------------------------
    target = (living_in(node, monster_hp) or [None])[0]
    to_hit = find_roll(move, 'hit')
    damage = move.get('damage_dealt')
    if damage is None:
        damage = find_roll(move, 'damage')
    attacked = bool(
        target and (damage is not None or to_hit is not None
                    or 'attack' in [c.lower() for c in move.get('checkboxes_marked', [])])
    )

    if attacked:
        ac = target.get('ac')
        if to_hit is not None and ac is not None and to_hit < ac:
            events.append(f'Missed {target["name"]} (rolled {to_hit} vs AC {ac}).')
        else:
            dealt = damage or 0
            monster_hp[target['id']] = max(0, monster_hp[target['id']] - dealt)
            roll_note = f' (rolled {to_hit} vs AC {ac})' if to_hit is not None else ''
            events.append(f'Hit {target["name"]} for {dealt}{roll_note}.')
            if monster_hp[target['id']] == 0:
                if target['id'] not in gs.setdefault('defeated_monsters', []):
                    gs['defeated_monsters'].append(target['id'])
                events.append(f'{target["name"]} defeated!')

    # --- Damage taken by the hero (player rolled the monster's attack) -----
    taken = move.get('damage_taken')
    if taken:
        ch['current_hp'] = max(0, ch['current_hp'] - taken)
        events.append(f'You took {taken} damage (HP {ch["current_hp"]}/{ch["max_hp"]}).')

    if ch['current_hp'] <= 0:
        gs['status'] = 'dead'
        events.append('You have fallen. The campaign ends here.')

    # --- Movement (only once the room is clear) ----------------------------
    chosen = (move.get('chosen_exit') or '').strip().lower() or None
    if gs['status'] == 'active' and chosen:
        if living_in(node, monster_hp):
            events.append('Enemies still block the way — clear the room before moving.')
        else:
            exits = {k.lower(): v for k, v in node.get('exits', {}).items()}
            dest = exits.get(chosen)
            if not dest or dest not in campaign['nodes']:
                events.append(f'There is no "{chosen}" exit from here.')
            else:
                gs['current_node'] = dest
                if dest not in gs.setdefault('discovered_nodes', []):
                    gs['discovered_nodes'].append(dest)
                dest_node = campaign['nodes'][dest]
                ensure_monsters(dest_node, monster_hp)
                events.append(f'You go {chosen} to {dest_node["title"]}.')

    # --- Victory: goal node reached and cleared ----------------------------
    cur = campaign['nodes'][gs['current_node']]
    if gs['status'] == 'active' and cur.get('is_goal') and not living_in(cur, monster_hp):
        gs['status'] = 'won'
        events.append(f'You have cleared {cur["title"]}. Victory!')

    # --- Bookkeeping -------------------------------------------------------
    gs['turn_count'] = gs.get('turn_count', 0) + 1
    ing['last_processed_message_id'] = message_id
    ing['awaiting_reply'] = False
    ing.setdefault('turn_log', []).append({
        'turn': gs['turn_count'],
        'read': move.get('actions_summary', ''),
        'result': ' '.join(events) if events else 'No effect.',
        'confidence': move.get('confidence'),
        'misread_flag': bool(move.get('misread_flag')),
    })
    return events


def main():
    argv = sys.argv[1:]
    if not argv:
        print('Usage: python engine.py <move.json> [--message-id <id>]', file=sys.stderr)
        sys.exit(1)
    move_path = argv[0]
    message_id = None
    if '--message-id' in argv:
        message_id = argv[argv.index('--message-id') + 1]

    move = load(move_path)
    if message_id is None:
        message_id = move.get('message_id')

    campaign_path = path_for('campaign.json', 'DND_CAMPAIGN_FILE')
    state_path = path_for('state.json', 'DND_STATE_FILE')
    campaign = load(campaign_path)
    state = load(state_path)

    events = apply_move(state, campaign, move, message_id)

    with open(state_path, 'w', encoding='utf-8') as f:
        json.dump(state, f, indent=2)
        f.write('\n')

    print(json.dumps({
        'status': state['game_state']['status'],
        'current_node': state['game_state']['current_node'],
        'current_hp': state['character']['current_hp'],
        'turn_count': state['game_state']['turn_count'],
        'events': events,
    }, indent=2))


if __name__ == '__main__':
    main()
