# Methodology

How MafiaFramework makes AI agents play a social deduction game — and why every design decision exists.

---

## TL;DR

MafiaFramework drops 11 LLM agents into a Mafia game where each agent has a randomised role, strategic archetype, performance personality, and language model. Agents maintain structured belief states (suspicion scores 0.0–1.0) instead of raw free-text reasoning, use middleware for error recovery and rate limiting, and accumulate cross-game memory. The prompt engineering stack layers role goals → reasoning frameworks → archetype modifiers → personality registers → anti-AI constraints so agents sound like distinct humans rather than corporate chatbots. A 3-tier combination ban system prevents broken archetype–personality pairings, and a graduated Iroh Protocol lets at-risk special roles reveal themselves before dying uselessly.

---

## ELI5

Imagine 11 kids playing Mafia at a sleepover, except every kid is actually an AI. Each AI gets a secret role (good guy or bad guy), a personality (are you the quiet one? the drama queen?), and a strategy style (are you paranoid? impulsive? manipulative?). The game engine is like the adult in the room — it tells each AI what's happening, collects their votes, and makes sure nobody cheats. After each game, the AIs remember what worked and what didn't, so they get better over time. The whole system is designed so the AIs argue, accuse, and deceive each other like real players would, not like robots reading from a script.

---

## Table of Contents

