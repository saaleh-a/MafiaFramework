"""
engine/orchestrator.py - v4
-----------------------------
Game loop. Passes archetype through to all display and logging calls.
Integrates:
  - SuspicionState: structured-intuition suspicion tracking per agent
  - SummaryAgent: Low-cognitive-load narrative summaries per phase
  - MAF ContextProviders: belief state + memory injected via session.state
  - MAF Agent middleware: corporate-speak enforcement via pipeline
  - MAF @tool: structured vote/target actions via tool-calling
  - Rate limiting: phase-tier semaphore + graceful degradation fallbacks

v4 changes (from v3):
  - Replaced manual belief_prefix string concatenation with MAF ContextProviders
  - Added _sync_provider_state() to push game state into session.state
  - Agents now use Agent() constructor with tools, middleware, compaction
  - Corporate-speak enforcement moved from base.py retry to agent middleware
"""

import asyncio
import logging
import random
import re
import sys
from engine.game_state import GameState, GamePhase
from engine.game_log import (
    print_phase_header, print_agent_action,
    print_vote_tally, print_night_result, print_game_over,
)
from agents.narrator  import NarratorAgent
from agents.mafia     import MafiaAgent
from agents.detective import DetectiveAgent
from agents.doctor    import DoctorAgent
from agents.villager  import VillagerAgent
from agents.belief_state import (
    SuspicionState,
    build_belief_prompt_injection,
    parse_belief_updates,
    apply_overconfidence_gate,
    BeliefGraph,
    TemporalConsistencyChecker,
)
from agents.providers import BeliefStateProvider, CrossGameMemoryProvider
from agents.summary   import SummaryAgent
from agents.memory    import GameMemoryStore
from config.settings  import (
    MAFIA_CONSENSUS_SHORTLIST_SIZE,
    MAFIA_DETECTIVE_VOTE_WEIGHT,
    MAFIA_EVASION_BONUS,
    MAFIA_MAX_CONCURRENT_CALLS,
    MAFIA_VOTE_CONFIDENCE_THRESHOLD,
)

logger = logging.getLogger(__name__)

# Fallback self-protect threshold: when the Doctor API call fails entirely,
# use a lower threshold than the prompt's 0.6 because a conservative
# self-protect is safer than a random target in degraded mode.
_FALLBACK_SELF_PROTECT_THRESHOLD = 0.3


