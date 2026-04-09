"""
agents/summary.py
-----------------
SummaryAgent that provides low-cognitive-load narrative summaries.

Consumes the last 50 messages from the game log and outputs a bulleted
"Current Narrative" covering:
  - Who is the current target
  - What is the main evidence
  - What is the required action

Designed for accessibility: minimal cognitive overhead, clear structure,
no ambiguity about what the player needs to do next.
"""

from __future__ import annotations

from engine.game_state import GameState, LogEntry, GamePhase


# Maximum messages to consider for summary
MAX_MESSAGES = 50


class SummaryAgent:
    """
    Generates concise narrative summaries from game state and log.

    No LLM calls — this is a deterministic summariser that extracts
    key information from structured game data. Fast, reliable, and
    zero-latency.
    """

    def generate_summary(self, game_state: GameState) -> str:
        """
        Build a bulleted "Current Narrative" from the game state.

        Returns a formatted string suitable for display to the user.
        """
        sections = [
            self._header(game_state),
            self._current_target(game_state),
            self._main_evidence(game_state),
            self._required_action(game_state),
            self._alive_status(game_state),
        ]
        return "\n".join(s for s in sections if s)

    def _header(self, gs: GameState) -> str:
        phase_name = gs.phase.value
        return (
            f"📋 CURRENT NARRATIVE — Round {gs.round_number} | {phase_name}\n"
            f"{'─' * 50}"
        )

    def _current_target(self, gs: GameState) -> str:
        """Identify who the group is focused on."""
        recent = self._get_recent_entries(gs)

        # Count name mentions in recent actions to find the most-discussed player
        mention_counts: dict[str, int] = {}
        alive = set(gs.get_alive_players())
        for entry in recent:
            if entry.agent_name == "Narrator":
                continue
            text = entry.action.lower()
            for name in alive:
                if name.lower() in text and name != entry.agent_name:
                    mention_counts[name] = mention_counts.get(name, 0) + 1

        if not mention_counts:
            return "• TARGET: No clear target yet — discussion is still open."

        top_target = max(mention_counts, key=lambda k: mention_counts[k])
        count = mention_counts[top_target]
        return f"• TARGET: {top_target} (mentioned by {count} player{'s' if count != 1 else ''})"

    def _main_evidence(self, gs: GameState) -> str:
        """Extract the key evidence or reasoning from recent discussion."""
        recent = self._get_recent_entries(gs)

        # Find the most substantive accusation-related messages
        evidence_lines = []
        for entry in recent:
            if entry.agent_name == "Narrator":
                continue
            action = entry.action.strip()
            # Look for messages that contain reasoning about suspicions
            lower = action.lower()
            if any(word in lower for word in [
                "vote", "suspect", "suspicious", "mafia",
                "trust", "lying", "defend", "accuse",
            ]):
                # Truncate long messages
                summary = action[:120] + "..." if len(action) > 120 else action
                evidence_lines.append(f"  — {entry.agent_name}: \"{summary}\"")

        if not evidence_lines:
            return "• EVIDENCE: No strong accusations yet."

        # Show top 3 most recent evidence items
        shown = evidence_lines[-3:]
        return "• KEY STATEMENTS:\n" + "\n".join(shown)

    def _required_action(self, gs: GameState) -> str:
        """Tell the user what needs to happen next."""
        if gs.phase == GamePhase.DAY_DISCUSSION:
            return "• ACTION NEEDED: Listen to discussion. A vote is coming."
        elif gs.phase == GamePhase.DAY_VOTE:
            return "• ACTION NEEDED: Players are voting. Watch for the result."
        elif gs.phase == GamePhase.NIGHT:
            return "• ACTION NEEDED: Night phase. Mafia, Detective, and Doctor act."
        else:
            return "• ACTION NEEDED: Game is over."

    def _alive_status(self, gs: GameState) -> str:
        """Quick alive/dead status."""
        alive = gs.get_alive_players()
        dead = [
            f"{n} ({p.role})"
            for n, p in gs.players.items()
            if not p.is_alive
        ]

        lines = [f"• ALIVE ({len(alive)}): {', '.join(alive)}"]
        if dead:
            lines.append(f"• ELIMINATED: {', '.join(dead)}")
        return "\n".join(lines)

    def _get_recent_entries(self, gs: GameState) -> list[LogEntry]:
        """Get the last MAX_MESSAGES log entries."""
        return gs.game_log[-MAX_MESSAGES:]
