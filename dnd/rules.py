#!/usr/bin/env python3
"""Shared 5e rules data for the solo-D&D engine: class profiles and the
derivation from a rolled character sheet to a full character block.

Imported by engine.py (apply a filled sheet) and render_dnd.py (the dice
reminder + character sheet). No magic classes — martial only for now.
"""

ABILITIES = ['strength', 'dexterity', 'constitution',
             'intelligence', 'wisdom', 'charisma']
ABBR = {'strength': 'STR', 'dexterity': 'DEX', 'constitution': 'CON',
        'intelligence': 'INT', 'wisdom': 'WIS', 'charisma': 'CHA'}

# Level-1 martial classes. ac is a callable(mods) so unarmored/finesse classes
# can fold in DEX/CON; armor is the human-readable description.
CLASS_PROFILES = {
    'fighter': {
        'hit_die': 10,
        'attack_ability': 'strength',
        'weapon': 'Longsword',
        'damage_die': '1d8',
        'armor': 'Chain mail + shield',
        'ac': lambda m: 18,
        'inventory': ['Longsword', 'Shield', 'Chain mail', "Explorer's pack",
                      'Health Potion x1'],
        'skills': [],
        'blurb': 'd10 HP, chain mail + shield (AC 18), Longsword. Tough and reliable.',
    },
    'rogue': {
        'hit_die': 8,
        'attack_ability': 'dexterity',
        'weapon': 'Shortsword',
        'damage_die': '1d6',
        'armor': 'Leather armor',
        'ac': lambda m: 11 + m.get('dexterity', 0),
        'inventory': ['Shortsword', 'Shortbow', 'Leather armor', "Thieves' tools",
                      'Health Potion x1'],
        'skills': ['Stealth'],
        'blurb': 'd8 HP, leather (AC 11+DEX), Shortsword + bow. Stealth expert (sneaks better).',
    },
    'barbarian': {
        'hit_die': 12,
        'attack_ability': 'strength',
        'weapon': 'Greataxe',
        'damage_die': '1d12',
        'armor': 'Unarmored defense',
        'ac': lambda m: 10 + m.get('dexterity', 0) + m.get('constitution', 0),
        'inventory': ['Greataxe', 'Handaxe x2', "Explorer's pack", 'Health Potion x1'],
        'skills': [],
        'blurb': 'd12 HP, unarmored (AC 10+DEX+CON), Greataxe. Big hits, big health.',
    },
}
DEFAULT_CLASS = 'fighter'


def ability_mod(score):
    try:
        return (int(score) - 10) // 2
    except (TypeError, ValueError):
        return 0


def proficiency_bonus(level):
    try:
        return 2 + max(0, (int(level) - 1) // 4)
    except (TypeError, ValueError):
        return 2


def normalize_class(name):
    key = (name or '').strip().lower()
    return key if key in CLASS_PROFILES else DEFAULT_CLASS


def derive_character(name, klass, scores):
    """Turn a rolled sheet (name, class, six ability scores) into a full
    level-1 character block. Missing/garbled scores default to 10."""
    klass = normalize_class(klass)
    prof = CLASS_PROFILES[klass]

    clean = {}
    for a in ABILITIES:
        try:
            v = int(scores.get(a)) if scores and scores.get(a) is not None else 10
        except (TypeError, ValueError):
            v = 10
        clean[a] = max(1, min(20, v))  # clamp to a sane 1..20

    mods = {a: ability_mod(clean[a]) for a in ABILITIES}
    max_hp = max(1, prof['hit_die'] + mods['constitution'])

    return {
        'name': (name or 'Hero').strip() or 'Hero',
        'class': klass.capitalize(),
        'level': 1,
        'max_hp': max_hp,
        'current_hp': max_hp,
        'ac': prof['ac'](mods),
        'attack_ability': prof['attack_ability'],
        'weapon': prof['weapon'],
        'damage_die': prof['damage_die'],
        'inventory': list(prof['inventory']),
        'modifiers': mods,
        'scores': clean,
        'skill_proficiencies': list(prof['skills']),
    }
