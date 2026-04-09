"""
agents/scheduler.py
-------------------
SchedulerAgent that monitors conversation for repetitive loops.

If the discussion repeats the words "quote" or "specifics" more than
three times without a new Vote or Accusation action, the scheduler
triggers a "Chaos Event" through the Narrator—revealing a random
(non-role) piece of metadata to break the loop.
"""

from __future__ import annotations

import random
import re


# Trigger words that indicate a quote-loop
_LOOP_WORDS = re.compile(r"\b(quote|specifics|word[- ]?for[- ]?word|receipts)\b", re.IGNORECASE)

# Patterns that count as "forward progress" and reset the counter
_PROGRESS_PATTERNS = re.compile(
    r"\b(VOTE:|I vote|I'm voting|I accuse|accusing|accusation)\b",
    re.IGNORECASE,
)

# Threshold: how many loop-word hits before triggering a chaos event
LOOP_THRESHOLD = 3

# Non-role metadata fragments the chaos event can reveal
_CHAOS_METADATA = [
    "The last vote was cast in under 2 seconds — someone wasn't thinking.",
    "Three players have used the exact same opening phrase this game.",
    "The player who spoke longest today has been the quietest at night.",
    "Someone changed their vote target between their reasoning and their action.",
    "The first player eliminated in discussion was also the most frequently named.",
    "Two players have never directly addressed each other in any round.",
    "The average message length has been dropping each round — people are getting impatient.",
    "One player has agreed with the majority vote in every single round.",
    "The player with the most accusations against them has never been voted out.",
    "Someone's discussion contribution was exactly the same length two rounds in a row.",
    "The most vocal player in round one has barely spoken since.",
    "No player has referenced a specific round number in their last three messages.",
]


class SchedulerAgent:
    """
    Monitors conversation for repetitive quote-demand loops and
    triggers chaos events to break deadlocks.
    """

    def __init__(self) -> None:
        self._loop_word_count: int = 0
        self._chaos_events_triggered: int = 0
        self._used_metadata: set[int] = set()

    def scan_message(self, message: str) -> bool:
        """
        Process a new message from the discussion.

        Returns True if a chaos event should be triggered.
        """
        # Check for forward progress (vote/accusation)
        if _PROGRESS_PATTERNS.search(message):
            self._loop_word_count = 0
            return False

        # Count loop-word occurrences
        hits = len(_LOOP_WORDS.findall(message))
        self._loop_word_count += hits

        return self._loop_word_count > LOOP_THRESHOLD

    def get_chaos_event(self) -> str:
        """
        Generate a chaos event prompt for the Narrator.

        Returns a metadata revelation that breaks the quote loop.
        """
        # Pick an unused metadata fragment if possible
        available = [
            i for i in range(len(_CHAOS_METADATA))
            if i not in self._used_metadata
        ]
        if not available:
            # All used — reset pool
            self._used_metadata.clear()
            available = list(range(len(_CHAOS_METADATA)))

        idx = random.choice(available)
        self._used_metadata.add(idx)
        self._chaos_events_triggered += 1
        self._loop_word_count = 0  # reset after triggering

        metadata = _CHAOS_METADATA[idx]
        return (
            f"CHAOS EVENT: The discussion has stalled in a loop of demands for "
            f"quotes and specifics. Break the loop NOW.\n"
            f"Reveal this observation to the group (dramatically): \"{metadata}\"\n"
            f"Then redirect the discussion toward a concrete vote or accusation."
        )

    def reset(self) -> None:
        """Reset loop tracking for a new round."""
        self._loop_word_count = 0
