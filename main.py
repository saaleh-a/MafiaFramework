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

from config.model_registry import validate_environment
from engine.game_manager  import create_game, print_assignments
from engine.orchestrator  import MafiaGameOrchestrator
from engine.game_log      import print_game_banner, BOLD, RESET, RED


def _configure_console_encoding() -> None:
    """
    Prefer UTF-8 output on Windows so box-drawing characters do not crash.

    If the host stream does not support reconfigure(), or refuses the change,
    we leave it alone and rely on the platform default.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            continue


async def run_one_game(debug: bool, quiet: bool, reveal_roles: bool, demo: bool = False) -> str:
    setup = create_game(demo=demo)
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
        memory_store=setup.memory_store,
        assignments=setup.assignments,
    )
    return await orchestrator.run_game()


async def main(
    debug:        bool = False,
    quiet:        bool = False,
    reveal_roles: bool = False,
    games:        int  = 1,
    seed:         int | None = None,
    demo:         bool = False,
) -> None:
    _configure_console_encoding()

    # Validate environment before starting
    issues = validate_environment()
    for issue in issues:
        print(f"{RED}{BOLD}WARNING:{RESET} {issue}", file=sys.stderr)
    if any("not set" in i and "FOUNDRY_PROJECT_ENDPOINT" in i for i in issues):
        print(
            f"\n{RED}Cannot start without FOUNDRY_PROJECT_ENDPOINT. "
            f"See README.md for setup instructions.{RESET}",
            file=sys.stderr,
        )
        sys.exit(1)

    if seed is not None:
        random.seed(seed)
        print(f"Seed: {seed}")

    results: dict[str, int] = {}

    for i in range(games):
        if games > 1:
            print(f"\n{BOLD}{'='*52}\n  GAME {i + 1} of {games}\n{'='*52}{RESET}")
        try:
            winner = await run_one_game(debug, quiet, reveal_roles, demo)
            results[winner] = results.get(winner, 0) + 1
        except KeyboardInterrupt:
            print("\n[Interrupted]")
            sys.exit(0)
        except Exception as exc:
            msg = str(exc)
            if "DeploymentNotFound" in msg or "does not exist" in msg:
                # Error details already printed by agents/base.py handler
                sys.exit(1)
            raise

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
    parser.add_argument("--demo",         action="store_true",
                        help="Restrict personalities to demo-safe subset")
    args = parser.parse_args()

    asyncio.run(main(
        debug=args.debug,
        quiet=args.quiet,
        reveal_roles=args.reveal_roles,
        games=args.games,
        seed=args.seed,
        demo=args.demo,
    ))
