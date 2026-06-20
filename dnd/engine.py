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
import re
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def path_for(name, env_override):
    return os.environ.get(env_override) or os.path.join(SCRIPT_DIR, name)


def consume_potion(inventory, idx):
    """Decrement an 'xN' potion count in place, dropping the entry at zero."""
    item = inventory[idx]
    m = re.search(r'x\s*(\d+)', item, re.I)
    if m:
        n = int(m.group(1)) - 1
        if n <= 0:
            inventory.pop(idx)
        else:
            inventory[idx] = re.sub(r'x\s*\d+', f'x{n}', item, flags=re.I)
    else:
        inventory.pop(idx)


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


def monster_passive_perception(m):
    return m.get('passive_perception', 10)


def monster_initiative(m):
    # Deterministic "take 10" initiative for the monster (no engine dice).
    return 10 + m.get('dex_mod', 0)


def _move(gs, campaign, monster_hp, direction, events, verb):
    """Move the player along an exit, discovering + seeding the destination and
    resetting the per-encounter combat tracker. Returns True on success."""
    exits = {k.lower(): v for k, v in campaign['nodes'][gs['current_node']].get('exits', {}).items()}
    dest = exits.get(direction)
    if not dest or dest not in campaign['nodes']:
        events.append(f'There is no "{direction}" exit from here.')
        return False
    gs['current_node'] = dest
    if dest not in gs.setdefault('discovered_nodes', []):
        gs['discovered_nodes'].append(dest)
    dest_node = campaign['nodes'][dest]
    ensure_monsters(dest_node, monster_hp)
    gs['combat'] = {'node': dest, 'player_first': None}
    events.append(f'{verb} {direction} to {dest_node["title"]}.')
    return True


def apply_move(state, campaign, move, message_id):
    gs = state['game_state']
    ing = state.setdefault('ingestion', {})
    ch = state['character']
    events = []

    checkboxes = [str(c).lower() for c in (move.get('checkboxes_marked') or [])]

    def marked(substr):
        return any(substr in c for c in checkboxes)

    monster_hp = gs.setdefault('monster_hp', {})
    gs.setdefault('status', 'active')
    node = campaign['nodes'][gs['current_node']]
    ensure_monsters(node, monster_hp)

    if move.get('misread_flag'):
        events.append('You flagged the previous read as a misread.')

    living = living_in(node, monster_hp)
    target = living[0] if living else None
    chosen = (move.get('chosen_exit') or '').strip().lower() or None

    sneak = bool(target) and (marked('sneak') or marked('stealth'))
    flee = bool(target) and (marked('flee') or marked('retreat'))
    dodge = bool(target) and marked('dodge')

    to_hit = find_roll(move, 'hit')
    damage = move.get('damage_dealt')
    if damage is None:
        damage = find_roll(move, 'damage')
    attack = bool(target) and not (sneak or flee or dodge) and (
        damage is not None or to_hit is not None or marked('attack')
    )

    def apply_damage_taken(note=''):
        taken = move.get('damage_taken')
        if taken:
            ch['current_hp'] = max(0, ch['current_hp'] - taken)
            suffix = f' {note}' if note else ''
            events.append(
                f'You took {taken} damage{suffix} (HP {ch["current_hp"]}/{ch["max_hp"]}).')

    # --- Initiative (add-on, resolved once per encounter) ------------------
    combat = gs.setdefault('combat', {'node': None, 'player_first': None})
    if combat.get('node') != gs['current_node']:
        combat = {'node': gs['current_node'], 'player_first': None}
        gs['combat'] = combat
    init_roll = move.get('initiative_roll')
    if target and init_roll is not None and combat.get('player_first') is None:
        mon_init = max(monster_initiative(m) for m in living)
        combat['player_first'] = init_roll >= mon_init
        verdict = 'you act first!' if combat['player_first'] else 'the enemy acts first.'
        events.append(f'Initiative: {init_roll} vs {mon_init} — {verdict}')

    # --- Primary action: sneak > flee > dodge > attack ---------------------
    sneak_success = False
    if sneak:
        stealth = move.get('stealth_roll')
        dc = max(monster_passive_perception(m) for m in living)
        if stealth is None:
            events.append('You try to sneak, but no Stealth roll was read — '
                          'write your total in the Stealth box.')
        elif stealth >= dc:
            sneak_success = True
            events.append(f'You move unseen (Stealth {stealth} vs passive Perception {dc}).')
        else:
            events.append(f'You are spotted! (Stealth {stealth} vs passive Perception {dc})')
            apply_damage_taken('as you are caught')
    elif flee:
        apply_damage_taken('as you turn to run')  # leaving melee provokes an attack
    elif dodge:
        events.append('You take the Dodge action — the enemy attacks at disadvantage.')
        apply_damage_taken()
    elif attack:
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
        # The monster swings back, unless you won initiative and cleared the room.
        if combat.get('player_first') and not living_in(node, monster_hp):
            events.append('You struck first and dropped it before it could swing — no damage taken.')
        else:
            apply_damage_taken()
    else:
        apply_damage_taken()

    # --- Drink a potion (player rolls 2d4+2 and writes the total) ----------
    if marked('potion'):
        inv = ch.setdefault('inventory', [])
        idx = next((i for i, it in enumerate(inv) if 'potion' in str(it).lower()), None)
        heal = move.get('heal_amount')
        if idx is None:
            events.append('You reach for a potion, but have none left.')
        elif heal is None:
            events.append('You ready a potion, but no healing roll was read — '
                          'write your 2d4+2 total in the Heal box.')
        else:
            before = ch['current_hp']
            new_hp = before + heal
            if ch.get('max_hp') is not None:
                new_hp = min(ch['max_hp'], new_hp)
            ch['current_hp'] = new_hp
            consume_potion(inv, idx)
            events.append(f'You drink a potion and recover {new_hp - before} HP '
                          f'(now {new_hp}/{ch.get("max_hp", "?")}).')

    # --- Search the room for loot ------------------------------------------
    if marked('search'):
        searched = gs.setdefault('searched_nodes', [])
        loot = node.get('loot', [])
        if gs['current_node'] in searched:
            events.append('You search again, but find nothing new.')
        elif loot:
            ch.setdefault('inventory', []).extend(loot)
            searched.append(gs['current_node'])
            events.append('You search and find: ' + ', '.join(loot) + '.')
        else:
            searched.append(gs['current_node'])
            events.append('You search, but find nothing of value.')

    # --- Death check (after any healing applied this turn) -----------------
    if ch['current_hp'] <= 0:
        gs['status'] = 'dead'
        events.append('You have fallen. The campaign ends here.')

    # --- Movement ----------------------------------------------------------
    if gs['status'] == 'active' and chosen:
        if sneak_success:
            _move(gs, campaign, monster_hp, chosen, events, 'You slip')
        elif flee:
            _move(gs, campaign, monster_hp, chosen, events, 'You flee')
        elif living_in(node, monster_hp):
            if not (sneak or flee):
                events.append('Enemies still block the way — clear the room, or Sneak past / Flee.')
        else:
            _move(gs, campaign, monster_hp, chosen, events, 'You go')

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