- [Design Philosophy](#design-philosophy)
- [The Four Dimensions of Agent Identity](#the-four-dimensions-of-agent-identity)
- [Belief State Architecture](#belief-state-architecture)
- [Prompt Engineering Methodology](#prompt-engineering-methodology)
- [Behavioural Control Systems](#behavioural-control-systems)
- [Error Recovery Philosophy](#error-recovery-philosophy)
- [Vote Coordination Pipeline](#vote-coordination-pipeline)
- [Scum-Tell Detection](#scum-tell-detection)
- [The Iroh Protocol](#the-iroh-protocol)
- [Cross-Game Learning](#cross-game-learning)
- [Combination Ban Methodology](#combination-ban-methodology)
- [Anti-AI Writing Enforcement](#anti-ai-writing-enforcement)
- [Graceful Degradation](#graceful-degradation)
- [Session Resilience](#session-resilience)

---

## Design Philosophy

MafiaFramework is built on three core principles:

### 1. Emergent Behaviour Over Scripted Interaction

Agents are not given scripts or decision trees. They receive:
- A role goal (what winning looks like)
- Reasoning frameworks (how to think about the game)
- An archetype (how their reasoning deviates from optimal play)
- A personality (how they talk)

The actual game dynamics — alliances, accusations, betrayals — emerge from the interaction of these layers during play. No two games are the same because every dimension is randomised independently.

### 2. Structured Intuition Over Bayesian Inference

Agent belief tracking is **not** Bayesian probability. It is "structured intuition" — the LLM assigns suspicion scores (0.0–1.0) based on conversational evidence, then the system uses those scores for downstream decisions (vote guidance, Iroh Protocol triggers, overconfidence gating). This is a deliberate design choice:
- LLMs are bad at rigorous probability math
- LLMs are good at "this person seems suspicious because X"
- The system anchors that intuition in a numeric format so it can be reasoned about programmatically

### 3. Controlled Chaos

Every archetype introduces a specific failure mode — Paranoid agents see threats that don't exist, Impulsive agents act before thinking, Contrarian agents oppose consensus even when it's correct. These failures are intentional. Perfect play is boring. Mechanically suboptimal play that mirrors human cognitive biases produces vastly more interesting games.

---

## The Four Dimensions of Agent Identity

Each agent's behaviour is the product of four independently randomised dimensions:

### Role (Strategic Objective)

| Role      | Count | Goal                                        | Special Abilities                      |
|-----------|-------|---------------------------------------------|----------------------------------------|
| Mafia     | 2     | Eliminate Town players without being caught  | Night kill, knows partner identity     |
| Detective | 1     | Find Mafia through investigation            | Night investigation, weighted vote (2×) |
| Doctor    | 1     | Protect Town from Mafia kills               | Night protection (no repeat target)    |
| Villager  | 7     | Identify and eliminate Mafia through voting  | None (pure social deduction)           |

### Model (Cognitive Engine)

Each agent is backed by a randomly selected Azure AI model deployment. When multiple models are available, the same game may have agents running on GPT-4o, GPT-4o-mini, and others simultaneously. This creates natural cognitive diversity — different models reason differently even with identical prompts.

### Archetype (Strategic Deviation)

13 archetypes define *how the agent deviates from optimal play*. This is the key insight — archetypes don't describe how agents play well, they describe how agents play *imperfectly in specific, human-like ways*.

**Full archetype list:** Paranoid, Overconfident, Impulsive, Passive, Reactive, Contrarian, Analytical, Diplomatic, Stubborn, Volatile, Manipulative, Charming, Methodical (Villager-only).

Each archetype includes:
- **Strategy modifier** — How reasoning deviates (e.g., Paranoid perceives threats at 2× actual level)
- **Voice profile** — Register, prohibited phrases, example speech patterns
- **IRRATIONAL ACTOR tendency** — Some archetypes (Paranoid, Impulsive, Volatile, Contrarian) produce unpredictable, human-like chaos

### Personality (Performance Layer)

8 personalities define *how the agent sounds externally*. Personality has **zero** effect on strategy — it is purely a communication layer.

**Full personality list:** TheGhost, TheAnalyst, TheConfessor, TheParasite, TheMartyr, ThePerformer, VibesVoter, MythBuilder.

Each personality includes:
- **Register** — Energy, cadence, sentence rhythm
- **Voice markers** — `sentence_length`, `evidence_relationship`, `deflection_style`
- **Prohibited phrases** — Things this voice never says
- **Example dialogue** — 5 in-game lines showing the voice in practice
- **When accused** responses — 3 lines for when directly targeted
- **Late game shift** — How behaviour changes in rounds 3+
- **Role notes** — Mafia vs Town differences
- **Performance notes** — How the personality wraps the archetype's strategy

**Why separate archetype and personality?** Because internal reasoning and external presentation should be independent. A Paranoid agent with TheGhost personality reasons anxiously but speaks minimally. A Paranoid agent with TheConfessor personality reasons anxiously but speaks in rapid-fire declarations. Same strategy, completely different table presence.

---

## Belief State Architecture

### SuspicionState

Each agent maintains a `SuspicionState` — a dictionary mapping every other player's name to a suspicion probability (0.0–1.0). This is initialised with a uniform prior based on the number of Mafia members and updated throughout the game.

**Update mechanism:** Agents embed `BELIEF_UPDATE: PlayerName=0.XX because [evidence]` tags in their reasoning. The `BeliefUpdateMiddleware` extracts these, and the orchestrator applies them to the agent's `SuspicionState`. Agents are instructed that updates are optional ("MAY include", not "MUST audit") to prevent formulaic reasoning.

**Archetype-specific update modulation:**
- **High threshold (0.15):** Overconfident, Stubborn — only strong evidence moves their beliefs
- **Low threshold (0.05):** Volatile, Reactive — any new information shifts their beliefs
- **Explicit reasoning required:** Analytical, Methodical — must articulate the reasoning chain

### Staleness Detection

If an agent's beliefs change by less than 0.05 total for 2+ consecutive rounds, a `FRUSTRATION STATE` fires. The agent receives explicit instructions to break its reasoning loop by:
- Naming a new suspect
- Challenging a quiet player
- Sharing information it has been holding back

This prevents agents from entering conversational death spirals where they repeat the same positions forever.

### Overconfidence Gating

When an Overconfident archetype's top suspect is below 70% certainty, the system softens their declarative accusations. "Bob is definitely Mafia" becomes "Bob (I think) is Mafia" until certainty rises above the threshold. This prevents the model from being more confident in output than the belief state warrants.

---

## Prompt Engineering Methodology

System prompts are assembled in `prompts/builder.py` from 9 layers, applied in order:

### Layer 1: Role Goal
First-person mandate explaining what winning looks like for this specific role. Written in second person to create role immersion.

### Layer 2: Grounding Constraint
Anti-confabulation rule preventing reference to events not in the discussion history. Prevents agents from inventing past conversations or "remembering" things that never happened.

### Layer 3: Conversational Rule
8 rules forcing genuine conversation:
1. Respond to the last speaker
2. Use names and second person
3. Make claims, not just questions
4. No pile-on echoing
5. Disagree out loud
6. Move the conversation forward
7. Show-don't-tell (describe behaviour, don't label it)
8. Own-read-first (state your own position before referencing others)

### Layer 4: Reasoning Frameworks
Reusable strategic thinking modules, routed per role and archetype:

| Framework           | Core Content                                                         |
|---------------------|----------------------------------------------------------------------|
| Game Theory         | Threat ranking, information asymmetry, timing optimisation           |
| Sun Tzu             | Deception economy, intelligence targeting, terrain awareness         |
| Machiavelli         | Coalition building, appearance management, necessity-guided action   |
| Carnegie Execution  | Social influence, indirect persuasion, challenge absorption          |
| Carnegie Villager   | Trust-building, interaction-based people-reading, social consensus   |
| Behavioural Psych   | Cognitive biases: anchoring, herding, loss aversion, overconfidence  |
| Strategic Glossary  | Competitive Mafia terminology (Busing, Instahammer, Wagon-steering)  |
| Incentive Reasoning | Who benefits from each elimination?                                   |
| Self-Critique       | Reflexion loop: tunneling check, circular reasoning check            |

**Framework routing:** Each role gets a base set of frameworks. Archetypes and personalities can add extras:
- Contrarian → dialectical-materialism
- Analytical/Methodical → systems-theory
- Diplomatic/Charming → carnegie-interpersonal
- TheGhost → sun-tzu-strategy
- ThePerformer/MythBuilder/TheMartyr → universal-storytelling

### Layer 5: Role-Specific Protocols

**Mafia:**
- **Deception Layer** — Instructions for maintaining Town cover
- **Syndicate Channel** — Partner coordination (injected teammate reasoning from previous night)
- **Mafia Threat Check** — 4 mandatory pre-reasoning questions:
  1. Am I personally under suspicion?
  2. Is my partner under suspicion?
  3. Who is the biggest threat to Mafia **among Town players**? (partner explicitly excluded)
  4. Is my cover story still consistent?
- **Solo 5th Question** (when partner eliminated): Which player is most likely to identify me?

**Detective:**
- **Claim Protocol** — Mandatory red-check announcement
- **Iroh Protocol** — Graduated identity reveal (see below)
- **Red Check Reveal Strategy** — When and how to share investigation results
- **Vote Pattern Analysis** — Lone divergent vote detection, voting bloc tracking

**Doctor:**
- **Value-Protection Heuristic** — Protect the reasoner (evidence-based predictions, bandwagon resistance), not the loudest voice
- **Iroh Protocol** — Graduated identity reveal

**Villager:**
- **Voter Consistency** — Anti-Mafia-steering tool: track vote blocs, last-moment switches, lone divergent votes

### Layer 6: Archetype Strategy Modifier
Role-specific behavioural deviation injected from `prompts/archetypes.py`.

### Layer 7: Voice / Personality Block
The personality's register, voice markers, prohibited phrases, example dialogue, and performance notes from `prompts/personalities.py`. Also includes the GenZ/MLE slang register.

### Layer 8: Self-Critique
Reflexion loop checking for tunneling, circular reasoning, and susceptibility to manipulation.

### Layer 9: Output Format
Mandatory `REASONING:` / `ACTION:` structure with explicit self-vote prevention.

### Discussion Rules

A separate `DISCUSSION_RULES` constant is injected into all discussion-phase prompts:
- No vote declarations during discussion phase
- Specific-claim engagement required
- Oblique suspicion (describe behaviour, don't label)
- Own-read-first (state position before referencing others)
- No consensus echoing
- Show-don't-tell

---

## Behavioural Control Systems

### Corporate-Speak Enforcement

A middleware layer (`corporate_speak_middleware`) detects corporate/boardroom language in agent output. If an action contains 3+ words from the `CORPORATE_WORDS` list (e.g., "synergy", "leverage", "actionable"), the response is re-invoked with a `CORPORATE_ENFORCEMENT_HINT` that pushes the agent toward natural slang.

### GenZ/MLE Slang Register

`GENZ_REGISTER` in `prompts/archetypes.py` contains ~80 Multicultural London English and Gen Z slang terms. These are injected into both voice blocks and personality blocks. Agents are instructed to use 1–3 terms per message naturally, not as decoration.

### Anti-AI Structure Rules

11 structural rules prevent AI-typical writing patterns:
- No rule of three
- No trailing -ing clauses
- No emoji
- No "As an AI" or "I don't have feelings but"
- No capitalised emphasis
- No rhetorical questions that answer themselves

### Negative Constraints

80+ banned AIism phrases sourced from common AI writing patterns:
- "It's worth noting"
- "Additionally"
- "Essentially"
- "Let me know"
- "I'd be happy to"
- And 75+ more

---

## Error Recovery Philosophy

The system uses a four-phase error recovery hierarchy:

### Phase 1: Streaming Retry
Agent calls use streaming by default. On failure:
- Corporate-speak detection and retry (with slang hint)
- Refusal detection and retry (with softened prompt)
- Empty action detection and retry
- Up to 2 retries total

### Phase 2: Non-Streaming Fallback
If streaming fails, fall back to a non-streaming call. Some models handle non-streaming more reliably.

### Phase 3: Session Reconstruction
If the server returns `previous_response_not_found` (session TTL expired), the `ResilientSessionMiddleware`:
1. Extracts conversation history from `InMemoryHistoryProvider`
2. Creates a fresh `AgentSession` with transferred state
3. Injects a compressed history summary
4. Retries the call

### Phase 4: Graceful Degradation
If all API-based recovery fails, per-phase fallback methods produce reasonable default behaviour:

| Phase             | Fallback Behaviour                                                    |
|-------------------|-----------------------------------------------------------------------|
| Discussion        | "I'll listen for now" — passes turn                                  |
| Voting            | Votes for highest-suspicion player from belief state                 |
| Mafia Night Kill  | Targets lowest-suspicion Town player (biggest threat)                |
| Investigation     | Investigates most suspicious unchecked player                        |
| Protection        | Self-protects if suspicion > 0.3, otherwise random ally              |

---

## Vote Coordination Pipeline

Vote coordination prevents the "everyone votes randomly" failure mode without scripting outcomes.

### Step 1: Room Suspicion Aggregation
`_compute_room_suspicion()` aggregates suspicion across all agents with:
- Weighted average of per-agent suspicion scores
- Evasion bonus (`MAFIA_EVASION_BONUS = 0.08`) for players flagged for evasion
- Red-check overrides for Detective findings

### Step 2: Shortlist Construction
`_build_vote_shortlist()` returns the top N (`MAFIA_CONSENSUS_SHORTLIST_SIZE = 3`) most-suspected players.

### Step 3: Per-Voter Recommendation
`_recommend_vote_target()` generates role-aware recommendations:
- **Mafia:** Consensus target, excluding partners
- **Detective:** Red-check target if available, else belief-based
- **Town:** Belief-based if confident (> `MAFIA_VOTE_CONFIDENCE_THRESHOLD = 0.45`), else consensus

### Step 4: Coordination Note
`_build_coordination_note()` builds a human-readable vote guidance string injected into the agent's context. This is guidance, not a mandate — agents can and do override it based on their archetype (Contrarian agents frequently reject recommendations).

### Step 5: Resolution
`_resolve_vote_target()` applies engine-side overrides when the agent's parsed vote doesn't match a high-confidence recommendation. This is a safety net, not a primary control mechanism.

---

## Scum-Tell Detection

The `BeliefGraph` runs three behavioural pattern detectors each round:

### Late Bandwagon
**Trigger:** A player votes with the majority without offering new reasoning.
**Signal:** Possible Mafia following Town consensus to avoid standing out.
**Injection:** Flag surfaced to all agents so they can reason about it.

### Redirect
**Trigger:** A player deflects attention from the consensus target onto a quiet player.
**Signal:** Possible Mafia protecting a partner by creating a new target.
**Injection:** Flag surfaced with the specific redirect details.

### Instahammer
**Trigger:** A player casts a decisive vote before meaningful discussion has occurred.
**Signal:** Possible Mafia trying to force a quick elimination.
**Injection:** Flag surfaced with the timing details.

### Evasion Detection
**Trigger:** A player is directly questioned by another player and responds without addressing the question.
**Signal:** Possible Mafia avoiding incriminating answers.
**Tracking:** Per-player evasion scores accumulate on `game_state.evasion_scores` and add to room suspicion via `MAFIA_EVASION_BONUS`.

---

## The Iroh Protocol

Named after Uncle Iroh from Avatar — the wise character who reveals his true power only when absolutely necessary.

### Problem
Detectives and Doctors who die without revealing their role waste their team's most valuable information. But revealing too early makes them an instant Mafia target.

### Solution: Graduated Reveal

The system monitors how suspicious other agents collectively are of a Detective or Doctor. When average suspicion exceeds a threshold, the system injects role-reveal instructions into the agent's context:

| Level        | Threshold | Instruction                                                        |
|--------------|-----------|--------------------------------------------------------------------|
| Soft Hint    | 0.35      | Hint at having information without revealing role                  |
| Hard Claim   | 0.45      | Conditionally claim role ("I am X, I will prove it if needed")     |
| Full Reveal  | 0.55      | Immediately reveal role with all evidence                          |

### Red-Check Adjustment

If the Detective holds an unshared Mafia finding (red check), all thresholds are lowered by 0.10. The system prioritises getting critical information out before the Detective dies.

---

## Cross-Game Learning

### GameMemoryStore

The `GameMemoryStore` persists agent learnings across games as JSON files in the `memory/` directory:

| File                        | Contents                              |
|-----------------------------|---------------------------------------|
| `detective_learnings.json`  | Detective-specific insights           |
| `doctor_learnings.json`     | Doctor-specific insights              |
| `mafia_learnings.json`      | Mafia-specific insights               |
| `villager_learnings.json`   | Villager-specific insights            |
| `global_patterns.json`      | Cross-role game patterns              |

### Learning Structure

Each learning contains:
- **insight** — What was learned
- **context** — The game situation that produced it
- **role** — Which role generated it
- **round_number** — When in the game
- **outcome** — "correct", "incorrect", or "unknown"
- **timestamp** — ISO 8601

### Injection

`CrossGameMemoryProvider` injects the 5 most recent role-specific learnings plus 5 most recent global patterns into each agent's context before every turn.

### Capacity Management

Maximum 50 learnings per role. Oldest are dropped when the cap is reached.

---

## Combination Ban Methodology

### Why Combinations Matter

An archetype controls *how the agent thinks*. A personality controls *how the agent talks*. When both push in the same direction, the agent loses its internal tension — the thing that makes it interesting and human-like.

### Tier 1: Structural Collapse (Hard Ban — All Roles)

Combinations where the archetype's failure mode and the personality's communication style produce non-functional agents:

| Archetype | Banned Personality | Failure                                        |
|-----------|--------------------|------------------------------------------------|
| Reactive  | VibesVoter         | Panic state + no logic vocabulary → one-word responses |

### Tier 2: Absence of Self (Ban for Power Roles and Mafia)

Combinations that remove independent judgment. Functional for Villager but broken for roles requiring independent decision-making:

| Archetype  | Banned Personality | Banned Roles             | Failure                                           |
|------------|--------------------|--------------------------|----------------------------------------------------|
| Diplomatic | TheParasite        | Detective, Doctor, Mafia | No internal position + consensus mirroring → cannot decide or deceive |

### Tier 3: Existing Bans (All Roles)

| Archetype     | Banned Personality | Failure                                              |
|---------------|--------------------|------------------------------------------------------|
| Passive       | MythBuilder        | Reasoning without decisions                          |
| Passive       | TheGhost           | Double silence                                       |
| Overconfident | TheParasite        | Redundant lock-on without internal contrast          |
| Stubborn      | MythBuilder        | Debate-proof anchoring with narrative cover           |
| Diplomatic    | TheConfessor       | Double softness — agent becomes invisible            |
| Manipulative  | ThePerformer       | Self-referential dramatic stances → self-voting      |

### Frequency Caps

- **Base cap (2/game):** No personality appears more than twice, preventing homogeneous rooms
- **Consensus cap (1/game):** TheParasite, TheConfessor, ThePerformer, MythBuilder — consensus-amplifying personalities limited to one per game
- **Cap relaxation:** When exclusions + caps exhaust all valid personalities (possible with 11 players and 8 personalities), the frequency cap is relaxed while hard exclusions are preserved. This prevents game creation crashes.

### Independent Archetype Floor

At least 2 players per game must have an independent-reasoning archetype (Contrarian, Analytical, Impulsive, Stubborn). If random assignment falls short, one assignment is re-rolled. This ensures every game has enough consensus resistance for meaningful deduction.

---

## Anti-AI Writing Enforcement

### Problem

LLMs default to corporate, formal, AI-identifiable writing. In a social deduction game, this breaks immersion and makes all agents sound identical.

### Solution Stack

1. **Negative Constraints (80+ banned phrases):** Removes the most common AI-isms ("It's worth noting", "Additionally", "Essentially")
2. **Anti-AI Structure (11 rules):** Prevents structural patterns (rule-of-three, trailing -ing clauses, self-referential "As an AI")
3. **GenZ/MLE Slang Register (~80 terms):** Provides natural-sounding alternatives from Multicultural London English and Gen Z vocabulary
4. **Corporate-Speak Penalty (20 words):** Runtime middleware catches boardroom language and forces retry with slang enforcement
5. **Voice Profiles (per archetype):** Each archetype has specific register and prohibited phrases
6. **Voice Markers (per personality):** Each personality has `sentence_length`, `evidence_relationship`, and `deflection_style` parameters
7. **Grounding Constraint:** Forces first-person perspective to prevent "as an outside observer" detachment

### Runtime Enforcement

The `corporate_speak_middleware` is not just a prompt instruction — it's a runtime check. If an agent's action text contains 3+ corporate words after generation, the entire call is re-invoked with a `CORPORATE_ENFORCEMENT_HINT`. This double-layer (prompt-time + runtime) approach catches cases where the prompt alone isn't enough.

---

## Graceful Degradation

### Philosophy

The system should never crash mid-game due to API failures. A degraded game is better than no game.

### Implementation

Every game phase has a `_fallback_*` method in `engine/orchestrator.py` that produces reasonable default behaviour when the API call fails entirely:

| Phase             | Fallback                                                                |
|-------------------|-------------------------------------------------------------------------|
| Discussion        | Agent passes turn with "I'll listen for now"                           |
| Voting            | Vote for highest-suspicion target from belief state                    |
| Mafia Night Kill  | Target lowest-suspicion Town player (biggest strategic threat)          |
| Investigation     | Investigate most suspicious unchecked player                           |
| Protection        | Self-protect if own suspicion > 0.3, otherwise protect random ally     |

These fallbacks are belief-state-aware — they use the agent's accumulated suspicion data to make reasonable decisions rather than purely random choices.

---

## Session Resilience

### Problem

Azure AI Foundry sessions have a TTL. Long games (10+ rounds) or slow API responses can cause sessions to expire mid-game, resulting in `previous_response_not_found` errors.

### Solution: Three-Layer Session Management

#### Layer 1: ResilientSessionMiddleware
Catches session expiration errors and rebuilds from `InMemoryHistoryProvider`:
1. Extract conversation history from local cache
2. Create fresh `AgentSession` with all state transferred
3. Inject compressed history summary into new session
4. Retry the failed call

#### Layer 2: RateLimitMiddleware
Handles 429 (rate limit) errors with:
- Exponential backoff: 1s → 2s → 4s → 8s + random jitter
- Up to 3 retries per call
- Proactive session refresh when cumulative backoff delay exceeds `MAFIA_SESSION_REFRESH_THRESHOLD` (25s)
- Pre-call idle check: if session has been idle > `MAFIA_SESSION_IDLE_THRESHOLD` (20s), proactively refresh before the call

#### Layer 3: SessionHealthMonitor
Tracks per-session last-call timestamps:
- `touch(session_id)` — Records when a call was made
- `idle_seconds(session_id)` — Reports how long since the last call
- Used by `RateLimitMiddleware` for proactive refresh decisions

### Middleware Order

Middleware is registered in a specific order on every player agent:
1. `ResilientSessionMiddleware` — outermost (catches all errors)
2. `RateLimitMiddleware` — handles 429s
3. `corporate_speak_middleware` — slang enforcement
4. `ReasoningActionMiddleware` — output parsing
5. `BeliefUpdateMiddleware` — belief extraction

The outermost middleware wraps all others, ensuring session recovery catches errors from any inner layer.
