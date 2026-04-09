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
from dataclasses import dataclass

from config.model_registry import AVAILABLE_MODELS, ModelConfig, make_client
from engine.game_state import GameState, PlayerState
from prompts.archetypes import ALL_ARCHETYPES, VILLAGER_ARCHETYPES
from agents.narrator  import NarratorAgent
from agents.mafia     import MafiaAgent
from agents.detective import DetectiveAgent
from agents.doctor    import DoctorAgent
from agents.villager  import VillagerAgent


PLAYER_NAMES = ["Alice", "Bob", "Charlie", "Diana", "Eve", "Frank", "Grace"]

ROLE_DISTRIBUTION = [
    "Mafia", "Mafia",
    "Detective",
    "Doctor",
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


def _pick_archetype(role: str) -> str:
    if role == "Villager":
        return random.choice(VILLAGER_ARCHETYPES)
    return random.choice(ALL_ARCHETYPES)


def create_game(narrator_model: ModelConfig | None = None) -> GameSetup:
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

    # 4. Build assignment table
    assignments = [
        {
            "name":      name,
            "role":      role_map[name],
            "model":     model_map[name].name,
            "short":     model_map[name].short,
            "archetype": archetype_map[name],
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
        MafiaAgent(m1, m2, archetype_map[m1], clients[m1]),
        MafiaAgent(m2, m1, archetype_map[m2], clients[m2]),
    ]
    detective = DetectiveAgent(detective_name, archetype_map[detective_name], clients[detective_name])
    doctor    = DoctorAgent(doctor_name, archetype_map[doctor_name], clients[doctor_name])
    villagers = [VillagerAgent(n, archetype_map[n], clients[n]) for n in villager_names]

    # 8. Build GameState (archetype on each PlayerState)
    game_state = GameState(
        players={
            name: PlayerState(
                name=name,
                role=role_map[name],
                archetype=archetype_map[name],
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
    )


def print_assignments(setup: GameSetup, reveal_roles: bool = False) -> None:
    from engine.game_log import (
        print_model_archetype_table,
        BOLD, CYAN, RED, YELLOW, GREEN, BLUE, RESET,
    )

    ROLE_C = {"Mafia": RED, "Detective": YELLOW, "Doctor": GREEN, "Villager": BLUE}

    if reveal_roles:
        print(f"\n{BOLD}{CYAN}Full Assignments (DEBUG):{RESET}")
        print(f"  {'Player':10} {'Model':20} {'Archetype':14} {'Role'}")
        print(f"  {'------':10} {'-----':20} {'---------':14} {'----'}")
        for a in setup.assignments:
            c = ROLE_C.get(a["role"], "")
            print(f"  {a['name']:10} {a['model']:20} {a['archetype']:14} {c}{a['role']}{RESET}")
        print()
    else:
        print_model_archetype_table(setup.assignments)
