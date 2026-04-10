"""
agents/summary.py
------------------
SummaryAgent: produces a low-cognitive-load "Current Narrative"
summary at the start of every phase.

Consumes the last 50 messages from the game log and outputs a bulleted
summary covering:
  - Who is the current target
  - What is the main evidence
  - What is the required action

Designed for accessibility: keeps output concise, structured, and
scannable. No walls of text, no ambiguity about what happens next.
"""

from __future__ import annotations

import re
from engine.game_state import GameState, LogEntry, GamePhase


# Maximum messages to consume for summary
_MAX_MESSAGES = 50


class SummaryAgent:
    """
    Generates concise narrative summaries from game state and log.

    SECURITY: This agent must NEVER expose the ``role`` attribute of
    any living player.  Only ``is_revealed is True`` players (dead /
    eliminated) may have their role displayed.  All summary methods
    must respect this invariant.

    Usage:
        summary_agent = SummaryAgent()
        narrative = summary_agent.summarize(game_state)
        print(narrative)  # bulleted summary
    """

    def summarize(self, game_state: GameState) -> str:
        """
        Generate a bulleted "Current Narrative" from the game state.
        Returns a formatted string ready for display.
        """
        recent_entries = game_state.game_log[-_MAX_MESSAGES:]

        parts = [
            f"📋 CURRENT NARRATIVE — Round {game_state.round_number}",
            f"   Phase: {game_state.phase.value}",
            "",
        ]

        # Who is alive
        alive = game_state.get_alive_players()
        parts.append(f"• Players alive: {', '.join(alive)} ({len(alive)} remaining)")

        # Who died / was eliminated
        dead_info = self._get_recent_eliminations(game_state)
        if dead_info:
            parts.append(f"• Recent elimination: {dead_info}")

        # Current target / main suspect
        suspect_info = self._get_current_target(
            recent_entries, alive, game_state.round_number,
        )
        if suspect_info:
            parts.append(f"• Current target: {suspect_info}")
        else:
            parts.append("• Current target: No clear consensus yet")

        # Main evidence
        evidence = self._get_main_evidence(recent_entries)
        if evidence:
            parts.append(f"• Key evidence: {evidence}")

        # Vote summary if in voting phase
        if game_state.votes:
            vote_summary = self._get_vote_summary(game_state.votes)
            parts.append(f"• Votes so far: {vote_summary}")

        # Required action
        action = self._get_required_action(game_state)
        parts.append(f"• What happens next: {action}")

        parts.append("")
        return "\n".join(parts)

    def _get_recent_eliminations(self, game_state: GameState) -> str | None:
        """Get the most recent elimination info."""
        eliminated = [
            (n, p) for n, p in game_state.players.items()
            if not p.is_alive and p.is_revealed
        ]
        if not eliminated:
            return None
        # Return the last eliminated player
        name, player = eliminated[-1]
        return f"{name} ({player.role})"

    def _get_current_target(
        self, entries: list[LogEntry], alive: list[str],
        current_round: int = 1,
    ) -> str | None:
        """
        Identify who is being targeted most in recent discussion.

        Recency weighting: mentions in the current round carry full
        weight (1.0), the previous round carries 0.3, and anything
        older contributes 0.05.  This ensures the current_target field
        reflects what the room is doing *right now* rather than
        accumulating stale all-time counts.

        Ghost filtering: only count mentions of *alive* players.
        Dead players are excluded from the mention map so that
        discussion about a dead Mafia member doesn't surface as the
        "Current Target".
        """
        # Only track alive players — dead players are ghosts
        mention_scores: dict[str, float] = {name: 0.0 for name in alive}
        accusation_pattern = re.compile(
            r"\b(?:suspect|vote|accuse|suspicious|mafia|guilty)\b",
            re.IGNORECASE,
        )

        for entry in entries:
            if entry.agent_name == "Narrator":
                continue
            action = entry.action or ""
            if not accusation_pattern.search(action):
                continue

            # Recency weight based on round distance
            # Current round dominates; 2+ rounds ago barely registers
            round_delta = current_round - entry.round_number
            if round_delta <= 0:
                weight = 1.0
            elif round_delta == 1:
                weight = 0.3
            else:
                weight = 0.05

            for name in alive:
                if name in action and name != entry.agent_name:
                    mention_scores[name] += weight

        if not any(mention_scores.values()):
            return None

        top_target = max(mention_scores, key=lambda k: mention_scores[k])
        score = mention_scores[top_target]

        return f"{top_target} (weighted suspicion score: {score:.1f})"

    def _get_main_evidence(self, entries: list[LogEntry]) -> str | None:
        """Extract the strongest piece of evidence from recent discussion."""
        # Look for the most recent substantive accusation
        for entry in reversed(entries):
            if entry.agent_name == "Narrator":
                continue
            action = entry.action or ""
            # Look for evidence-bearing statements
            evidence_markers = [
                "voted", "didn't explain", "changed", "suspicious",
                "quiet", "loud", "defended", "attacked", "shifted",
                "pattern", "noticed", "round one", "round two",
            ]
            if any(marker in action.lower() for marker in evidence_markers):
                # Truncate to keep it scannable
                truncated = action[:120].strip()
                if len(action) > 120:
                    truncated += "..."
                return f'{entry.agent_name} said: "{truncated}"'

        return None

    def _get_vote_summary(self, votes: dict[str, str]) -> str:
        """Compact vote summary."""
        counts: dict[str, int] = {}
        for target in votes.values():
            counts[target] = counts.get(target, 0) + 1
        parts = [f"{name}({c})" for name, c in sorted(counts.items(), key=lambda x: -x[1])]
        return ", ".join(parts)

    def _get_required_action(self, game_state: GameState) -> str:
        """What does the player need to do right now?"""
        phase = game_state.phase
        if phase == GamePhase.DAY_DISCUSSION:
            return "Listen and discuss. Share your reads. Build a case or defend yourself."
        elif phase == GamePhase.DAY_VOTE:
            return "VOTE. Pick a player to eliminate. Majority rules."
        elif phase == GamePhase.NIGHT:
            return "Night phase. Special roles act. Town sleeps."
        elif phase == GamePhase.GAME_OVER:
            winner = game_state.check_win_condition()
            return f"Game over. {winner or 'Unknown'} wins."
        return "Waiting for the next phase."

    def compress_discussion_history(
        self,
        full_history: list[str],
        game_state: GameState,
    ) -> list[str]:
        """
        Progressive history compression to prevent context overflow.

        Rounds 1-2: Full conversation history
        Rounds 3-4: Summarized key accusations + full current round
        Round 5+:   Only elimination reveals, role flips, and current round

        This forces agents to rely on belief state rather than transcript
        search for older information.
        """
        current_round = game_state.round_number

        if current_round <= 2:
            return full_history

        if not full_history:
            return full_history

        # Separate current round entries from older ones.
        # We assume discussion_history is built sequentially; entries from
        # the current round are the most recent ones.  We use a heuristic:
        # keep the last N entries as "current round" based on alive count.
        alive_count = len(game_state.get_alive_players())
        # Each round has ~alive_count discussion entries per sub-round, 2 sub-rounds
        current_round_size = min(alive_count * 2, len(full_history))
        older = full_history[:-current_round_size] if current_round_size < len(full_history) else []
        current = full_history[-current_round_size:]

        if current_round <= 4:
            # Rounds 3-4: summarize older entries, keep current
            if older:
                summary = self._summarize_key_accusations(older)
                return [f"[EARLIER SUMMARY]: {summary}"] + current
            return current

        # Round 5+: only elimination/role reveals + current round
        critical = []
        for entry in older:
            entry_lower = entry.lower()
            if any(kw in entry_lower for kw in (
                "eliminated", "was found dead", "role:",
                "[system]", "flipped", "detective", "i am the",
                "mafia", "innocent",
            )):
                critical.append(entry)
        if critical:
            return [f"[CRITICAL HISTORY]:"] + critical + [""] + current
        return current

    @staticmethod
    def _summarize_key_accusations(entries: list[str]) -> str:
        """Extract key accusations from older discussion entries."""
        accusations: list[str] = []
        accusation_words = {"suspect", "suspicious", "vote", "accuse", "mafia", "guilty"}
        for entry in entries:
            entry_lower = entry.lower()
            if any(w in entry_lower for w in accusation_words):
                # Extract speaker and truncated content
                if ":" in entry:
                    speaker, content = entry.split(":", 1)
                    truncated = content.strip()[:80]
                    accusations.append(f"{speaker.strip()}: {truncated}")
        if not accusations:
            return "No significant accusations in earlier rounds."
        # Keep at most 8 key accusations
        return " | ".join(accusations[-8:])
