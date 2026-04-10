"""
engine/game_manager.py - v3
----------------------------
Randomises:
  1. Roles across player names
  2. Model from pool for each player
  3. Archetype from pool for each player

Role + model + archetype are all independently random.
The same player name gets a different combination every game.
"""

import random
import sys
from dataclasses import dataclass

from config.model_registry import AVAILABLE_MODELS, ModelConfig, make_client
from engine.game_state import GameState, PlayerState
from prompts.archetypes import ALL_ARCHETYPES, VILLAGER_ARCHETYPES
from prompts.personalities import ALL_PERSONALITIES, DEMO_PERSONALITIES
from agents.narrator  import NarratorAgent
from agents.mafia     import MafiaAgent
from agents.detective import DetectiveAgent
from agents.doctor    import DoctorAgent
from agents.villager  import VillagerAgent
from agents.memory    import GameMemoryStore


PLAYER_NAMES = [
    "Alice", "Bob", "Charlie", "Diana", "Eve", "Frank",
    "Grace", "Hank", "Ivy", "Jack", "Kate",
]

ROLE_DISTRIBUTION = [
    "Mafia", "Mafia",
    "Detective",
    "Doctor",
    "Villager", "Villager", "Villager", "Villager",
    "Villager", "Villager", "Villager",
]

assert len(PLAYER_NAMES) == len(ROLE_DISTRIBUTION)


@dataclass
class GameSetup:
    game_state:   GameState
    narrator:     NarratorAgent
    mafia_agents: list[MafiaAgent]
    detective:    DetectiveAgent
    doctor:       DoctorAgent
    villagers:    list[VillagerAgent]
    assignments:  list[dict]
    memory_store: GameMemoryStore = None


def _pick_archetype(role: str) -> str:
    if role == "Villager":
        return random.choice(VILLAGER_ARCHETYPES)
    return random.choice(ALL_ARCHETYPES)


def _pick_personality(demo: bool = False) -> str:
    pool = DEMO_PERSONALITIES if demo else ALL_PERSONALITIES
    return random.choice(pool)


# ------------------------------------------------------------------ #
#  Role-Personality exclusion table                                    #
# ------------------------------------------------------------------ #
# Strategic roles must not be "lobotomised" by performance-first
# personalities that undermine their core mechanics.
PERSONALITY_EXCLUSIONS: dict[str, list[str]] = {
    "Detective": ["TheParasite", "ThePerformer"],
    "Doctor":    ["TheParasite", "ThePerformer"],
}

# ------------------------------------------------------------------ #
#  Archetype-Personality exclusion table                               #
# ------------------------------------------------------------------ #
# Some archetype-personality combinations reinforce the same tendency
# in both layers, producing agents with no internal contrast and no
# interesting failure mode.  These are banned from co-assignment.
ARCHETYPE_PERSONALITY_EXCLUSIONS: dict[str, list[str]] = {
    "Passive":       ["MythBuilder", "TheGhost"],
    "Overconfident": ["TheParasite"],
    "Stubborn":      ["MythBuilder"],
    "Diplomatic":    ["TheConfessor"],
}

# No personality may appear more than this many times per game.
_PERSONALITY_FREQUENCY_CAP = 2


def _pick_personality_constrained(
    role: str,
    current_counts: dict[str, int],
    demo: bool = False,
    archetype: str = "",
) -> str:
    """
    Pick a personality that respects:
      1. The role-personality exclusion table.
      2. The archetype-personality exclusion table.
      3. The per-game frequency cap.

    Raises ValueError if no valid personality remains (should never
    happen with a reasonable pool / player count).
    """
    pool = list(DEMO_PERSONALITIES if demo else ALL_PERSONALITIES)

    excluded = set(PERSONALITY_EXCLUSIONS.get(role, []))
    excluded |= set(ARCHETYPE_PERSONALITY_EXCLUSIONS.get(archetype, []))
    eligible = [
        p for p in pool
        if p not in excluded
        and current_counts.get(p, 0) < _PERSONALITY_FREQUENCY_CAP
    ]

    if not eligible:
        raise ValueError(
            f"No valid personality for role={role} archetype={archetype} "
            f"with current counts {current_counts}. "
            f"Exclusions={excluded}, cap={_PERSONALITY_FREQUENCY_CAP}"
        )

    return random.choice(eligible)


