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

# Default threshold for Lifeline Protocol: if a Detective/Doctor's perceived
# suspicion exceeds this, they should reveal their identity to survive.
SELF_PRESERVATION_THRESHOLD = 0.45

# Graduated Lifeline Protocol thresholds
LIFELINE_SOFT_HINT_THRESHOLD = 0.35     # Soft reveal hint
LIFELINE_HARD_CLAIM_THRESHOLD = 0.45    # Hard role claim with conditional
LIFELINE_FULL_REVEAL_THRESHOLD = 0.55   # Immediate full reveal

# Red-check adjustment: if Detective holds confirmed Mafia finding,
# lower all thresholds by this amount (information > survival)
LIFELINE_RED_CHECK_ADJUSTMENT = 0.10

# Archetype modulation thresholds for belief updates
STRONG_EVIDENCE_THRESHOLD = 0.15    # Overconfident/Stubborn: require strong evidence
WEAK_EVIDENCE_THRESHOLD = 0.05      # Volatile/Reactive: update on any signal


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
    self_preservation_threshold: float = SELF_PRESERVATION_THRESHOLD

    # Staleness tracking: detect when beliefs loop without new evidence
    _previous_snapshot: dict[str, float] = field(default_factory=dict)
    _stale_rounds: int = 0
    _STALENESS_THRESHOLD: int = 2  # consecutive rounds with <0.05 total change

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

    def check_staleness(self) -> bool:
        """
        Check if beliefs have barely moved since the last snapshot.
        Returns True if the agent is stuck in a belief loop.
        Call this at the end of each discussion round.
        """
        if not self._previous_snapshot:
            self._previous_snapshot = dict(self.probabilities)
            return False
        total_delta = sum(
            abs(self.probabilities.get(n, 0.0) - self._previous_snapshot.get(n, 0.0))
            for n in set(self.probabilities) | set(self._previous_snapshot)
        )
        self._previous_snapshot = dict(self.probabilities)
        if total_delta < 0.05:
            self._stale_rounds += 1
        else:
            self._stale_rounds = 0
        return self._stale_rounds >= self._STALENESS_THRESHOLD

    @property
    def is_frustrated(self) -> bool:
        """True if beliefs have been stale for too long."""
        return self._stale_rounds >= self._STALENESS_THRESHOLD

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

    def should_reveal_identity(self, own_name: str, all_beliefs: dict[str, "SuspicionState"]) -> bool:
        """
        Lifeline Protocol: return True if enough other agents suspect *own_name*
        above self_preservation_threshold.

        We check every OTHER agent's belief about us. If the average
        suspicion across all agents who track us exceeds the threshold,
        we should reveal.
        """
        avg = self._get_avg_suspicion(own_name, all_beliefs)
        if avg is None:
            return False
        return avg >= self.self_preservation_threshold

    def get_lifeline_level(
        self, own_name: str, all_beliefs: dict[str, "SuspicionState"],
        *, has_red_check: bool = False,
    ) -> str | None:
        """
        Graduated Lifeline Protocol: return the appropriate reveal level.

        Returns one of:
          - "soft_hint"   (threshold 0.35): hint at having information
          - "hard_claim"  (threshold 0.45): conditional role claim
          - "full_reveal" (threshold 0.55): immediate full reveal
          - None: no reveal needed

        If has_red_check is True, all thresholds are lowered by 0.10
        because information preservation outweighs survival risk.
        """
        avg = self._get_avg_suspicion(own_name, all_beliefs)
        if avg is None:
            return None

        adjustment = LIFELINE_RED_CHECK_ADJUSTMENT if has_red_check else 0.0

        if avg >= LIFELINE_FULL_REVEAL_THRESHOLD - adjustment:
            return "full_reveal"
        if avg >= LIFELINE_HARD_CLAIM_THRESHOLD - adjustment:
            return "hard_claim"
        if avg >= LIFELINE_SOFT_HINT_THRESHOLD - adjustment:
            return "soft_hint"
        return None

    def _get_avg_suspicion(
        self, own_name: str, all_beliefs: dict[str, "SuspicionState"],
    ) -> float | None:
        """Calculate average suspicion of *own_name* across all other agents."""
        suspicion_values: list[float] = []
        for agent_name, belief in all_beliefs.items():
            if agent_name == own_name:
                continue
            if own_name in belief.probabilities:
                suspicion_values.append(belief.probabilities[own_name])
        if not suspicion_values:
            return None
        return sum(suspicion_values) / len(suspicion_values)


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
    4. Triggers frustration state when beliefs are stale (looping)
    """
    parts = [
        "SUSPICION CHECK (System 2 — slow down and think):",
        belief.summary(),
        "",
        "You MAY include one or more BELIEF_UPDATE tags in your REASONING to anchor "
        "specific inferences. You SHOULD include at least one per phase, even if your "
        "beliefs have not changed — tag it as a reaffirmation: "
        "'BELIEF_UPDATE: PlayerName=0.XX because [reaffirm — no new evidence changes this read].'",
        "",
        "Do NOT audit every living player every turn — focus on the 1-3 players whose "
        "suspicion SHOULD move based on what just happened.",
        "Format: BELIEF_UPDATE: PlayerName=0.XX because [specific evidence].",
        "Do NOT invent evidence. Only cite things visible in the discussion history.",
        "",
        "TRIGGER CONDITIONS (you SHOULD update when any of these occur):",
        "  - A player was just eliminated and their role revealed",
        "  - Someone directly accused you",
        "  - Your Mafia partner was mentioned (Mafia only)",
        "  - You completed an investigation (Detective only)",
        "",
    ]

    # Archetype-specific belief update modulation
    if archetype in ("Overconfident", "Stubborn"):
        parts.append(
            f"ARCHETYPE MODULATION: You are {archetype}. You only update beliefs "
            f"on STRONG evidence (threshold: beliefs must shift by at least "
            f"{STRONG_EVIDENCE_THRESHOLD} to warrant "
            f"a change). Small signals do not move you. Explain why evidence is strong enough "
            f"to shift your read, or explicitly state it is not."
        )
    elif archetype in ("Volatile", "Reactive"):
        parts.append(
            f"ARCHETYPE MODULATION: You are {archetype}. You update on ANY new information "
            f"(threshold: {WEAK_EVIDENCE_THRESHOLD} change is sufficient). New information "
            f"feels urgent. Show why the latest development reshapes your read."
        )
    elif archetype in ("Analytical", "Methodical"):
        parts.append(
            "ARCHETYPE MODULATION: You are " + archetype + ". Every BELIEF_UPDATE must include "
            "an explicit explanation chain: what evidence you saw, what inference you drew, "
            "and how it changes the probability. No update without a complete reasoning chain."
        )

    parts.append(
        "\nYour REASONING block should reflect the texture of your archetype. "
        "If you are Paranoid, your reasoning should convey anxiety and threat-inflation. "
        "If you are Volatile, show why new information feels more urgent than old. "
        "If you are Analytical, write structured inference with explicit evidence chains. "
        "The archetype is not just a conclusion modifier — it is a reasoning style. "
        "Write in that style. Do not produce the same neutral probability audit "
        "regardless of who you are."
    )

    if archetype == "Overconfident":
        top = belief.get_top_suspect()
        if top and top[1] < 0.7:
            parts.append(
                "\nCAUTION: Your top suspect is below 70% certainty. "
                "You MUST NOT use declarative accusations until your "
                "certainty is higher. Hedge your language this round."
            )

    # Staleness/Frustration check: break the belief loop
    if belief.is_frustrated:
        parts.append(
            "\n⚠ FRUSTRATION STATE: Your reads have NOT changed in multiple rounds. "
            "You are stuck in a loop recalculating the same numbers. STOP.\n"
            "This means the conversation has stalled and you are part of the problem.\n"
            "You MUST do ONE of the following in your next ACTION:\n"
            "  1. Name a COMPLETELY DIFFERENT suspect you have not focused on before\n"
            "  2. Challenge someone who has been quiet — demand they take a position\n"
            "  3. Call out the group for going in circles and force a new angle\n"
            "  4. Share new information you have been holding back\n"
            "Do NOT repeat your previous read. Do NOT recalculate the same probabilities. "
            "The room is stuck. Break it."
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


# ------------------------------------------------------------------ #
#  BeliefGraph — Weighted scum-tell detection                          #
# ------------------------------------------------------------------ #

@dataclass
class BeliefGraph:
    """
    Tracks behavioural scum-tells across the discussion.

    Three detectors:
      A) Late bandwagon — joining a vote without new reasoning
      B) Redirect — deflecting a solid case onto a quiet player
      C) Instahammer — voting immediately to end the round

    Each flag is accumulated per-player and surfaced in the belief
    prompt so agents can reason about patterns, not just quotes.
    """

    # Minimum character length for reasoning to count as "substantive"
    # when checking for late bandwagons. Below this → likely thin agreement.
    MIN_REASONING_LENGTH: int = 30

    # player_name -> list of scum-tell descriptions
    flags: dict[str, list[str]] = field(default_factory=dict)

    # Track vote timing and discussion contributions
    _discussion_counts: dict[str, int] = field(default_factory=dict)
    _vote_order: list[str] = field(default_factory=list)
    _current_target_votes: dict[str, int] = field(default_factory=dict)

    def record_discussion(self, player_name: str) -> None:
        """Record that a player spoke during discussion."""
        self._discussion_counts[player_name] = (
            self._discussion_counts.get(player_name, 0) + 1
        )

    def get_quiet_players(self, alive_players: list[str], threshold: int = 1) -> list[str]:
        """Return players who have spoken <= threshold times."""
        return [
            p for p in alive_players
            if self._discussion_counts.get(p, 0) <= threshold
        ]

    def check_late_bandwagon(
        self, voter: str, target: str, reasoning: str,
        current_votes: dict[str, str],
    ) -> str | None:
        """
        Flag if a player joins an existing vote majority without adding
        new reasoning. Returns a flag string or None.
        """
        # Count how many already voted for this target
        existing = sum(1 for t in current_votes.values() if t == target)
        if existing < 2:
            return None
        # Check if the reasoning is substantive (meets min length, not just "I agree")
        thin = len(reasoning.strip()) < self.MIN_REASONING_LENGTH
        agreeing = any(
            p in reasoning.lower()
            for p in ["i agree", "same", "what they said", "yeah", "ditto"]
        )
        if thin or agreeing:
            flag = (
                f"LATE BANDWAGON: {voter} joined the vote on {target} "
                f"(already {existing} votes) without new reasoning"
            )
            self.flags.setdefault(voter, []).append(flag)
            return flag
        return None

    def check_redirect(
        self, speaker: str, action: str,
        current_target: str | None, alive_players: list[str],
    ) -> str | None:
        """
        Flag if a player redirects attention from the current consensus
        target onto a quiet player.
        """
        if not current_target:
            return None
        quiet = self.get_quiet_players(alive_players)
        if not quiet:
            return None
        action_lower = action.lower()
        for q in quiet:
            if (
                q.lower() in action_lower
                and current_target.lower() not in action_lower
            ):
                flag = (
                    f"REDIRECT: {speaker} shifted attention from {current_target} "
                    f"onto quiet player {q}"
                )
                self.flags.setdefault(speaker, []).append(flag)
                return flag
        return None

    def check_instahammer(
        self, voter: str, votes_so_far: int, total_alive: int,
    ) -> str | None:
        """
        Flag if a player votes immediately when their vote could end
        the round (majority reached).
        """
        majority = total_alive // 2 + 1
        if votes_so_far + 1 >= majority:
            self._vote_order.append(voter)
            if len(self._vote_order) <= 2:
                # One of the first voters — not suspicious
                return None
            flag = (
                f"INSTAHAMMER: {voter} cast the decisive vote "
                f"({votes_so_far + 1}/{majority} needed) without "
                f"waiting for more discussion"
            )
            self.flags.setdefault(voter, []).append(flag)
            return flag
        self._vote_order.append(voter)
        return None

    def get_flags_for_prompt(self) -> str:
        """Format all accumulated flags for prompt injection."""
        if not self.flags:
            return ""
        lines = ["SCUM-TELL PATTERNS DETECTED (factor these into your reads):"]
        for player, player_flags in self.flags.items():
            for f in player_flags[-3:]:  # Last 3 flags per player max
                lines.append(f"  ⚠ {f}")
        return "\n".join(lines)

    def reset_round(self) -> None:
        """Clear per-round tracking (keep cumulative flags)."""
        self._vote_order.clear()
        self._current_target_votes.clear()


# ------------------------------------------------------------------ #
#  TemporalConsistency — "DeepSeek" slip detection                     #
# ------------------------------------------------------------------ #

# Patterns that indicate impossible temporal references
_TEMPORAL_SLIP_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"\byesterday\b", re.IGNORECASE),
        "referenced 'yesterday' — there is no yesterday in this game",
    ),
    (
        re.compile(r"\blast\s+(?:night|game|session|week)\b", re.IGNORECASE),
        "referenced a prior game/session that does not exist",
    ),
    (
        re.compile(r"\bpre[- ]?day\s+chat\b", re.IGNORECASE),
        "referenced 'pre-day chat' — no such thing exists",
    ),
    (
        re.compile(r"\bearlier\s+(?:today|conversation)\b", re.IGNORECASE),
        "referenced 'earlier conversation' outside the discussion history",
    ),
    (
        re.compile(r"\bremember\s+when\s+(?:we|you|they)\b", re.IGNORECASE),
        "used 'remember when' — possible confabulation",
    ),
]


class TemporalConsistencyChecker:
    """
    Checks agent messages for temporal impossibilities.

    On Day 1 (round 1), references to "yesterday" or "last night's
    discussion" are impossible. In any round, references to "pre-day chat"
    or conversations outside the game history are fabrications.

    Detected slips are accumulated and injected into the belief prefix
    so other agents can notice and flag the inconsistency.
    """

    def __init__(self) -> None:
        self.slips: dict[str, list[str]] = {}  # player_name -> slip descriptions

    def check_message(
        self, player_name: str, message: str, round_number: int,
    ) -> list[str]:
        """
        Check a message for temporal slips.
        Returns list of detected slip descriptions (empty if clean).
        """
        detected: list[str] = []
        for pattern, description in _TEMPORAL_SLIP_PATTERNS:
            if pattern.search(message):
                # "yesterday" is only a slip on round 1
                if "yesterday" in description and round_number > 1:
                    continue
                # "prior game/session" is always a slip — no prior game exists
                # "pre-day chat" is always a slip — no such thing exists
                # "earlier conversation" is always a slip
                # "remember when" is always a slip
                # (no round-gating needed for these — they are never valid)
                slip = f"TEMPORAL SLIP by {player_name}: {description}"
                detected.append(slip)
                self.slips.setdefault(player_name, []).append(slip)
        return detected

    def get_slips_for_prompt(self) -> str:
        """Format detected slips for prompt injection."""
        if not self.slips:
            return ""
        lines = [
            "IMPOSSIBLE INFORMATION DETECTED (someone may be confabulating):"
        ]
        for player, player_slips in self.slips.items():
            for s in player_slips[-2:]:  # Last 2 slips per player max
                lines.append(f"  🚩 {s}")
        return "\n".join(lines)
