"""
main.py - Mafia v3
-------------------
Run:
  python main.py
  python main.py --reveal-roles        # show all assignments at start
  python main.py --debug               # full reasoning, no truncation
  python main.py --quiet               # action lines only
  python main.py --seed 42             # reproducible game
  python main.py --games 5             # run N games, print win stats

Prerequisites:
  pip install -r requirements.txt
  az login
  cp .env.example .env && edit .env
"""

import asyncio
import argparse
import random
import sys

from engine.game_manager  import create_game, print_assignments
from engine.orchestrator  import MafiaGameOrchestrator
from engine.game_log      import print_game_banner, BOLD, RESET


async def run_one_game(debug: bool, quiet: bool, reveal_roles: bool) -> str:
    setup = create_game()
    print_game_banner(setup.game_state.players)
    print_assignments(setup, reveal_roles=reveal_roles)

    orchestrator = MafiaGameOrchestrator(
        game_state=setup.game_state,
        narrator=setup.narrator,
        mafia_agents=setup.mafia_agents,
        detective=setup.detective,
        doctor=setup.doctor,
        villagers=setup.villagers,
        debug=debug,
        quiet=quiet,
    )
    return await orchestrator.run_game()


async def main(
    debug:        bool = False,
    quiet:        bool = False,
    reveal_roles: bool = False,
    games:        int  = 1,
    seed:         int | None = None,
) -> None:
    if seed is not None:
        random.seed(seed)
        print(f"Seed: {seed}")

    results: dict[str, int] = {}

    for i in range(games):
        if games > 1:
            print(f"\n{BOLD}{'='*52}\n  GAME {i + 1} of {games}\n{'='*52}{RESET}")
        try:
            winner = await run_one_game(debug, quiet, reveal_roles)
            results[winner] = results.get(winner, 0) + 1
        except KeyboardInterrupt:
            print("\n[Interrupted]")
            sys.exit(0)

    if games > 1:
        print(f"\n{BOLD}Results across {games} games:{RESET}")
        for faction, wins in sorted(results.items()):
            pct = (wins / games) * 100
            print(f"  {faction}: {wins}/{games} ({pct:.0f}%)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Mafia v3")
    parser.add_argument("--debug",        action="store_true")
    parser.add_argument("--quiet",        action="store_true")
    parser.add_argument("--reveal-roles", action="store_true")
    parser.add_argument("--games",        type=int, default=1)
    parser.add_argument("--seed",         type=int, default=None)
    args = parser.parse_args()

    asyncio.run(main(
        debug=args.debug,
        quiet=args.quiet,
        reveal_roles=args.reveal_roles,
        games=args.games,
        seed=args.seed,
    ))