def create_game(narrator_model: ModelConfig | None = None, demo: bool = False) -> GameSetup:
    # 0. Load persistent cross-game memory
    memory_store = GameMemoryStore()
    memory_store.load()

    # 1. Shuffle roles
    roles  = list(ROLE_DISTRIBUTION)
    names  = list(PLAYER_NAMES)
    random.shuffle(roles)
    role_map: dict[str, str] = dict(zip(names, roles))

    # 2. Assign random model per player
    model_map: dict[str, ModelConfig] = {
        name: random.choice(AVAILABLE_MODELS) for name in names
    }
    narrator_cfg = narrator_model or random.choice(AVAILABLE_MODELS)

    # 3. Assign random archetype per player (role-appropriate)
    archetype_map: dict[str, str] = {
        name: _pick_archetype(role_map[name]) for name in names
    }

    # Soft warning: 3+ Analytical players → convergent reasoning risk
    analytical_count = sum(1 for a in archetype_map.values() if a == "Analytical")
    if analytical_count >= 3:
        print(
            f"  [⚠] {analytical_count} players assigned Analytical archetype — "
            f"this may produce convergent reasoning and low variance. "
            f"Consider re-rolling.",
            file=sys.stderr,
        )

    # 3b. Assign random personality per player (with exclusion + frequency cap)
    personality_map: dict[str, str] = {}
    personality_counts: dict[str, int] = {}
    for name in names:
        p = _pick_personality_constrained(
            role_map[name], personality_counts, demo,
            archetype=archetype_map[name],
        )
        personality_map[name] = p
        personality_counts[p] = personality_counts.get(p, 0) + 1

    # 4. Build assignment table
    assignments = [
        {
            "name":        name,
            "role":        role_map[name],
            "model":       model_map[name].name,
            "short":       model_map[name].short,
            "archetype":   archetype_map[name],
            "personality": personality_map[name],
        }
        for name in names
    ]

    # 5. Build clients
    clients     = {name: make_client(model_map[name]) for name in names}
    narrator_cl = make_client(narrator_cfg)

    # 6. Identify role assignments
    mafia_names    = [n for n, r in role_map.items() if r == "Mafia"]
    assert len(mafia_names) == 2
    m1, m2         = mafia_names
    detective_name = next(n for n, r in role_map.items() if r == "Detective")
    doctor_name    = next(n for n, r in role_map.items() if r == "Doctor")
    villager_names = [n for n, r in role_map.items() if r == "Villager"]

    # 7. Instantiate agents
    narrator  = NarratorAgent(narrator_cl)
    mafia     = [
        MafiaAgent(m1, m2, archetype_map[m1], personality_map[m1], clients[m1]),
        MafiaAgent(m2, m1, archetype_map[m2], personality_map[m2], clients[m2]),
    ]
    detective = DetectiveAgent(detective_name, archetype_map[detective_name], personality_map[detective_name], clients[detective_name])
    doctor    = DoctorAgent(doctor_name, archetype_map[doctor_name], personality_map[doctor_name], clients[doctor_name])
    villagers = [VillagerAgent(n, archetype_map[n], personality_map[n], clients[n]) for n in villager_names]

    # 8. Build GameState (archetype on each PlayerState)
    game_state = GameState(
        players={
            name: PlayerState(
                name=name,
                role=role_map[name],
                archetype=archetype_map[name],
                personality=personality_map[name],
            )
            for name in names
        }
    )

    return GameSetup(
        game_state=game_state,
        narrator=narrator,
        mafia_agents=mafia,
        detective=detective,
        doctor=doctor,
        villagers=villagers,
        assignments=assignments,
        memory_store=memory_store,
    )


def print_assignments(setup: GameSetup, reveal_roles: bool = False) -> None:
    from engine.game_log import (
        print_model_archetype_table,
        BOLD, CYAN, RED, YELLOW, GREEN, BLUE, RESET,
    )

    ROLE_C = {"Mafia": RED, "Detective": YELLOW, "Doctor": GREEN, "Villager": BLUE}

    if reveal_roles:
        print(f"\n{BOLD}{CYAN}Full Assignments (DEBUG):{RESET}")
        print(f"  {'Player':10} {'Model':20} {'Archetype':14} {'Personality':14} {'Role'}")
        print(f"  {'------':10} {'-----':20} {'---------':14} {'-----------':14} {'----'}")
        for a in setup.assignments:
            c = ROLE_C.get(a["role"], "")
            print(f"  {a['name']:10} {a['model']:20} {a['archetype']:14} {a.get('personality',''):14} {c}{a['role']}{RESET}")
        print()
    else:
        print_model_archetype_table(setup.assignments)
