"""
agents/scheduler.py
--------------------
SchedulerAgent: monitors the discussion for conversational loops.

If "quote" or "specifics" are mentioned more than 3 times without
a new Vote or Accusation, the Scheduler triggers a "Chaos Event"
through the Narrator - revealing a random (non-role) piece of
metadata to break the loop.

Chaos Events include revealing things like:
  - Who was the last person to change their vote target
  - How many total accusations have been made
  - Which player has spoken the least
  - A random "overheard whisper" that is mechanically true but vague
"""

from __future__ import annotations

import random
import re

# Keywords that indicate a stalling quote-loop
_LOOP_KEYWORDS = re.compile(r"\b(quote|specifics|word[\- ]for[\- ]word|exact\s+words|receipts)\b", re.IGNORECASE)

# Keywords that indicate productive forward progress
_PROGRESS_KEYWORDS = re.compile(r"\b(VOTE:|I\s+vote|accuse|accusation|I\s+suspect)\b", re.IGNORECASE)

# Threshold: how many loop-keyword hits before triggering chaos
_LOOP_THRESHOLD = 3


class SchedulerAgent:
    """
    Monitors discussion for conversational loops and injects Chaos Events.

    Usage:
        scheduler = SchedulerAgent(player_names)
        # After each discussion message:
        event = scheduler.observe(speaker_name, message_text, discussion_history)
        if event:
            # Inject event through narrator
    """

    def __init__(self, player_names: list[str]) -> None:
        self.player_names = list(player_names)
        self._loop_keyword_count = 0
        self._progress_since_reset = False
        self._speaker_counts: dict[str, int] = {n: 0 for n in player_names}
        self._vote_changes: list[str] = []
        self._total_accusations = 0
        self._chaos_events_triggered = 0

    def observe(
        self,
        speaker: str,
        message: str,
        discussion_history: list[str],
    ) -> str | None:
        """
        Observe a message. Returns a Chaos Event string if the
        conversation has stalled, or None if everything is fine.
        """
        # Track speaker activity
        if speaker in self._speaker_counts:
            self._speaker_counts[speaker] += 1

        # Check for progress keywords
        if _PROGRESS_KEYWORDS.search(message):
            self._progress_since_reset = True
            self._loop_keyword_count = 0
            self._total_accusations += 1
            return None

        # Count loop keywords
        loop_hits = len(_LOOP_KEYWORDS.findall(message))
        if loop_hits > 0 and not self._progress_since_reset:
            self._loop_keyword_count += loop_hits
        elif loop_hits > 0:
            # Reset progress flag - we had progress but now looping again
            self._loop_keyword_count = loop_hits
            self._progress_since_reset = False

        # Check if we've hit the threshold
        if self._loop_keyword_count >= _LOOP_THRESHOLD:
            self._loop_keyword_count = 0
            self._progress_since_reset = False
            self._chaos_events_triggered += 1
            return self._generate_chaos_event(discussion_history)

        return None

    def record_vote_change(self, player: str) -> None:
        """Track when a player changes their vote target."""
        self._vote_changes.append(player)

    def reset_round(self) -> None:
        """Reset per-round counters (call between game rounds)."""
        self._loop_keyword_count = 0
        self._progress_since_reset = False
        self._vote_changes.clear()

    def _generate_chaos_event(self, discussion_history: list[str]) -> str:
        """
        Generate a random Chaos Event that reveals non-role metadata
        to break the conversational loop.
        """
        events = [
            self._chaos_quiet_player,
            self._chaos_accusation_count,
            self._chaos_vote_changer,
            self._chaos_whisper,
            self._chaos_word_count,
        ]
        # Pick a random chaos event generator
        generator = random.choice(events)
        return generator(discussion_history)

    def _chaos_quiet_player(self, _history: list[str]) -> str:
        """Reveal which player has spoken the least."""
        if not self._speaker_counts:
            return self._chaos_whisper(_history)
        quietest = min(self._speaker_counts, key=lambda k: self._speaker_counts[k])
        count = self._speaker_counts[quietest]
        return (
            f"CHAOS EVENT: The Narrator notices something. "
            f"{quietest} has only spoken {count} time(s) this game. "
            f"Silence can mean many things. The town should decide what it means here."
        )

    def _chaos_accusation_count(self, _history: list[str]) -> str:
        """Reveal total accusation count."""
        return (
            f"CHAOS EVENT: The Narrator checks the record. "
            f"There have been {self._total_accusations} direct accusations so far. "
            f"{'That is surprisingly few.' if self._total_accusations < 3 else 'The town is divided.'}"
        )

    def _chaos_vote_changer(self, _history: list[str]) -> str:
        """Reveal who last changed their vote."""
        if self._vote_changes:
            last = self._vote_changes[-1]
            return (
                f"CHAOS EVENT: The Narrator recalls that {last} was the last "
                f"player to shift their position. Make of that what you will."
            )
        return (
            "CHAOS EVENT: The Narrator notes that nobody has changed "
            "their position yet. Stubbornness or certainty? Hard to tell."
        )

    def _chaos_whisper(self, _history: list[str]) -> str:
        """Generate a vague but mechanically true 'overheard whisper'."""
        whispers = [
            "CHAOS EVENT: A whisper carries on the wind - 'Not everything is as it seems.' The town grows uneasy.",
            "CHAOS EVENT: The Narrator clears their throat. 'Someone here knows more than they're saying.' Everyone does, of course. But the reminder lands differently.",
            "CHAOS EVENT: A crow lands on the town square. The Narrator says: 'Even the birds are watching. Perhaps it's time to stop asking for proof and start making decisions.'",
            "CHAOS EVENT: The Narrator interrupts. 'You've been going in circles. The Mafia loves circles. Pick a direction.'",
        ]
        return random.choice(whispers)

    def _chaos_word_count(self, history: list[str]) -> str:
        """Reveal word count disparity between players."""
        word_counts: dict[str, int] = {n: 0 for n in self.player_names}
        for line in history:
            for name in self.player_names:
                if line.startswith(f"{name}:"):
                    content = line[len(name) + 1:].strip()
                    word_counts[name] += len(content.split())
                    break
        if not word_counts or not any(word_counts.values()):
            return self._chaos_whisper(history)
        most_verbose = max(word_counts, key=lambda k: word_counts[k])
        most_quiet = min(word_counts, key=lambda k: word_counts[k])
        return (
            f"CHAOS EVENT: The Narrator counts the words. "
            f"{most_verbose} has said the most ({word_counts[most_verbose]} words). "
            f"{most_quiet} has said the least ({word_counts[most_quiet]} words). "
            f"Volume and guilt don't correlate. Or do they?"
        )
