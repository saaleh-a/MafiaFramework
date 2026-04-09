"""
engine/belief_state.py
----------------------
Bayesian belief tracking for agents.

Each agent maintains a dictionary of player_name -> probability_of_mafia.
A middleware hook intercepts every message and prompts the agent to update
its beliefs before generating a response.

When an agent's certainty for a target is below 0.7, the Overconfident
archetype's declarative patterns are suppressed in favour of hedged language.

Entropy decay prevents agents from "solving" the game too fast by adding
noise proportional to how certain the beliefs have become overall, keeping
the human drama of archetypes alive.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field


# Threshold below which Overconfident archetype constraints are applied
CERTAINTY_THRESHOLD = 0.7

# Entropy floor: beliefs are nudged toward uncertainty when total entropy
# drops below this, preventing agents from converging too quickly.
_ENTROPY_FLOOR = 1.5

# Constraints injected when certainty is low and archetype is Overconfident
OVERCONFIDENT_SUPPRESSION = (
    "Your certainty about this target is below 70%. "
    "You MUST NOT use declarative accusations. Instead of stating conclusions, "
    "raise questions. Replace 'X is Mafia' with 'I'm getting a read on X but "
    "I need more'. Do not commit to a target you are not confident about."
)


@dataclass
class BayesianBelief:
    """
    Per-agent belief state tracking probability of each player being Mafia.

    Initialised with a uniform prior based on the known number of Mafia
    players and total players.
    """
    agent_name: str
    beliefs: dict[str, float] = field(default_factory=dict)
    _num_mafia: int = 2

    def initialise(self, player_names: list[str], num_mafia: int = 2) -> None:
        """Set uniform priors for all players (excluding self)."""
        self._num_mafia = num_mafia
        others = [n for n in player_names if n != self.agent_name]
        prior = num_mafia / len(others) if others else 0.0
        self.beliefs = {name: round(prior, 4) for name in others}

    def update(self, target: str, evidence_weight: float) -> None:
        """
        Bayesian update on a single target.

        evidence_weight > 0 means evidence toward Mafia,
        evidence_weight < 0 means evidence toward Innocent.
        Magnitude indicates strength (typical range: 0.05 to 0.3).
        """
        if target not in self.beliefs:
            return

        prior = self.beliefs[target]
        # Convert weight to a likelihood ratio
        # Positive weight -> more likely Mafia, negative -> less likely
        likelihood_ratio = math.exp(evidence_weight)

        # Bayes' rule: posterior ∝ prior * likelihood
        posterior = prior * likelihood_ratio
        # Normalise against complement
        complement = (1 - prior) * (1 / likelihood_ratio)
        total = posterior + complement
        if total > 0:
            self.beliefs[target] = round(
                max(0.01, min(0.99, posterior / total)), 4
            )

        self._apply_entropy_floor()

    def update_from_vote(self, voter: str, target: str, voter_is_suspect: bool) -> None:
        """
        Update beliefs based on observed voting behaviour.

        If a suspected player votes for someone, that target becomes
        slightly less suspicious (Mafia rarely votes for their own).
        """
        if voter_is_suspect and target in self.beliefs:
            self.update(target, -0.1)  # slight innocence signal
        elif not voter_is_suspect and target in self.beliefs:
            self.update(target, 0.05)  # mild suspicion signal

    def update_from_accusation(self, accuser: str, target: str) -> None:
        """Mild update when someone accuses a target."""
        if target in self.beliefs:
            self.update(target, 0.08)

    def update_from_defense(self, defender: str, target: str) -> None:
        """When someone defends a target, slight Mafia signal on defender."""
        if defender in self.beliefs:
            self.update(defender, 0.05)

    def get_certainty(self, target: str) -> float:
        """Return how certain we are about a target's alignment (0-1)."""
        if target not in self.beliefs:
            return 0.0
        p = self.beliefs[target]
        # Certainty is distance from 0.5 (maximum uncertainty), scaled to 0-1
        return abs(p - 0.5) * 2

    def get_top_suspect(self) -> tuple[str, float] | None:
        """Return the player with the highest Mafia probability."""
        if not self.beliefs:
            return None
        top = max(self.beliefs, key=lambda k: self.beliefs[k])
        return top, self.beliefs[top]

    def should_suppress_overconfident(self, target: str) -> bool:
        """Return True if Overconfident declarations should be suppressed."""
        return self.get_certainty(target) < CERTAINTY_THRESHOLD

    def format_for_prompt(self) -> str:
        """Format beliefs as a concise string for injection into prompts."""
        if not self.beliefs:
            return "No beliefs formed yet."
        lines = []
        sorted_beliefs = sorted(
            self.beliefs.items(), key=lambda x: x[1], reverse=True
        )
        for name, prob in sorted_beliefs:
            certainty = self.get_certainty(name)
            level = (
                "HIGH" if certainty >= 0.7
                else "MEDIUM" if certainty >= 0.4
                else "LOW"
            )
            lines.append(f"  {name}: {prob:.0%} Mafia probability [{level} certainty]")
        return "Your current reads:\n" + "\n".join(lines)

    def _apply_entropy_floor(self) -> None:
        """
        Prevent beliefs from converging too tightly.

        When total Shannon entropy drops below the floor, add noise
        proportional to the deficit. This keeps the game dramatic.
        """
        if len(self.beliefs) < 2:
            return

        probs = list(self.beliefs.values())
        # Sum of binary entropies: H(p) = -p*log2(p) - (1-p)*log2(1-p)
        # for each player's Mafia probability. Higher = more uncertain.
        entropy = sum(
            -(p * math.log2(p) + (1 - p) * math.log2(1 - p))
            for p in probs if 0 < p < 1
        )

        if entropy < _ENTROPY_FLOOR:
            deficit = _ENTROPY_FLOOR - entropy
            noise_scale = min(0.08, deficit * 0.03)
            for name in self.beliefs:
                noise = random.gauss(0, noise_scale)
                self.beliefs[name] = round(
                    max(0.05, min(0.95, self.beliefs[name] + noise)), 4
                )


def build_belief_prompt_injection(
    belief: BayesianBelief,
    archetype: str,
    vote_target: str | None = None,
) -> str:
    """
    Build the belief-state prompt block to inject before agent response.

    If archetype is Overconfident and certainty on the vote target is
    below threshold, inject suppression constraints.
    """
    parts = [
        "BELIEF STATE (update these reads based on what you just heard):",
        belief.format_for_prompt(),
    ]

    if archetype == "Overconfident" and vote_target:
        if belief.should_suppress_overconfident(vote_target):
            parts.append(f"\n⚠ CONSTRAINT: {OVERCONFIDENT_SUPPRESSION}")

    return "\n".join(parts)
