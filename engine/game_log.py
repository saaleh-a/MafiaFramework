"""
engine/game_log.py - v3
-----------------------
Terminal renderer. Shows archetype alongside role in every agent box.
Each archetype gets a slightly distinct display treatment where possible.
"""

RED    = "\033[91m"
YELLOW = "\033[93m"
GREEN  = "\033[92m"
BLUE   = "\033[94m"
CYAN   = "\033[96m"
WHITE  = "\033[97m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
ITALIC = "\033[3m"
RESET  = "\033[0m"

ROLE_COLOURS: dict[str, str] = {
    "Mafia":     RED,
    "Detective": YELLOW,
    "Doctor":    GREEN,
    "Villager":  BLUE,
    "Narrator":  WHITE + BOLD,
}

MAX_REASONING_CHARS = 500


def _colour(role: str) -> str:
    return ROLE_COLOURS.get(role, WHITE)


def print_game_banner(players: dict) -> None:
    print(f"\n{BOLD}{CYAN}")
    print("╔══════════════════════════════════════════════╗")
    print("║            MAFIA: THE AI GAME  v3           ║")
    print("║  Microsoft Agent Framework + Azure Foundry  ║")
    print("╚══════════════════════════════════════════════╝")
    print(f"{RESET}")
    print(f"{BOLD}Players:{RESET}")
    for name in players:
        print(f"  - {name}")
    print()


def print_model_archetype_table(assignments: list[dict]) -> None:
    print(f"{BOLD}Assignments (roles hidden):{RESET}")
    print(f"  {'Player':10} {'Model':20} {'Archetype'}")
    print(f"  {'------':10} {'-----':20} {'---------'}")
    for a in assignments:
        print(f"  {a['name']:10} {a['model']:20} {a.get('archetype', '?')}")
    print()


def print_phase_header(phase: str, round_number: int) -> None:
    label = f"  ROUND {round_number}: {phase}  "
    bar   = "=" * (len(label) + 2)
    print(f"\n{BOLD}{CYAN}{bar}\n={label}=\n{bar}{RESET}\n")


def print_agent_action(
    agent_name: str,
    role: str,
    archetype: str,
    reasoning: str | None,
    action: str,
    truncate: bool = True,
) -> None:
    colour  = _colour(role)
    header  = f" [{agent_name} | {role} | {archetype}] "
    width   = max(58, len(header) + 4)
    pad     = "─" * max(0, width - len(header) - 2)

    print(f"\n{colour}┌─{header}{pad}┐")

    if reasoning:
        display = (
            reasoning[:MAX_REASONING_CHARS] + f"{DIM}...{RESET}{colour}"
            if truncate and len(reasoning) > MAX_REASONING_CHARS
            else reasoning
        )
        print(f"│ {DIM}REASONING:{RESET}{colour}")
        for line in display.strip().splitlines():
            while len(line) > width - 4:
                print(f"│   {line[:width - 4]}")
                line = line[width - 4:]
            print(f"│   {line}")
        print(f"│")

    print(f"│ {BOLD}ACTION:{RESET}{colour}")
    for line in action.strip().splitlines():
        while len(line) > width - 4:
            print(f"│   {line[:width - 4]}")
            line = line[width - 4:]
        print(f"│   {line}")

    print(f"└{'─' * width}┘{RESET}")


def print_vote_tally(votes: dict[str, str], result: str | None) -> None:
    counts: dict[str, int] = {}
    for target in votes.values():
        counts[target] = counts.get(target, 0) + 1

    print(f"\n{BOLD}---- Vote Tally ----{RESET}")
    for voter, target in sorted(votes.items()):
        print(f"  {voter:10} -> {target}")

    print(f"\n{BOLD}---- Counts ----{RESET}")
    for name, count in sorted(counts.items(), key=lambda x: -x[1]):
        bar = "█" * count
        print(f"  {name:10} {bar} ({count})")

    if result:
        print(f"\n{RED}{BOLD}>>> ELIMINATED: {result} <<<{RESET}")
    else:
        print(f"\n{YELLOW}{BOLD}TIE - no elimination this round.{RESET}")


def print_night_result(
    killed: str | None,
    was_protected: bool,
    role: str | None = None,
) -> None:
    print(f"\n{BOLD}---- Dawn breaks... ----{RESET}")
    if was_protected:
        print(f"{YELLOW}Someone was targeted, but the Doctor intervened.{RESET}")
        print(f"{GREEN}Nobody was eliminated tonight.{RESET}")
    elif killed:
        role_str = f" ({role})" if role else ""
        print(f"{RED}{BOLD}ELIMINATED: {killed}{role_str}{RESET}")
    else:
        print(f"{GREEN}The night passes peacefully. Nobody eliminated.{RESET}")


def print_game_over(winner: str, game_state) -> None:
    colour = RED if winner == "Mafia" else GREEN
    print(f"\n{BOLD}{colour}")
    print("╔══════════════════════════════════════════════╗")
    winner_line = f"  GAME OVER - {winner.upper()} WINS!  "
    print(f"║{winner_line:^46}║")
    print(f"╚══════════════════════════════════════════════╝{RESET}")
    print(f"\n{BOLD}Final roles and archetypes:{RESET}")
    for name, player in game_state.players.items():
        status    = "SURVIVED" if player.is_alive else "ELIMINATED"
        c         = RED if player.role == "Mafia" else GREEN
        archetype = getattr(player, "archetype", "?")
        print(f"  {c}{name:10} {player.role:10} [{archetype:12}] {status}{RESET}")
    print(f"\n{BOLD}Game lasted {game_state.round_number} round(s).{RESET}\n")
