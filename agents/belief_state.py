"""
agents/belief_state.py
-----------------------
Belief State layer for Mafia agents.

Each agent maintains a dictionary of player_name -> suspicion_level.
A prompt injection asks the agent to update its estimates before
generating a response, citing the specific observed evidence.

This is NOT Bayesian inference. There is no likelihood function, no
conditional probability calculation, no Bayes' theorem. The LLM picks
numbers based on its reading of the conversation. It is "structured
intuition" — better than nothing, worse than real probability theory.

What it does do:
  - Forces agents to think about suspicion levels before speaking
  - Gates the Overconfident archetype when certainty is low
  - Creates a paper trail of how reads evolved across rounds
  - Requires evidence citations, reducing confabulation

The "Dual-Process" framing:
  - System 1 (fast/intuitive): the agent's immediate response
  - System 2 (deliberate): the forced suspicion-update check
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class SuspicionState:
    """
    Tracks per-player suspicion levels (0.0 to 1.0).

    This is NOT Bayesian inference. It is structured intuition: the LLM
    assigns numbers based on conversational evidence, not conditional
    probability. There is no likelihood function, no Bayes' theorem.

    The name 'SuspicionState' replaces the misleading 'BayesianBelief'.
    """

    probabilities: dict[str, float] = field(default_factory=dict)
    update_count: int = 0

    def initialize(self, player_names: list[str], num_mafia: int = 2) -> None:
        """Set uniform prior: P(mafia) = num_mafia / total_players."""
        prior = num_mafia / len(player_names) if player_names else 0.0
        for name in player_names:
            self.probabilities[name] = prior
        self.update_count = 0

    def update(self, player_name: str, new_probability: float) -> None:
        """Update a single player's probability, clamped to [0.01, 0.99]."""
        clamped = max(0.01, min(0.99, new_probability))
        self.probabilities[player_name] = clamped
        self.update_count += 1

    def get_certainty(self, player_name: str) -> float:
        """Return the current mafia-probability for a player."""
        return self.probabilities.get(player_name, 0.0)

    def get_top_suspect(self) -> tuple[str, float] | None:
        """Return the player with the highest mafia probability."""
        if not self.probabilities:
            return None
        top = max(self.probabilities, key=self.probabilities.get)
        return top, self.probabilities[top]

    def remove_player(self, player_name: str) -> None:
        """Remove an eliminated player from tracking."""
        self.probabilities.pop(player_name, None)

    def summary(self) -> str:
        """Return a compact belief-state summary for prompt injection."""
        if not self.probabilities:
            return "No belief state."
        lines = []
        for name, prob in sorted(
            self.probabilities.items(), key=lambda x: -x[1]
        ):
            pct = int(prob * 100)
            lines.append(f"  {name}: {pct}% sus")
        return "Your current reads (mafia probability):\n" + "\n".join(lines)


# ------------------------------------------------------------------ #
#  Overconfident archetype gating                                      #
# ------------------------------------------------------------------ #

# Phrases that indicate overconfident/declarative certainty
_OVERCONFIDENT_MARKERS = [
    re.compile(r"\bis\s+the\s+one\b", re.IGNORECASE),
    re.compile(r"\bhas\s+been\s+since\b", re.IGNORECASE),
    re.compile(r"\bI'm\s+not\s+wrong\b", re.IGNORECASE),
    re.compile(r"\bwithout\s+a\s+doubt\b", re.IGNORECASE),
    re.compile(r"\b100\s*%\b", re.IGNORECASE),
    re.compile(r"\bdefinitely\s+(?:mafia|guilty)\b", re.IGNORECASE),
]


def should_gate_overconfidence(
    archetype: str,
    belief: SuspicionState,
    target_name: str | None,
) -> bool:
    """
    Return True if the agent is using the Overconfident archetype
    but their certainty for the target is below 0.7.
    When True, the agent should be forced to hedge.
    """
    if archetype != "Overconfident":
        return False
    if target_name is None:
        return False
    return belief.get_certainty(target_name) < 0.7


def build_belief_prompt_injection(
    belief: SuspicionState,
    archetype: str,
) -> str:
    """
    Build a prompt fragment that:
    1. Shows the agent their current suspicion state
    2. Asks them to update levels in their REASONING with evidence
    3. Gates overconfident declarations if certainty is low
    """
    parts = [
        "SUSPICION CHECK (System 2 — slow down and think):",
        belief.summary(),
        "",
        "Before responding, update your suspicion estimates in your REASONING.",
        "You MUST cite evidence from the discussion history for any change.",
        "Format: BELIEF_UPDATE: PlayerName=0.XX because [what they said/did in the discussion above].",
        "If you have no evidence for a player, do not change their number.",
        "Do NOT invent evidence. Only cite things visible in the discussion history.",
    ]

    if archetype == "Overconfident":
        top = belief.get_top_suspect()
        if top and top[1] < 0.7:
            parts.append(
                "\nCAUTION: Your top suspect is below 70% certainty. "
                "You MUST NOT use declarative accusations until your "
                "certainty is higher. Hedge your language this round."
            )

    return "\n".join(parts)


def parse_belief_updates(reasoning_text: str) -> dict[str, float]:
    """
    Extract BELIEF_UPDATE lines from reasoning text.
    Format: BELIEF_UPDATE: PlayerName=0.XX
    Returns dict of player_name -> new_probability.
    """
    updates: dict[str, float] = {}
    pattern = re.compile(
        r"BELIEF_UPDATE:\s*(\w+)\s*=\s*(0?\.\d+|1\.0|0\.0)",
        re.IGNORECASE,
    )
    for match in pattern.finditer(reasoning_text):
        name = match.group(1)
        prob = float(match.group(2))
        updates[name] = prob
    return updates


def apply_overconfidence_gate(action_text: str, belief: SuspicionState) -> str:
    """
    If the action contains overconfident markers about a player
    whose certainty is below 0.7, soften the language.
    Returns the (possibly modified) action text.
    """
    for name, prob in belief.probabilities.items():
        if prob >= 0.7:
            continue
        # Check if overconfident language targets this low-certainty player
        for marker in _OVERCONFIDENT_MARKERS:
            # Only modify if the player's name appears near the marker
            name_pattern = re.compile(
                rf"\b{re.escape(name)}\b.{{0,30}}" + marker.pattern,
                re.IGNORECASE,
            )
            if name_pattern.search(action_text):
                action_text = action_text.replace(
                    name, f"{name} (I think)", 1
                )
                break
    return action_text


# Backward-compat alias (orchestrator and tests may still import this name)
BayesianBelief = SuspicionState