class MafiaGameOrchestrator:
    def __init__(
        self,
        game_state:   GameState,
        narrator:     NarratorAgent,
        mafia_agents: list[MafiaAgent],
        detective:    DetectiveAgent,
        doctor:       DoctorAgent,
        villagers:    list[VillagerAgent],
        debug:        bool = False,
        quiet:        bool = False,
        memory_store: GameMemoryStore | None = None,
        assignments:  list[dict] | None = None,
    ) -> None:
        self.gs        = game_state
        self.narrator  = narrator
        self.mafia     = mafia_agents
        self.detective = detective
        self.doctor    = doctor
        self.villagers = villagers
        self.debug     = debug
        self.quiet     = quiet
        self._memory   = memory_store
        self._assignments = assignments or []
        self._agents: dict[str, any] = {}
        for a in mafia_agents + [detective, doctor] + villagers:
            self._agents[a.name] = a

        # Suspicion State: each agent tracks suspicion levels (structured intuition)
        player_names = list(game_state.players.keys())
        total_mafia = sum(
            1 for player in game_state.players.values() if player.role == "Mafia"
        )
        self._beliefs: dict[str, SuspicionState] = {}
        for name in player_names:
            belief = SuspicionState()
            # Exclude self from suspicion tracking
            others = [n for n in player_names if n != name]
            known_mafia = 1 if game_state.players[name].role == "Mafia" else 0
            belief.initialize(others, num_mafia=max(1, total_mafia - known_mafia))
            self._beliefs[name] = belief

        # Summary: generates low-cognitive-load narrative each phase
        self._summary = SummaryAgent()

        # BeliefGraph: scum-tell pattern detection (bandwagon, redirect, instahammer)
        self._belief_graph = BeliefGraph()

        # Temporal consistency: "DeepSeek" slip detection
        self._temporal_checker = TemporalConsistencyChecker()

        # Track vote parse failures per agent for format reinforcement
        self._vote_parse_failures: dict[str, int] = {}
        self._current_vote_shortlist: list[str] = []
        self._current_vote_recommendations: dict[str, str] = {}
        self._last_vote_warnings: list[str] = []

        # Phase-tier semaphore: limits concurrency within a single phase.
        # Minimum of 2 concurrent slots ensures progress even under
        # heavy rate limiting (1 would serialize all calls within a phase).
        phase_limit = max(2, MAFIA_MAX_CONCURRENT_CALLS - 1)
        self._phase_semaphore = asyncio.Semaphore(phase_limit)

        # Populate MAF ContextProvider state on each agent's session.
        # This is how the BeliefStateProvider and CrossGameMemoryProvider
        # get their data — via session.state, the MAF-idiomatic way.
        self._sync_provider_state()

    def _sync_provider_state(self) -> None:
        """
        Push current game state into each agent's session.state so that
        MAF ContextProviders can read it during before_run().

        Called once at init and again whenever beliefs/graphs update.
        """
        for name, agent in self._agents.items():
            session = agent.session
            belief = self._beliefs.get(name)

            # BeliefStateProvider state
            session.state.setdefault(BeliefStateProvider.DEFAULT_SOURCE_ID, {})
            belief_state = session.state[BeliefStateProvider.DEFAULT_SOURCE_ID]
            belief_state["suspicion"] = belief
            belief_state["archetype"] = agent.archetype
            belief_state["graph"] = self._belief_graph
            belief_state["temporal"] = self._temporal_checker
            belief_state["all_beliefs"] = self._beliefs
            belief_state["role"] = agent.role
            belief_state["name"] = name
            belief_state["phase_value"] = self.gs.phase.value
            belief_state["vote_shortlist"] = list(self._current_vote_shortlist)
            belief_state["recommended_vote"] = self._current_vote_recommendations.get(name, "")
            belief_state["evasion_scores"] = dict(self._belief_graph.evasion_scores)
            belief_state["detective_vote_weight"] = MAFIA_DETECTIVE_VOTE_WEIGHT

            # Pass detective findings for Last Stand Protocol red-check detection
            if agent.role == "Detective" and hasattr(agent, "findings"):
                belief_state["findings"] = agent.findings
            else:
                belief_state["findings"] = {}

            # Pass vote parse failure count for format reinforcement
            belief_state["vote_parse_failures"] = self._vote_parse_failures.get(name, 0)

            # CrossGameMemoryProvider state
            session.state.setdefault(CrossGameMemoryProvider.DEFAULT_SOURCE_ID, {})
            mem_state = session.state[CrossGameMemoryProvider.DEFAULT_SOURCE_ID]
            mem_state["store"] = self._memory
            mem_state["role"] = agent.role

        self.gs.evasion_scores = dict(self._belief_graph.evasion_scores)

    def _compute_room_suspicion(
        self,
        candidates: list[str],
    ) -> dict[str, float]:
        """Aggregate room suspicion into a shortlist-friendly score."""
        alive = set(self.gs.get_alive_players())
        scores: dict[str, float] = {}
        for target in candidates:
            if target not in alive:
                continue
            weighted_total = 0.0
            total_weight = 0.0
            for source_name, belief in self._beliefs.items():
                if source_name not in alive or source_name == target:
                    continue
                suspicion = belief.probabilities.get(target)
                if suspicion is None:
                    continue
                weight = float(self.gs.get_vote_weight(source_name))
                weighted_total += suspicion * weight
                total_weight += weight

            score = weighted_total / total_weight if total_weight else 0.0
            score += self._belief_graph.evasion_scores.get(target, 0) * MAFIA_EVASION_BONUS
            if (
                self.detective.name in alive
                and self.detective.findings.get(target) == "Mafia"
            ):
                score += 0.35
            scores[target] = score
        return scores

    def _build_vote_shortlist(
        self,
        alive: list[str],
        *,
        allowed_targets: list[str] | None = None,
    ) -> list[str]:
        """Return the highest-pressure wagon shortlist for the current vote."""
        candidate_pool = list(allowed_targets or alive)
        candidate_pool = [target for target in candidate_pool if target in alive]
        if not candidate_pool:
            return []

        room_scores = self._compute_room_suspicion(candidate_pool)
        ranked = sorted(
            candidate_pool,
            key=lambda target: (
                -room_scores.get(target, 0.0),
                -self._belief_graph.evasion_scores.get(target, 0),
                target,
            ),
        )
        limit = min(MAFIA_CONSENSUS_SHORTLIST_SIZE, len(ranked))
        return ranked[:limit]

    def _recommend_vote_target(
        self,
        voter: str,
        candidates: list[str],
    ) -> tuple[str | None, float, str]:
        """Recommend a vote target anchored to beliefs plus coordination."""
        legal = [target for target in candidates if target != voter]
        if not legal:
            return None, 0.0, "belief"

        player = self.gs.players.get(voter)
        if not player:
            return legal[0], 0.0, "belief"

        if player.role == "Mafia":
            partners = {
                ally.name for ally in self.mafia if ally.name != voter and ally.name in legal
            }
            town_legal = [target for target in legal if target not in partners]
            if town_legal:
                legal = town_legal
            room_scores = self._compute_room_suspicion(legal)
            ranked = sorted(
                legal,
                key=lambda target: (
                    -room_scores.get(target, 0.0),
                    -self._belief_graph.evasion_scores.get(target, 0),
                    target,
                ),
            )
            top = ranked[0]
            return top, room_scores.get(top, 0.0), "consensus"

        if player.role == "Detective":
            red_checks = [
                target for target in legal
                if self.detective.findings.get(target) == "Mafia"
            ]
            if red_checks:
                return red_checks[0], 1.0, "belief"

        belief = self._beliefs.get(voter)
        scored: list[tuple[str, float]] = []
        for target in legal:
            score = belief.probabilities.get(target, 0.0) if belief else 0.0
            score += self._belief_graph.evasion_scores.get(target, 0) * MAFIA_EVASION_BONUS
            if player.role == "Detective" and self.detective.findings.get(target) == "Innocent":
                score -= 0.25
            scored.append((target, score))

        scored.sort(key=lambda item: (-item[1], item[0]))
        top_target, top_score = scored[0]
        if top_score >= MAFIA_VOTE_CONFIDENCE_THRESHOLD:
            return top_target, top_score, "belief"

        # Early-round protection: in rounds 1-2, prefer the agent's own
        # (low-confidence) belief over room consensus.  Consensus in early
        # rounds is unreliable and self-reinforcing — the first accusation
        # can snowball into a unanimous miskill.  Returning the agent's
        # own top target with its actual score lets the coordination note
        # flag the low confidence and encourages independent reasoning.
        if self.gs.round_number <= 2:
            return top_target, top_score, "belief"

        consensus_scores = self._compute_room_suspicion(legal)
        consensus_ranked = sorted(
            legal,
            key=lambda target: (
                -consensus_scores.get(target, 0.0),
                -self._belief_graph.evasion_scores.get(target, 0),
                target,
            ),
        )
        consensus_top = consensus_ranked[0]
        return consensus_top, top_score, "consensus"

    def _build_coordination_note(
        self,
        voter: str,
        allowed_targets: list[str],
        recommendation: str | None,
        basis: str,
        confidence: float,
    ) -> str:
        """Human-readable vote coordination note for the vote prompt."""
        shortlist = [target for target in self._current_vote_shortlist if target in allowed_targets]
        display_shortlist = shortlist or allowed_targets
        note = [
            "CONSENSUS TRACKING:",
            f"Top pressure targets: {', '.join(display_shortlist)}.",
            f"Recommended vote for you: {recommendation or 'none'} ({basis}, confidence {confidence:.2f}).",
        ]
        # Early-round caution: in rounds 1-2 with low confidence, warn
        # agents that consensus is unreliable and encourage independent
        # reasoning over herd following.
        if self.gs.round_number <= 2 and confidence < MAFIA_VOTE_CONFIDENCE_THRESHOLD:
            note.append(
                f"⚠ EARLY GAME WARNING: It is round {self.gs.round_number}. "
                "There is very little evidence yet. The consensus is based on "
                "first impressions, not confirmed information. Do NOT default to "
                "the room's direction just because others are voting that way. "
                "Think about WHO started the pressure and WHY. An early bandwagon "
                "with no evidence is exactly how Mafia steers Town into eliminating "
                "their own power roles. Vote on YOUR read, not the room's momentum."
            )
        else:
            note.append(
                "Pick from the shortlist unless you have a real reason to override it.",
            )
        if self._belief_graph.evasion_scores:
            evasion_text = ", ".join(
                f"{player}:{score}"
                for player, score in sorted(
                    self._belief_graph.evasion_scores.items(),
                    key=lambda item: (-item[1], item[0]),
                )
                if score > 0 and player in allowed_targets
            )
            if evasion_text:
                note.append(f"Evasion pressure: {evasion_text}.")
        if voter == self.detective.name and self.detective.findings:
            note.append(
                f"Your vote counts as {MAFIA_DETECTIVE_VOTE_WEIGHT}. Use confirmed information to force consolidation."
            )
        return "\n".join(note)

    def _resolve_vote_target(
        self,
        voter: str,
        parsed_target: str | None,
        reasoning: str,
        action: str,
        allowed_targets: list[str],
        recommended_target: str | None,
        confidence: float,
    ) -> tuple[str | None, str | None]:
        """Resolve a final vote target and explain any engine-side override."""
        legal = [target for target in allowed_targets if target != voter]
        if not legal:
            return None, "no legal targets available"

        if parsed_target and parsed_target not in legal:
            parsed_target = None

        if parsed_target is None:
            return recommended_target or legal[0], "vote was unparseable; used engine recommendation"

        if not recommended_target:
            return parsed_target, None

        if parsed_target == recommended_target:
            return parsed_target, None

        override_text = f"{reasoning}\n{action}".lower()
        if "override:" in override_text:
            return parsed_target, None

        if confidence >= MAFIA_VOTE_CONFIDENCE_THRESHOLD:
            return (
                recommended_target,
                f"vote contradicted high-confidence belief state; forced to {recommended_target}",
            )

        return parsed_target, None

    def _sync_vote_guidance(
        self,
        alive: list[str],
        *,
        allowed_targets: list[str] | None = None,
    ) -> None:
        """Refresh the current shortlist and per-agent vote recommendations."""
        self._current_vote_shortlist = self._build_vote_shortlist(
            alive, allowed_targets=allowed_targets,
        )
        recommendations: dict[str, str] = {}
        for voter in alive:
            legal = [
                target for target in (
                    self._current_vote_shortlist or allowed_targets or alive
                )
                if target != voter and target in alive
            ]
            if not legal:
                legal = [target for target in alive if target != voter]
            target, _, _ = self._recommend_vote_target(voter, legal)
            recommendations[voter] = target or ""
        self._current_vote_recommendations = recommendations
        self._sync_provider_state()

    # ------------------------------------------------------------------ #
    #  Graceful degradation fallbacks                                      #
    # ------------------------------------------------------------------ #

    def _fallback_discussion(self, name: str) -> tuple[str, str]:
        """Fallback when discussion API call fails: player passes."""
        logger.warning("[%s] Discussion call failed — player passes turn", name)
        return ("", "I'll listen for now.")

    def _fallback_vote(self, name: str, candidates: list[str]) -> tuple[str, str]:
        """Fallback when vote API call fails: vote using belief state."""
        eligible = [p for p in candidates if p != name]
        target, _, basis = self._recommend_vote_target(name, eligible)
        if target:
            logger.warning(
                "[%s] Vote call failed — fallback to %s target: %s",
                name, basis, target,
            )
            return ("", f"VOTE: {target}")
        if eligible:
            target = random.choice(eligible)
            logger.warning(
                "[%s] Vote call failed — random fallback: %s",
                name, target,
            )
            return ("", f"VOTE: {target}")
        return ("", "")

    def _fallback_night_kill(self, name: str, targets: list[str]) -> tuple[str, str]:
        """Fallback when Mafia night kill API call fails: target highest-threat."""
        belief = self._beliefs.get(name)
        if belief and targets:
            scored = [(p, belief.probabilities.get(p, 0.0)) for p in targets]
            # Mafia targets the player LEAST suspected (highest threat to Mafia)
            # because least-suspected Town players are likely power roles.
            scored.sort(key=lambda x: x[1])
            target = scored[0][0]
            logger.warning(
                "[%s] Night kill call failed — fallback to lowest-suspicion Town: %s",
                name, target,
            )
            return ("", target)
        if targets:
            target = random.choice(targets)
            logger.warning("[%s] Night kill call failed — random: %s", name, target)
            return ("", target)
        return ("", "")

    def _fallback_investigation(self, name: str, eligible: list[str]) -> tuple[str, str]:
        """Fallback when Detective investigation API call fails: most suspicious."""
        belief = self._beliefs.get(name)
        if belief and eligible:
            scored = [(p, belief.probabilities.get(p, 0.0)) for p in eligible]
            scored.sort(key=lambda x: -x[1])
            target = scored[0][0]
            logger.warning(
                "[%s] Investigation call failed — fallback to most suspicious: %s",
                name, target,
            )
            return ("", target)
        if eligible:
            target = random.choice(eligible)
            logger.warning("[%s] Investigation call failed — random: %s", name, target)
            return ("", target)
        return ("", "")

    def _fallback_protection(self, name: str, valid: list[str]) -> tuple[str, str]:
        """Fallback when Doctor protection API call fails: protect self if threatened."""
        belief = self._beliefs.get(name)
        if belief and name in [p for p in self.gs.get_alive_players()]:
            own_suspicion_values = []
            for _, b in self._beliefs.items():
                if name in b.probabilities:
                    own_suspicion_values.append(b.probabilities[name])
            avg_suspicion = (
                sum(own_suspicion_values) / len(own_suspicion_values)
                if own_suspicion_values else 0.0
            )
            # If Doctor is threatened, protect self. This threshold is
            # intentionally lower than the prompt's 0.6 override because
            # this path fires only when the API call failed entirely.
            if avg_suspicion > _FALLBACK_SELF_PROTECT_THRESHOLD and name in valid:
                logger.warning(
                    "[%s] Protection call failed — self-protect (suspicion %.2f)",
                    name, avg_suspicion,
                )
                return ("", name)
        if valid:
            target = random.choice(valid)
            logger.warning("[%s] Protection call failed — random: %s", name, target)
            return ("", target)
        return ("", "")

    async def run_game(self) -> str:
        print_phase_header("GAME START", 0)
        await self._narrate("Announce the start of the Mafia game. Set the scene.")

        while self.gs.check_win_condition() is None:
            await self._run_day_phase()
            if self.gs.check_win_condition():
                break
            await self._run_night_phase()
            self.gs.round_number += 1
            self.gs.reset_round_state()

        winner = self.gs.check_win_condition()
        print_game_over(winner, self.gs)

        # Persist cross-game learnings
        if self._memory:
            self._memory.record_game_outcome(
                winner=winner,
                role_assignments=self._assignments,
                round_count=self.gs.round_number,
            )
            self._memory.save()

        return winner

    async def _run_day_phase(self) -> None:
        self.gs.phase = GamePhase.DAY_DISCUSSION
        print_phase_header("DAY DISCUSSION", self.gs.round_number)

        # Summary Agent: display narrative summary at phase start
        narrative = self._summary.summarize(self.gs)
        print(narrative)

        if self.gs.round_number == 1:
            await self._narrate("Announce round 1. First morning. Town meets.")
        else:
            victim = self.gs.eliminated_this_round
            if victim:
                role = self.gs.players[victim].role
                await self._narrate(
                    f"Dawn. {victim} ({role}) was found dead. Town must now discuss."
                )
                # Remove eliminated player from all belief states
                for belief in self._beliefs.values():
                    belief.remove_player(victim)
            else:
                await self._narrate("Dawn. Nobody died last night. Tension remains.")

        alive            = self.gs.get_alive_players()
        discussion_history: list[str] = []

        for _round in range(2):
            order = list(alive)
            random.shuffle(order)
            for name in order:
                if name not in self._agents:
                    continue
                agent = self._agents[name]

                # Sync ContextProvider state before each agent turn.
                # The BeliefStateProvider and CrossGameMemoryProvider read
                # from session.state — this is the MAF-idiomatic approach.
                self._sync_provider_state()

                # Compress history for late-game context management
                compressed = self._summary.compress_discussion_history(
                    discussion_history, self.gs,
                )

                try:
                    reasoning, action = await agent.day_discussion(
                        self.gs, compressed,
                    )
                except Exception as exc:
                    logger.error("[%s] Discussion failed: %s", name, exc)
                    reasoning, action = self._fallback_discussion(name)

                # Parse and apply belief updates from reasoning
                belief = self._beliefs.get(name)
                if belief and reasoning:
                    updates = parse_belief_updates(reasoning)
                    for target, prob in updates.items():
                        belief.update(target, prob)

                # Overconfidence gate: soften if certainty too low
                if belief and agent.archetype == "Overconfident":
                    action = apply_overconfidence_gate(action, belief)

                # Track discussion contribution in BeliefGraph
                self._belief_graph.record_discussion(name)

                # Check for temporal slips in the action
                self._temporal_checker.check_message(
                    name, action, self.gs.round_number,
                )

                # Check for redirects (BeliefGraph)
                current_target = self._get_current_consensus(discussion_history, alive)
                self._belief_graph.check_redirect(
                    name, action, current_target, alive,
                )
                self._belief_graph.check_evasion(
                    name,
                    action,
                    discussion_history,
                    alive,
                )
                self.gs.evasion_scores = dict(self._belief_graph.evasion_scores)

                self.gs.log(name, agent.role, agent.archetype, reasoning, action)
                self._print(name, agent.role, agent.archetype, reasoning, action,
                            personality=getattr(agent, 'personality', ''))
                discussion_history.append(f"{name}: {action}")

        self.gs.phase = GamePhase.DAY_VOTE
        print_phase_header("DAY VOTE", self.gs.round_number)

        # Summary Agent: display narrative summary before voting
        narrative = self._summary.summarize(self.gs)
        print(narrative)

        await self._narrate("Announce voting time. Players must choose who to eliminate.")
        self._sync_vote_guidance(alive)
        if self._current_vote_shortlist:
            discussion_history.append(
                "[SYSTEM]: Consensus shortlist for this vote: "
                + ", ".join(self._current_vote_shortlist)
                + ". Consolidate unless you have a strong override."
            )

        self.gs.votes = {}
        vote_warnings = await self._collect_votes(alive, discussion_history)

        eliminated = self.gs.tally_votes()
        tied_players = self.gs.get_tied_players()

        # ----------------------------------------------------------------
        #  Tie-Break Protocol (two stages)
        # ----------------------------------------------------------------
        if not eliminated and tied_players:
            print_vote_tally(
                self.gs.votes,
                None,
                weighted_counts=self.gs.get_weighted_vote_counts(),
                warnings=vote_warnings,
            )
            await self._narrate(
                f"The vote is tied between {', '.join(tied_players)}! "
                f"They will now state their defence before a decisive re-vote."
            )

            # Stage 1 — Defence Phase: each tied player gets a turn
            print_phase_header("TIE-BREAK: DEFENCE", self.gs.round_number)
            for name in tied_players:
                if name not in self._agents:
                    continue
                agent = self._agents[name]
                reasoning, action = await agent.day_discussion(
                    self.gs, discussion_history + [
                        f"[SYSTEM]: {name}, you are tied for elimination. "
                        f"State your case to the town."
                    ],
                )
                self.gs.log(name, agent.role, agent.archetype, reasoning, action)
                self._print(name, agent.role, agent.archetype, reasoning, action,
                            personality=getattr(agent, 'personality', ''))
                discussion_history.append(f"{name} (DEFENCE): {action}")

            # Stage 2 — Decisive Vote: everyone *except* tied players
            print_phase_header("TIE-BREAK: DECISIVE VOTE", self.gs.round_number)
            self.gs.votes = {}  # reset for re-vote
            tie_note = (
                "[SYSTEM]: Decisive revote. You must choose among tied players only: "
                + ", ".join(tied_players)
                + "."
            )
            discussion_history.append(tie_note)
            self._sync_vote_guidance(alive, allowed_targets=tied_players)
            vote_warnings = await self._collect_votes(
                list(alive),
                discussion_history,
                allowed_targets=tied_players,
                coordination_note=tie_note,
            )

            eliminated = self.gs.tally_votes()
            tied_again = self.gs.get_tied_players()

            if not eliminated and tied_again:
                evasion_ranked = sorted(
                    tied_again,
                    key=lambda player: (
                        -self._belief_graph.evasion_scores.get(player, 0),
                        player,
                    ),
                )
                if len(evasion_ranked) >= 2 and (
                    self._belief_graph.evasion_scores.get(evasion_ranked[0], 0)
                    > self._belief_graph.evasion_scores.get(evasion_ranked[1], 0)
                ):
                    eliminated = evasion_ranked[0]
                    vote_warnings.append(
                        f"Second tie broken by evasion score: {eliminated} had the highest evasion."
                    )
                else:
                    eliminated = None

        if (
            eliminated == self.detective.name
            and self.detective.name in alive
            and not self.detective.reveal_vote_used
        ):
            self.detective.reveal_vote_used = True
            print_phase_header("DETECTIVE REVEAL WINDOW", self.gs.round_number)
            reasoning, action = await self.detective.reveal_vote_window(
                self.gs,
                discussion_history,
            )
            self.gs.log(
                self.detective.name,
                self.detective.role,
                self.detective.archetype,
                reasoning,
                action,
            )
            self._print(
                self.detective.name,
                self.detective.role,
                self.detective.archetype,
                reasoning,
                action,
                personality=self.detective.personality,
            )
            discussion_history.append(f"{self.detective.name} (REVEAL): {action}")
            self.gs.votes = {}
            reveal_targets = list(alive)
            reveal_note = (
                f"[SYSTEM]: {self.detective.name} used their reveal vote window. "
                "Re-vote now with that information in mind."
            )
            discussion_history.append(reveal_note)
            self._sync_vote_guidance(alive, allowed_targets=reveal_targets)
            vote_warnings = await self._collect_votes(
                alive,
                discussion_history,
                allowed_targets=reveal_targets,
                coordination_note=reveal_note,
            )
            eliminated = self.gs.tally_votes()
            tied_after_reveal = self.gs.get_tied_players()
            if not eliminated and tied_after_reveal:
                evasion_ranked = sorted(
                    tied_after_reveal,
                    key=lambda player: (
                        -self._belief_graph.evasion_scores.get(player, 0),
                        player,
                    ),
                )
                if len(evasion_ranked) == 1 or (
                    self._belief_graph.evasion_scores.get(evasion_ranked[0], 0)
                    > self._belief_graph.evasion_scores.get(evasion_ranked[1], 0)
                ):
                    eliminated = evasion_ranked[0]
                else:
                    eliminated = None

        print_vote_tally(
            self.gs.votes,
            eliminated,
            weighted_counts=self.gs.get_weighted_vote_counts(),
            warnings=vote_warnings,
        )

        if eliminated:
            eliminated_role = self.gs.players[eliminated].role
            self.gs.eliminate_player(eliminated)
            await self._narrate(
                f"{eliminated} eliminated by vote. Role: {eliminated_role}. React dramatically."
            )
        else:
            await self._narrate("Vote tied. Nobody eliminated. Town is nervous.")

        # Reset per-round BeliefGraph tracking (keep cumulative flags)
        self._belief_graph.reset_round()
        self._current_vote_shortlist = []
        self._current_vote_recommendations = {}

    async def _collect_votes(
        self,
        voters: list[str],
        discussion_history: list[str],
        *,
        allowed_targets: list[str] | None = None,
        coordination_note: str = "",
    ) -> list[str]:
        """Run a vote round for *voters*, populating ``self.gs.votes``."""
        alive = self.gs.get_alive_players()
        warnings: list[str] = []
        if not voters:
            warning = "Vote collection received zero eligible voters."
            logger.error(warning)
            warnings.append(warning)
            self._last_vote_warnings = warnings
            return warnings

        if allowed_targets is not None:
            target_pool = [target for target in allowed_targets if target in alive]
        elif self._current_vote_shortlist:
            target_pool = [target for target in self._current_vote_shortlist if target in alive]
        else:
            target_pool = list(alive)

        for name in voters:
            if name not in self._agents or name not in alive:
                continue
            agent = self._agents[name]
            vote_targets = [target for target in target_pool if target != name]
            if not vote_targets:
                vote_targets = [target for target in alive if target != name]
            recommended_target, confidence, basis = self._recommend_vote_target(
                name,
                vote_targets,
            )
            self._current_vote_recommendations[name] = recommended_target or ""
            self._sync_provider_state()
            per_agent_note = coordination_note or self._build_coordination_note(
                name,
                vote_targets,
                recommended_target,
                basis,
                confidence,
            )

            try:
                reasoning, action = await agent.cast_vote(
                    self.gs,
                    discussion_history,
                    allowed_targets=vote_targets,
                    coordination_note=per_agent_note,
                )
            except Exception as exc:
                logger.error("[%s] Vote call failed: %s", name, exc)
                reasoning, action = self._fallback_vote(name, vote_targets)

            belief = self._beliefs.get(name)
            if belief and reasoning:
                updates = parse_belief_updates(reasoning)
                for target, prob in updates.items():
                    belief.update(target, prob)

            self.gs.log(name, agent.role, agent.archetype, reasoning, action)
            self._print(name, agent.role, agent.archetype, reasoning, action,
                        personality=getattr(agent, 'personality', ''))
            vote_target = self._parse_vote(action, vote_targets, name)
            if vote_target is None and reasoning:
                vote_target = self._parse_vote(reasoning, vote_targets, name)
            if vote_target is None:
                self._vote_parse_failures[name] = (
                    self._vote_parse_failures.get(name, 0) + 1
                )

            vote_target, resolution_warning = self._resolve_vote_target(
                name,
                vote_target,
                reasoning or "",
                action or "",
                vote_targets,
                recommended_target,
                confidence,
            )
            if resolution_warning:
                warnings.append(f"{name}: {resolution_warning}")
                logger.warning("[%s] %s", name, resolution_warning)

            if vote_target:
                self.gs.votes[name] = vote_target

                self._belief_graph.check_late_bandwagon(
                    name, vote_target, reasoning or "",
                    self.gs.votes,
                )

                votes_before_current = len(self.gs.votes) - 1
                self._belief_graph.check_instahammer(
                    name,
                    votes_before_current,
                    len(alive),
                )
            else:
                warning = f"{name} produced no valid vote target."
                warnings.append(warning)
                logger.error(warning)

        if not self.gs.votes:
            warning = "Vote collection ended with zero recorded votes."
            logger.error(warning)
            warnings.append(warning)

        self._last_vote_warnings = warnings
        return warnings

    async def _run_night_phase(self) -> None:
        if self.gs.check_win_condition():
            return

        self.gs.phase = GamePhase.NIGHT
        # Clear the day-vote elimination so that only the night kill
        # (if any) is visible to the next day's narrator.
        self.gs.eliminated_this_round = None
        print_phase_header("NIGHT", self.gs.round_number)

        # Summary Agent: display narrative summary at night start
        narrative = self._summary.summarize(self.gs)
        print(narrative)

        await self._narrate("Night falls. Town sleeps. Mafia stirs.")

        alive_mafia   = self.gs.get_alive_mafia()
        targets       = self.gs.get_alive_town()
        mafia_actions: list[str] = []
        final_kill:   str | None = None

        for mafia_agent in self.mafia:
            if mafia_agent.name not in alive_mafia:
                continue
            teammate_reasonings = [
                (other.name, other.last_night_reasoning)
                for other in self.mafia
                if other.name != mafia_agent.name and other.last_night_reasoning
            ]

            try:
                reasoning, action = await mafia_agent.choose_night_kill(
                    self.gs,
                    teammate_actions=mafia_actions,
                    teammate_reasonings=teammate_reasonings,
                )
            except Exception as exc:
                logger.error("[%s] Night kill call failed: %s", mafia_agent.name, exc)
                reasoning, action = self._fallback_night_kill(mafia_agent.name, targets)
            self.gs.log(mafia_agent.name, "Mafia", mafia_agent.archetype, reasoning, action)
            self._print(
                mafia_agent.name, "Mafia", mafia_agent.archetype,
                reasoning, f"[NIGHT TARGET]: {action}",
                personality=mafia_agent.personality,
            )
            # Extract a valid target name from the action text
            parsed_target = self._parse_target(action, targets)
            if not parsed_target and reasoning:
                parsed_target = self._parse_target(reasoning, targets)
            mafia_actions.append(parsed_target or action.strip())
            final_kill = parsed_target

        if final_kill and final_kill in self.gs.get_alive_town():
            self.gs.night_kill_target = final_kill
        elif not final_kill:
            # Fallback only when no valid kill target was parsed at all
            town = self.gs.get_alive_town()
            self.gs.night_kill_target = town[0] if town else None

        if self.detective.name in self.gs.get_alive_players():
            alive = self.gs.get_alive_players()
            eligible = [p for p in alive if p != self.detective.name]
            try:
                reasoning, action = await self.detective.choose_investigation_target(self.gs)
            except Exception as exc:
                logger.error("[%s] Investigation call failed: %s", self.detective.name, exc)
                reasoning, action = self._fallback_investigation(self.detective.name, eligible)
            target = self._parse_target(action, eligible)
            if not target and reasoning:
                target = self._parse_target(reasoning, eligible)
            if not target and eligible:
                target = random.choice(eligible)
                print(
                    f"  [!] {self.detective.name}'s investigation target was unparseable; "
                    f"random fallback -> {target}",
                    file=sys.stderr,
                )
            if target and target in self.gs.players:
                true_role  = self.gs.players[target].role
                result     = "Mafia" if true_role == "Mafia" else "Innocent"
                self.detective.record_finding(target, result)
                self.gs.detective_findings[target] = result
                self.gs.log(self.detective.name, "Detective", self.detective.archetype, reasoning, action)
                self._print(
                    self.detective.name, "Detective", self.detective.archetype,
                    reasoning, f"[INVESTIGATED]: {target} -> {result}",
                    personality=self.detective.personality,
                )

        if self.doctor.name in self.gs.get_alive_players():
            alive = self.gs.get_alive_players()
            valid = [p for p in alive if p != self.gs.last_protected]
            try:
                reasoning, action = await self.doctor.choose_protection_target(self.gs)
            except Exception as exc:
                logger.error("[%s] Protection call failed: %s", self.doctor.name, exc)
                reasoning, action = self._fallback_protection(self.doctor.name, valid)
            protect_target = self._parse_target(action, alive)
            if not protect_target and reasoning:
                protect_target = self._parse_target(reasoning, alive)
            if not protect_target:
                eligible = [p for p in alive if p != self.gs.last_protected]
                if eligible:
                    protect_target = random.choice(eligible)
                    print(
                        f"  [!] {self.doctor.name}'s protection target was unparseable; "
                        f"random fallback -> {protect_target}",
                        file=sys.stderr,
                    )
            # Server-side consecutive-protection enforcement
            if protect_target and protect_target == self.gs.last_protected:
                eligible = [p for p in alive if p != self.gs.last_protected]
                if eligible:
                    protect_target = random.choice(eligible)
                    print(
                        f"  [!] {self.doctor.name} tried to protect the same player "
                        f"two nights in a row; random fallback -> {protect_target}",
                        file=sys.stderr,
                    )
            if protect_target and protect_target in alive:
                self.gs.doctor_protect_target = protect_target
                self.doctor.last_protected = protect_target
            self.gs.log(self.doctor.name, "Doctor", self.doctor.archetype, reasoning, action)
            self._print(
                self.doctor.name, "Doctor", self.doctor.archetype,
                reasoning, f"[PROTECTING]: {protect_target}",
                personality=self.doctor.personality,
            )

        killed, was_protected = self.gs.apply_night_actions()
        killed_role = (
            self.gs.players[killed].role
            if killed and killed in self.gs.players else None
        )
        print_night_result(killed, was_protected, killed_role)

        if was_protected:
            await self._narrate("Dawn. Someone targeted but survived - a mystery protector.")
        elif killed:
            await self._narrate(f"Dawn. {killed} ({killed_role}) found dead.")
        else:
            await self._narrate("A quiet dawn. Nobody died tonight.")

    async def _narrate(self, prompt: str) -> None:
        try:
            reasoning, announcement = await self.narrator.announce(prompt, self.gs)
        except Exception as exc:
            logger.error("[Narrator] Narration failed: %s", exc)
            # Graceful degradation: produce a minimal announcement so the
            # game can continue without a narrator flourish.
            reasoning = ""
            announcement = prompt
        self.gs.log("Narrator", "Narrator", "Impartial", reasoning, announcement)
        self._print("Narrator", "Narrator", "Impartial", reasoning, announcement)

    def _print(
        self,
        name: str, role: str, archetype: str,
        reasoning: str | None, action: str,
        personality: str = "",
    ) -> None:
        display_reasoning = None if self.quiet else reasoning
        print_agent_action(name, role, archetype, display_reasoning, action, not self.debug, personality=personality)

    def _parse_vote(self, action: str, valid_targets: list[str], voter: str) -> str | None:
        """
        Intent-based vote parser with three priority tiers.

        Priority 1: Explicit ``VOTE: {name}`` tags.
        Priority 2: Intent phrases (``I'm voting for …``, ``Staying on …``).
        Priority 3: Last mentioned valid name (avoids parsing addressees).
        Hard filter: A self-vote always returns ``None``.
        """
        text = action.strip()
        if not text:
            return None

        # ------------------------------------------------------------------
        # Priority 1 — explicit VOTE: tag
        # ------------------------------------------------------------------
        vote_tag = re.search(r"VOTE:\s*(\w+)", text, re.IGNORECASE)
        if vote_tag:
            tagged = vote_tag.group(1).strip()
            for target in valid_targets:
                if target.lower() == tagged.lower() and target != voter:
                    return target

        # ------------------------------------------------------------------
        # Priority 1b — serialized tool call traces
        # ------------------------------------------------------------------
        tool_target = self._extract_target_field(text, valid_targets, exclude=voter)
        if tool_target is not None:
            return tool_target

        # ------------------------------------------------------------------
        # Priority 2 — intent phrases
        # ------------------------------------------------------------------
        intent_patterns = [
            r"(?:I(?:'m| am)\s+voting\s+(?:for\s+)?)",
            r"(?:my\s+vote\s+(?:is\s+(?:for\s+)?|goes?\s+to\s+))",
            r"(?:I\s+vote\s+(?:for\s+)?)",
            r"(?:staying\s+on\s+)",
            r"(?:I(?:'m| am)\s+going\s+with\s+)",
            r"(?:locking\s+(?:in\s+)?(?:on\s+)?)",
            r"(?:voting\s+out\s+)",
            r"(?:I\s+cast\s+(?:my\s+)?vote\s+(?:for\s+)?)",
            r"(?:cast_vote\s+on\s+)",
        ]
        combined = "|".join(intent_patterns)
        intent_match = re.search(
            rf"(?:{combined})(\w+)", text, re.IGNORECASE,
        )
        if intent_match:
            candidate = intent_match.group(1).strip()
            for target in valid_targets:
                if target.lower() == candidate.lower() and target != voter:
                    return target

        # ------------------------------------------------------------------
        # Priority 3 — last mentioned valid name
        # ------------------------------------------------------------------
        return self._last_mentioned_valid_name(text, valid_targets, exclude=voter)

    @staticmethod
    def _parse_target(action: str, valid_targets: list[str]) -> str | None:
        """Extract a valid player name from free-form action text."""
        text = action.strip()
        if not text:
            return None
        # Exact match first
        if text in valid_targets:
            return text

        explicit_patterns = [
            r"TARGET:\s*(\w+)",
            r"\[NIGHT TARGET\]:\s*(\w+)",
            r"\[INVESTIGATED\]:\s*(\w+)",
            r"\[PROTECTING\]:\s*(\w+)",
        ]
        for pattern in explicit_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if not match:
                continue
            candidate = match.group(1).strip()
            for target in valid_targets:
                if target.lower() == candidate.lower():
                    return target

        tool_target = MafiaGameOrchestrator._extract_target_field(text, valid_targets)
        if tool_target is not None:
            return tool_target

        intent_patterns = [
            r"(?:choose_target\s+on\s+)",
            r"(?:targeting\s+)",
            r"(?:protecting\s+)",
            r"(?:investigating\s+)",
            r"(?:going\s+with\s+)",
        ]
        combined = "|".join(intent_patterns)
        intent_match = re.search(rf"(?:{combined})(\w+)", text, re.IGNORECASE)
        if intent_match:
            candidate = intent_match.group(1).strip()
            for target in valid_targets:
                if target.lower() == candidate.lower():
                    return target

        return MafiaGameOrchestrator._last_mentioned_valid_name(text, valid_targets)

    @staticmethod
    def _extract_target_field(
        text: str,
        valid_targets: list[str],
        *,
        exclude: str | None = None,
    ) -> str | None:
        """Extract a target from JSON-like tool text such as `{\"target\":\"Bob\"}`."""
        match = re.search(
            r"[\"']target[\"']\s*:\s*[\"'](?P<target>\w+)[\"']",
            text,
            re.IGNORECASE,
        )
        if not match:
            return None
        candidate = match.group("target").strip()
        for target in valid_targets:
            if target.lower() == candidate.lower() and target != exclude:
                return target
        return None

    @staticmethod
    def _last_mentioned_valid_name(
        text: str,
        valid_targets: list[str],
        *,
        exclude: str | None = None,
    ) -> str | None:
        """Return the last whole-word valid target name mentioned in text."""
        best_target: str | None = None
        best_index = -1
        for target in valid_targets:
            if target == exclude:
                continue
            pattern = re.compile(rf"\b{re.escape(target)}\b", re.IGNORECASE)
            for match in pattern.finditer(text):
                if match.start() >= best_index:
                    best_target = target
                    best_index = match.start()
        return best_target

    @staticmethod
    def _get_current_consensus(
        discussion_history: list[str], alive: list[str],
    ) -> str | None:
        """
        Identify the player most frequently mentioned in accusatory
        context across the discussion history. Used by BeliefGraph
        to detect redirects away from the consensus target.
        """
        if not discussion_history:
            return None
        mention_counts: dict[str, int] = {name: 0 for name in alive}
        accusation_words = {
            "suspect", "vote", "sus", "mafia", "guilty", "suspicious",
            "accuse", "hammer", "lynch",
        }
        for line in discussion_history:
            line_lower = line.lower()
            if not any(w in line_lower for w in accusation_words):
                continue
            speaker = line.split(":", 1)[0].strip() if ":" in line else ""
            for name in alive:
                if name.lower() in line_lower and name != speaker:
                    mention_counts[name] += 1
        if not any(mention_counts.values()):
            return None
        return max(mention_counts, key=lambda k: mention_counts[k])
