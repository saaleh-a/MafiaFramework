"""
agents/memory.py
-----------------
Persistent cross-game memory for Mafia agents.

Each agent accumulates "learnings" across games — patterns they noticed,
strategies that worked or failed, reads that were correct or wrong.
These are stored as JSON on disk and loaded at the start of each new game.

This gives agents genuine cross-game improvement: a Detective who learned
that "quiet players in round 2 are often mafia" can carry that forward.

The memory is role-aware: learnings are stored per-role (not per-name),
so a Detective's investigation insights are available to the next
Detective regardless of which player name they get.

Storage format:
  memory/
    detective_learnings.json
    doctor_learnings.json
    mafia_learnings.json
    villager_learnings.json
    global_patterns.json

Each file contains a list of learning entries with timestamps.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path

# Default storage directory (relative to project root)
_MEMORY_DIR = Path(os.environ.get("MAFIA_MEMORY_DIR", "memory"))

# Maximum learnings per role to prevent unbounded growth
_MAX_LEARNINGS_PER_ROLE = 50


@dataclass
class Learning:
    """A single cross-game learning entry."""
    insight: str           # What was learned
    context: str           # What situation produced it
    role: str              # Which role learned it
    round_number: int      # When in the game
    outcome: str           # "correct" | "incorrect" | "unknown"
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


class GameMemoryStore:
    """
    Persistent memory store for cross-game learnings.

    Usage:
        store = GameMemoryStore()
        store.load()

        # During game: record learnings
        store.add_learning(Learning(
            insight="Quiet players who survive multiple rounds are often lynch-bait targets",
            context="Day 3 discussion",
            role="Villager",
            round_number=3,
            outcome="correct",
        ))

        # Before prompting: get relevant context
        prefix = store.get_memory_prefix("Detective")

        # After game: save
        store.save()
    """

    def __init__(self, memory_dir: Path | str | None = None) -> None:
        self.memory_dir = Path(memory_dir) if memory_dir else _MEMORY_DIR
        self._learnings: dict[str, list[dict]] = {
            "Detective": [],
            "Doctor": [],
            "Mafia": [],
            "Villager": [],
            "global": [],
        }

    def load(self) -> None:
        """Load learnings from disk. No-op if directory doesn't exist."""
        if not self.memory_dir.exists():
            return
        for role_key in self._learnings:
            filename = f"{role_key.lower()}_learnings.json"
            filepath = self.memory_dir / filename
            if filepath.exists():
                try:
                    with open(filepath) as f:
                        data = json.load(f)
                    if isinstance(data, list):
                        self._learnings[role_key] = data[-_MAX_LEARNINGS_PER_ROLE:]
                except (json.JSONDecodeError, OSError):
                    pass  # Corrupted file — start fresh

    def save(self) -> None:
        """Persist learnings to disk."""
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        for role_key, learnings in self._learnings.items():
            filename = f"{role_key.lower()}_learnings.json"
            filepath = self.memory_dir / filename
            # Keep only the most recent entries
            trimmed = learnings[-_MAX_LEARNINGS_PER_ROLE:]
            with open(filepath, "w") as f:
                json.dump(trimmed, f, indent=2)

    def add_learning(self, learning: Learning) -> None:
        """Record a new learning for a specific role."""
        entry = asdict(learning)
        role_key = learning.role if learning.role in self._learnings else "global"
        self._learnings[role_key].append(entry)
        # Also add to global if role-specific
        if role_key != "global":
            self._learnings["global"].append(entry)

    def get_memory_prefix(self, role: str) -> str:
        """
        Build a prompt prefix with relevant learnings for a role.
        Returns empty string if no learnings exist.
        """
        role_learnings = self._learnings.get(role, [])
        global_learnings = self._learnings.get("global", [])

        # Combine role-specific (priority) + recent global
        relevant = role_learnings[-5:] + [
            g for g in global_learnings[-5:]
            if g not in role_learnings[-5:]
        ]

        if not relevant:
            return ""

        lines = [
            "CROSS-GAME MEMORY (patterns from previous games — use these "
            "to inform your reasoning but do not cite them as evidence from "
            "THIS game's discussion):"
        ]
        for entry in relevant[-8:]:  # Cap at 8 entries
            insight = entry.get("insight", "")
            outcome = entry.get("outcome", "unknown")
            marker = "✓" if outcome == "correct" else "✗" if outcome == "incorrect" else "?"
            lines.append(f"  {marker} {insight}")
        lines.append("")
        return "\n".join(lines)

    def record_game_outcome(
        self,
        winner: str,
        role_assignments: list[dict],
        round_count: int,
    ) -> None:
        """
        Record high-level game outcome learnings automatically.
        Called at the end of each game.
        """
        self.add_learning(Learning(
            insight=f"Game lasted {round_count} rounds. {winner} won.",
            context="game_outcome",
            role="global",
            round_number=round_count,
            outcome="correct" if winner == "Town" else "incorrect",
        ))

        # Record which roles survived
        for assignment in role_assignments:
            # role_assignments come from GameSetup.assignments
            role = assignment.get("role", "")
            name = assignment.get("name", "")
            if role and name:
                self.add_learning(Learning(
                    insight=f"{name} ({role}) — game ended round {round_count}, {winner} won",
                    context="game_outcome",
                    role=role,
                    round_number=round_count,
                    outcome="correct" if (
                        (winner == "Town" and role != "Mafia")
                        or (winner == "Mafia" and role == "Mafia")
                    ) else "incorrect",
                ))
