# MafiaFramework

An AI-powered simulation of the social deduction game **Mafia**, built on the [Microsoft Agent Framework (MAF)](https://github.com/microsoft/agent-framework) and [Azure AI Foundry](https://azure.microsoft.com/products/ai-studio). Eleven AI agents — each with a unique archetype, personality, randomly assigned role, and independently chosen language model — play a full game of Mafia against each other in the terminal.

---

## Table of Contents

- [Overview](#overview)
- [How the Game Works](#how-the-game-works)
- [Architecture](#architecture)
- [Project Structure](#project-structure)
- [Archetypes](#archetypes)
- [Personalities](#personalities)
- [Combination Constraints](#combination-constraints)
- [Agent Intelligence Systems](#agent-intelligence-systems)
- [Rate Limiting](#rate-limiting)
- [Session Resilience](#session-resilience)
- [Graceful Degradation](#graceful-degradation)
- [Prompt Engineering](#prompt-engineering)
- [Prerequisites](#prerequisites)
- [Setup](#setup)
- [Usage](#usage)
- [Configuration](#configuration)
- [Testing](#testing)
- [Troubleshooting](#troubleshooting)

---

## Overview

MafiaFramework simulates full Mafia games where every participant is an LLM agent. Each game randomises four independent dimensions:

| Dimension       | Description                                                            |
|-----------------|------------------------------------------------------------------------|
| **Role**        | Mafia (×2), Detective, Doctor, Villager (×7) — shuffled each game      |
| **Model**       | Each agent is backed by a randomly selected Azure model deployment     |
| **Archetype**   | One of 13 strategy archetypes that shape how the agent reasons         |
| **Personality** | One of 8 performance personalities that shape how the agent communicates|

The same player name gets a different combination every game, producing emergent and unpredictable social dynamics. An exclusion system prevents mechanically broken archetype–personality combinations from being assigned.

---

## How the Game Works

A standard Mafia game loop runs as follows:

### Day Phase
1. **Discussion** — All alive players speak (two rounds of shuffled speaking order). Each agent sees the public game state and the conversation so far.
2. **Voting** — Each player votes to eliminate one other player. A simple plurality eliminates the target.

#### Tie-Break Protocol
When votes tie, a two-stage tie-break fires:
1. **Defence** — Tied players speak in their own defence.
2. **Decisive Vote** — All non-tied players re-vote on only the tied candidates.

If the re-vote also ties, no one is eliminated that round.

#### Vote Parsing
The vote parser uses a three-tier intent system:
1. Explicit `VOTE:` tags
2. Intent phrases ("I'm voting for…", "staying on…", "locking in on…")
3. Last-mentioned valid player name

Self-votes are always rejected. When parsing fails entirely, a random eligible target is assigned as a fallback — the log includes the raw unparsed text for diagnosability.

### Night Phase
3. **Mafia Kill** — The two Mafia agents independently choose a kill target. The second Mafia member sees the first's preference as a coordination hint. Each receives their partner's reasoning from the previous night via the Syndicate Channel.
4. **Detective Investigation** — The Detective chooses one player to investigate, learning whether they are Mafia or Innocent.
5. **Doctor Protection** — The Doctor chooses one player to protect (cannot repeat the same player two nights in a row). If the Mafia's target matches the Doctor's protection, the kill is blocked.
6. **Dawn** — Night actions resolve. The Narrator announces the result.

Night actions execute in fixed order: Mafia → Detective → Doctor.

### Win Conditions
- **Town wins** when all Mafia members are eliminated.
- **Mafia wins** when Mafia members equal or outnumber Town players.

An impartial **Narrator** agent (with omniscient knowledge of all roles) announces phase transitions dramatically without leaking hidden information. The Night Anonymity Rule prevents the Narrator from naming any living player during night announcements.

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                       main.py                           │
│            CLI, argument parsing, game loop              │
└──────────────────────┬──────────────────────────────────┘
                       │
         ┌─────────────▼─────────────┐
         │   engine/game_manager.py  │
         │   Role/Model/Archetype/   │
         │   Personality assignment   │
         │   + exclusion constraints  │
         └─────────────┬─────────────┘
                       │
         ┌─────────────▼─────────────┐
         │  engine/orchestrator.py   │
         │  Game loop: day/night     │
         │  phases, belief tracking, │
         │  win detection, graceful  │
         │  degradation fallbacks    │
         └──┬──────────┬─────────┬───┘
            │          │         │
    ┌───────▼──┐ ┌─────▼───┐ ┌──▼────────┐
    │  agents/ │ │ engine/ │ │  prompts/ │
    │ Per-role │ │ State,  │ │ Frameworks│
    │ AI agent │ │ logging │ │ Archetypes│
    │ classes, │ │ display │ │ Personal. │
    │ belief,  │ │         │ │ Builder   │
    │ memory,  │ │         │ │           │
    │ tools    │ │         │ │           │
    └──┬───────┘ └─────────┘ └───────────┘
       │
    ┌──▼───────────────────────────────┐
    │  agents/middleware.py            │
    │  ResilientSession → RateLimit → │
    │  CorporateSpeak → Reasoning →   │
    │  BeliefUpdate middleware chain   │
    │  + SessionHealthMonitor          │
    └──┬───────────────────────────────┘
       │
    ┌──▼───────────────┐
    │ agents/           │
    │ rate_limiter.py   │
    │ Global semaphore, │
    │ backoff, retries  │
    └──┬───────────────┘
       │
    ┌──▼──────────────┐
    │ config/          │
    │ model_registry   │
    │ settings         │
    └──────────────────┘
```

### Key Components

| Module                       | Purpose                                                                                      |
|------------------------------|----------------------------------------------------------------------------------------------|
| `main.py`                    | Entry point. Parses CLI args, validates environment, runs one or more games.                  |
| `engine/orchestrator.py`     | Game loop — runs day discussion, voting (with tie-break), night actions, belief tracking, and win-condition checks. |
| `engine/game_manager.py`     | Randomises roles, models, archetypes, and personalities. Enforces exclusion constraints. Instantiates all agents. |
| `engine/game_state.py`       | Core data model: player state, phase tracking, vote tallying, night action resolution.       |
| `engine/game_log.py`         | Terminal renderer with ANSI colours — banners, agent action boxes, vote tallies, results.    |
| `agents/base.py`             | Shared utilities: response parsing (`REASONING:`/`ACTION:`), streaming, retry logic, corporate-speak enforcement. |
| `agents/belief_state.py`     | Suspicion tracking per agent, overconfidence gating, staleness detection, BeliefGraph scum-tell detection, temporal consistency checking. |
| `agents/summary.py`          | SummaryAgent — generates low-cognitive-load narrative summaries with recency-weighted target identification. |
| `agents/providers.py`        | MAF ContextProviders — `BeliefStateProvider` and `CrossGameMemoryProvider` inject dynamic context into each agent call. |
| `agents/rate_limiter.py`     | Two-tier rate limiting — global asyncio semaphore, exponential backoff with jitter, error-aware retry logic (429 retry, 5xx fail fast, timeout retry once). |
| `agents/middleware.py`       | MAF middleware — `ResilientSessionMiddleware` (session recovery), `RateLimitMiddleware` (429 handling + proactive refresh), `corporate_speak_middleware` (slang enforcement), `ReasoningActionMiddleware` (REASONING/ACTION parsing), `BeliefUpdateMiddleware` (BELIEF_UPDATE extraction), `SessionHealthMonitor` (per-session idle tracking). |
| `agents/game_tools.py`       | MAF `@tool`-decorated functions — `cast_vote` and `choose_target` for structured game actions. |
| `agents/memory.py`           | Persistent cross-game memory — role-aware learnings stored as JSON, loaded each game.        |
| `agents/narrator.py`         | Narrator agent — omniscient, impartial game master announcements.                            |
| `agents/mafia.py`            | Mafia agent — day discussion, voting, night kill target, Syndicate coordination.             |
| `agents/detective.py`        | Detective agent — day discussion, voting, night investigation, Iroh Protocol reveal.         |
| `agents/doctor.py`           | Doctor agent — day discussion, voting, night protection, Value-Protection Heuristic.         |
| `agents/villager.py`         | Villager agent — day discussion, voting, Voter Consistency tracking.                         |
| `config/model_registry.py`   | Model pool definition and Azure Foundry client factory.                                      |
| `config/settings.py`         | Loads environment variables for the Foundry endpoint and default model.                      |
| `prompts/builder.py`         | Assembles system prompts from role goals, frameworks, archetype modifiers, personality blocks, and mandatory role-specific protocols. |
| `prompts/frameworks.py`      | Reusable reasoning frameworks: game theory, Sun Tzu, Machiavelli, Carnegie, behavioural psych, strategic glossary, incentive reasoning, self-critique. |
| `prompts/archetypes.py`      | 13 strategy archetypes, anti-AI-writing constraints, GenZ/MLE slang register, corporate-speak penalty, conversational rules. |
| `prompts/personalities.py`   | 8 performance personalities with register, prohibited phrases, example dialogue, and role-aware behaviour notes. |
| `check.py`                   | Pre-flight connectivity check — verifies Azure Foundry setup before running a game.          |

---

## Project Structure

```
MafiaFramework/
├── main.py                     # Entry point
├── check.py                    # Connectivity pre-check
├── requirements.txt            # Python dependencies
├── agents/
│   ├── __init__.py
│   ├── base.py                 # Shared streaming + parsing + retry utilities
│   ├── belief_state.py         # Suspicion tracking, BeliefGraph, temporal checks
│   ├── summary.py              # SummaryAgent — narrative summaries per phase
│   ├── providers.py            # MAF ContextProviders (belief + memory injection)
│   ├── middleware.py           # MAF middleware (session resilience, rate limiting, corporate-speak, REASONING/ACTION parsing, belief updates)
│   ├── rate_limiter.py         # Two-tier rate limiting (semaphore + backoff + error classification)
│   ├── game_tools.py           # @tool-decorated game actions (vote, target)
│   ├── memory.py               # Cross-game persistent memory
│   ├── narrator.py             # Narrator agent
│   ├── mafia.py                # Mafia agent
│   ├── detective.py            # Detective agent
│   ├── doctor.py               # Doctor agent
│   └── villager.py             # Villager agent
├── engine/
│   ├── __init__.py
│   ├── game_manager.py         # Game setup, randomisation, exclusion constraints
│   ├── game_state.py           # State model & game logic
│   ├── orchestrator.py         # Game loop controller
│   └── game_log.py             # Terminal display / ANSI rendering
├── config/
│   ├── __init__.py
│   ├── settings.py             # Environment variable loader
│   └── model_registry.py       # Model pool & client factory
├── prompts/
│   ├── __init__.py
│   ├── builder.py              # System prompt assembler
│   ├── frameworks.py           # Reasoning framework text blocks
│   ├── archetypes.py           # Strategy archetype definitions + voice rules
│   └── personalities.py        # Performance personality definitions
├── tests/
│   ├── __init__.py
│   └── test_refactor.py        # 149 tests across 41 test classes
└── memory/                     # Cross-game learnings (gitignored)
```

---

## Archetypes

Each player is assigned a random **archetype** that governs how the agent reasons and makes decisions internally. There are 13 archetypes defined in `prompts/archetypes.py`:

| Archetype       | Strategy Tendency                                                       | Availability   |
|-----------------|-------------------------------------------------------------------------|----------------|
| **Paranoid**    | Perceives threats at twice the actual level; occasional panic spirals    | All roles      |
| **Overconfident** | First read is final; rarely updates on new information               | All roles      |
| **Impulsive**   | Acts on first instinct; occasionally brilliant, often premature         | All roles      |
| **Passive**     | Requires overwhelming evidence; acts a round later than optimal          | All roles      |
| **Reactive**    | Accusations override strategic calculation; easy to bait                 | All roles      |
| **Contrarian**  | Instinctively questions strong consensus, even when correct              | All roles      |
| **Analytical**  | Closest to optimal play; failure mode is predictability                  | Non-Villager   |
| **Methodical**  | Evidence-based but slow; anchors on early reads                          | Villager only  |
| **Diplomatic**  | Prioritises group harmony; softens accusations into suggestions          | All roles      |
| **Stubborn**    | Round-one read is load-bearing; treats counter-evidence as misdirection  | All roles      |
| **Volatile**    | Position shifts with the last compelling thing heard; chaos agent         | All roles      |
| **Manipulative**| Engineers group conclusions through leading questions                     | All roles      |
| **Charming**    | Builds specific, genuine-seeming warmth rapidly                          | All roles      |

Each archetype includes:
- A **strategy modifier** that changes how the agent deviates from optimal play
- A **voice profile** with register description, prohibited AI-writing patterns, and example phrases
- An **IRRATIONAL ACTOR** tendency on some archetypes (Paranoid, Impulsive, Volatile, Contrarian) that produces unpredictable, human-like chaos

The same archetype on different roles produces completely different gameplay. A Paranoid Mafia member behaves very differently from a Paranoid Villager.

---

## Personalities

Each player also receives a **personality** — a performance layer that governs how the agent communicates externally, independent of its strategic archetype. There are 8 personalities defined in `prompts/personalities.py`:

| Personality       | Communication Style                                                      |
|-------------------|--------------------------------------------------------------------------|
| **TheGhost**      | Minimal output. Short declaratives. Speaks last and least, lands hardest.|
| **TheAnalyst**    | Full sentences with count framing. Escalates from measured to exasperated.|
| **TheConfessor**  | High velocity ADHD energy. Bold declarations, partial walk-backs.        |
| **TheParasite**   | Conversational, agrees readily, claims credit for others' reads.         |
| **TheMartyr**     | Deliberate, slightly formal. Performed acceptance of elimination.        |
| **ThePerformer**  | Fully in-character. Refuses to break frame. Non-sequiturs with conviction.|
| **VibesVoter**    | Casual, intuitive. Speaks in emotional impressions, not logic chains.    |
| **MythBuilder**   | Dramatic but grounded. Narrative framing. Treats the game as a story.    |

Each personality includes:
- A **register** defining energy, cadence, and sentence rhythm
- **Voice markers** — structured descriptors for `sentence_length`, `evidence_relationship`, and `deflection_style` that anchor the LLM's tone (e.g. TheGhost: "Short and punchy. Silence is a sentence."; TheAnalyst: "Long and winding. Gets longer when frustrated.")
- **Prohibited phrases** that this voice must never produce
- **Example dialogue** (5 lines) showing the voice in practice
- **When accused** responses (3 lines) for when directly targeted
- A **late game shift** describing how behaviour changes in rounds 3+
- **Role notes** explaining Mafia vs Town differences
- **Performance notes** on how the personality wraps the archetype's strategy

**Demo mode** (`--demo`) restricts personalities to a safe subset: TheGhost, TheAnalyst, TheConfessor, TheMartyr.

---

## Combination Constraints

Good archetype–personality combinations create contrast between the internal reasoning layer (archetype) and the external presentation layer (personality). Bad combinations reinforce the same tendency twice, producing agents with no distinct voice and no interesting failure mode.

### Role–Personality Exclusions

Strategic roles must not be undermined by performance-first personalities:

| Role      | Banned Personalities               |
|-----------|------------------------------------|
| Detective | TheParasite, ThePerformer          |
| Doctor    | TheParasite, ThePerformer          |

### Archetype–Personality Exclusions

Banned combinations are organised into tiers based on the severity of the failure mode.

#### Tier 1: Structural Collapse (Hard Ban — All Roles)

Combinations where the archetype's internal failure mode aligns with the personality's external communication prohibition, leaving no functional response mechanism.

| Archetype | Banned Personalities | Reason                                                               |
|-----------|----------------------|----------------------------------------------------------------------|
| Reactive  | VibesVoter           | Panic state + no logic vocabulary → empty/one-word responses         |

#### Tier 2: Absence of Self (Ban for Power Roles and Mafia)

Combinations that remove independent judgment. Functional for Villager (following town is acceptable), but broken for roles requiring independent decisions or deception.

| Archetype  | Banned Personalities | Banned Roles              | Reason                                                      |
|------------|----------------------|---------------------------|-------------------------------------------------------------|
| Diplomatic | TheParasite          | Detective, Doctor, Mafia  | No internal position + consensus mirroring → cannot decide or deceive |

#### Tier 3: Existing Bans (Retained)

| Archetype     | Banned Personalities | Reason                                        |
|---------------|----------------------|-----------------------------------------------|
| Passive       | MythBuilder, TheGhost| Reasoning without decisions / double silence   |
| Overconfident | TheParasite          | Redundant lock-on without internal contrast    |
| Stubborn      | MythBuilder          | Debate-proof anchoring with narrative cover    |
| Diplomatic    | TheConfessor         | Double softness — agent becomes invisible      |
| Manipulative  | ThePerformer         | Self-referential dramatic stances — produced self-voting behaviour |

### Frequency Caps

- **Base cap:** No personality may appear more than **2 times** per game, preventing homogeneous rooms.
- **Consensus personality cap:** Consensus-following personalities — TheParasite, TheConfessor, ThePerformer, MythBuilder — are capped at **1 per game**. These amplify or follow the room's existing direction rather than generating independent analysis.

### Independent Archetype Floor

At least **2 players** per game must be assigned an independent-reasoning archetype: Contrarian, Analytical, Impulsive, or Stubborn. If the initial random assignment falls short, one assignment is re-rolled to meet this floor. This ensures every game has enough resistance to consensus to produce meaningful deduction.

### Soft Warnings

If 3 or more players receive the **Analytical** archetype in the same game, a warning fires suggesting a re-roll (convergent reasoning risk). This is advisory — it does not force a re-roll.

### Combinations to Monitor (Not Banned)

| Combination           | Effect                                                                     |
|-----------------------|----------------------------------------------------------------------------|
| Volatile + Detective  | Highest-drama combo — Detective reveals immediately, likely dies next night |
| Manipulative + Mafia  | Significantly increases Mafia win probability                              |
| Reactive + Mafia      | Self-destructive — agent gets louder under pressure, self-exposes           |

---

## Agent Intelligence Systems

### Belief State Tracking

Each agent maintains private suspicion levels (0.0–1.0) for every other player via `SuspicionState`. This is structured intuition — the LLM assigns numbers based on conversational evidence. Agents may include sparse `BELIEF_UPDATE` tags in their reasoning to anchor specific inferences, but are not required to audit every player every turn. The system parses whatever tags are present.

Agents are instructed to write reasoning that reflects their **archetype's texture** — a Paranoid agent's reasoning should convey anxiety, an Analytical agent should produce structured inference chains, a Volatile agent should show why new information feels more urgent than old.

### Staleness Detection

After 2 consecutive rounds with less than 0.05 total belief change, a **FRUSTRATION STATE** fires, forcing the agent to break out of its reasoning loop by naming a new suspect, challenging a quiet player, or sharing held-back information.

### Overconfidence Gating

When an Overconfident agent's top suspect is below 70% certainty, declarative accusations are gated — the agent must hedge until certainty rises.

### Scum-Tell Detection (BeliefGraph)

Three behavioural pattern detectors run during each round:
- **Late Bandwagon** — Joining a vote majority without new reasoning
- **Redirect** — Deflecting from the consensus target onto a quiet player
- **Instahammer** — Casting a decisive vote immediately without discussion

Detected patterns are surfaced in each agent's context so they can reason about them.

### Temporal Consistency Checking

Agents that reference impossible temporal events ("yesterday", "pre-day chat", "earlier conversation" outside the history) are flagged. These slips are injected into other agents' context as potential confabulation markers.

### Recency-Weighted Target Identification

The SummaryAgent shows a "current target" before each phase. Mention counts are weighted by recency:
- **Current round**: weight 1.0
- **Previous round**: weight 0.3
- **Older (2+ rounds)**: weight 0.05

This aggressive decay ensures the current_target field reflects what the room is doing *right now*. Even 10 mentions from two rounds ago barely outweigh a single current-round mention.

### Cross-Game Persistent Memory

Agents accumulate learnings across games via `GameMemoryStore`. Role-aware insights (patterns, strategies, correct/incorrect reads) are stored as JSON in the `memory/` directory and loaded at the start of each new game. This gives agents genuine cross-game improvement — a Detective who learned a useful pattern carries it forward.

### Context Providers

Two MAF-native `ContextProvider` classes inject dynamic per-turn context:
- **BeliefStateProvider** — Injects suspicion state, frustration warnings, overconfidence gates, scum-tell flags, temporal slip alerts, and Iroh Protocol reveal instructions.
- **CrossGameMemoryProvider** — Injects relevant learnings from previous games.

### Tools and Middleware

Agents use MAF-native structured actions:
- `cast_vote` — Submit a vote during day phase
- `choose_target` — Select a target during night phase

Five middleware components run on every player agent (outermost to innermost):

1. **`ResilientSessionMiddleware`** — Catches `previous_response_not_found` errors when server-side sessions expire. Extracts conversation history from `InMemoryHistoryProvider`, creates a fresh `AgentSession` with transferred state, and injects a compressed history summary. Registered first so it wraps all other middleware.
2. **`RateLimitMiddleware`** — Handles 429 rate-limit errors with exponential backoff and jitter. Also performs **proactive** session refresh: before each call, if the session has been idle longer than `MAFIA_SESSION_IDLE_THRESHOLD` (default 20 s), the session is refreshed pre-emptively. During backoff, if cumulative delay exceeds `MAFIA_SESSION_REFRESH_THRESHOLD` (default 25 s), a mid-backoff refresh fires.
3. **`corporate_speak_middleware`** — If an agent's action contains 3+ corporate/boardroom words, the response is re-invoked with a slang enforcement hint.
4. **`ReasoningActionMiddleware`** — Parses the `REASONING:`/`ACTION:` split from every response and stores parsed values on `context.metadata` so the orchestrator can read them cleanly.
5. **`BeliefUpdateMiddleware`** — Extracts `BELIEF_UPDATE` tags from reasoning text and stores the parsed updates on `context.metadata` for automatic belief state application.

A static **`SessionHealthMonitor`** tracks per-session idle time (via `touch()` / `idle_seconds()` / `remove()`) to support proactive refresh decisions.

### Multi-Turn Conversational Memory

Each player agent includes an `InMemoryHistoryProvider` from the MAF framework. This gives agents genuine multi-turn memory — each agent remembers what it and others actually said in prior rounds as proper message objects rather than a concatenated string. This reduces reasoning drift and partner-confusion errors.

### Iroh Protocol

When other agents' collective suspicion of a Detective or Doctor rises, a graduated identity-reveal system fires:

| Level          | Avg Suspicion Threshold | Behaviour                                                       |
|----------------|------------------------|-----------------------------------------------------------------|
| **Soft Hint**  | ≥ 0.35                 | Oblique hint — "I have information that would change this vote"  |
| **Hard Claim** | ≥ 0.45                 | Conditional claim — "I am Detective. If the vote isn't redirected, I reveal everything." |
| **Full Reveal**| ≥ 0.55                 | Immediate full reveal with all accumulated findings              |

If the Detective holds a confirmed red-check (proven Mafia finding), all thresholds drop by **0.10** — information preservation outweighs survival risk. The current Iroh level is automatically injected into each agent's context via `BeliefStateProvider`.

---

## Rate Limiting

API calls are rate-limited through two cooperating tiers implemented in `agents/rate_limiter.py`:

### Global Concurrency Limit

A process-wide `asyncio.Semaphore` (default **5** concurrent calls, configurable via `MAFIA_MAX_CONCURRENT_CALLS`) prevents overwhelming the Azure endpoint. The semaphore is lazily created to bind to the active event loop. A secondary per-phase semaphore in the orchestrator (`max(2, MAFIA_MAX_CONCURRENT_CALLS - 1)`) ensures at least two slots remain available under heavy rate limiting.

### Error-Aware Retry Logic

| Error Type | Retry Behaviour                                     |
|------------|-----------------------------------------------------|
| **429**    | Retry up to `MAFIA_RATE_LIMIT_RETRIES` times (default 3) with exponential backoff |
| **5xx**    | Fail immediately — server errors are unlikely to self-heal fast enough |
| **Timeout**| Retry once                                           |
| **Other**  | Propagate immediately                                |

### Exponential Backoff

Delay is computed as `base × 2^attempt`, capped at **8 s**, with full jitter (+0–0.5 s). The base delay defaults to **1.0 s** (`MAFIA_BACKOFF_BASE_DELAY`). Per-player retry counters are tracked for observability via `get_retry_stats()`.

---

## Session Resilience

Azure Foundry server-side sessions can expire under load or idle time. Three components in `agents/middleware.py` cooperate to prevent and recover from session loss:

### ResilientSessionMiddleware

Catches `previous_response_not_found` errors (server-side TTL expiry). When triggered:
1. Extracts conversation history from `InMemoryHistoryProvider`'s internal cache.
2. Creates a fresh `AgentSession` with all state transferred.
3. Injects a compressed text summary of recent messages (truncated to 200 chars per message) so the agent retains conversational context.
4. Registers the replacement session in `_session_refresh_registry` so the orchestrator's `run_agent_stream()` can pick up the new session transparently.

Must be registered **first** in the middleware chain to wrap all other middleware.

### RateLimitMiddleware

In addition to handling 429 errors (see [Rate Limiting](#rate-limiting)), this middleware performs **proactive** session refresh:
- **Pre-call:** If the session has been idle longer than `MAFIA_SESSION_IDLE_THRESHOLD` (default 20 s), the session is refreshed before the API call fires.
- **Mid-backoff:** If cumulative backoff delay exceeds `MAFIA_SESSION_REFRESH_THRESHOLD` (default 25 s), a refresh fires during the backoff window.

This prevents session expiry rather than only recovering from it.

### SessionHealthMonitor

A static utility class that tracks per-session timestamps:
- `touch(session_id)` — Record a successful call.
- `idle_seconds(session_id)` — Seconds since last call.
- `remove(session_id)` — Clean up on session destruction.

Used by `RateLimitMiddleware` to decide whether proactive refresh is needed.

---

## Graceful Degradation

When an API call fails entirely (after all retries are exhausted), the orchestrator falls back to heuristic actions so the game can continue without crashing. Fallbacks are implemented per phase in `engine/orchestrator.py`:

| Phase             | Fallback Behaviour                                                                  |
|-------------------|-------------------------------------------------------------------------------------|
| **Discussion**    | Player passes their turn — "I'll listen for now"                                    |
| **Voting**        | Vote for the player with the highest suspicion in the agent's belief state           |
| **Mafia Night Kill** | Target the Town player with the lowest suspicion (i.e. highest threat to Mafia)  |
| **Detective Investigation** | Investigate a random eligible (un-investigated) player                   |
| **Doctor Protection** | Protect the player with the highest threat score from the belief state. If the Doctor's own suspicion exceeds **0.3**, self-protect instead. |

These fallbacks ensure that a single API failure never kills a game — the affected player simply makes a reasonable heuristic decision for that turn.

---

## Prompt Engineering

Agent prompts are assembled in `prompts/builder.py` from layered components:

1. **Role Goal** — What winning looks like for this specific role (first-person mandate)
2. **Grounding Constraint** — Anti-confabulation rule preventing reference to events not in the discussion history
3. **Conversational Rule** — 8 rules forcing genuine conversation: respond to the last speaker, use names + second person, make claims not just questions, no pile-on echoing, disagree out loud, move the conversation forward
4. **Reasoning Frameworks** — Reusable strategic thinking modules:
   - **Game Theory** — Threat ranking, information asymmetry, timing
   - **Sun Tzu** — Deception, intelligence targeting, terrain awareness
   - **Machiavelli** — Political operation, coalition building, appearance management
   - **Carnegie (Execution)** — Social influence, indirect persuasion, challenge absorption
   - **Carnegie (Villager)** — People-reading, trust through interaction, social consensus
   - **Behavioural Psychology** — Cognitive biases, anchoring, loss aversion, narrative coherence
   - **Strategic Glossary** — Competitive Mafia terms (Busing, Lynch-bait, Tunneling, Wagon-steering, Instahammer)
   - **Incentive Reasoning** — Who benefits from each elimination?
5. **Role-Specific Protocols** — Mandatory blocks that must not be removed:
   - **Mafia**: Deception Layer, Syndicate Channel (partner coordination), Mafia Threat Check (4 mandatory pre-reasoning questions + solo 5th question)
   - **Detective**: Claim Protocol (mandatory red-check announcement), Iroh Protocol (identity reveal), Red Check Reveal Strategy, Innocent Result Sharing
   - **Doctor**: Value-Protection Heuristic (protect the reasoner — evidence-based predictions, bandwagon resistance — not the loudest voice), Iroh Protocol
   - **Villager**: Voter Consistency (anti-Mafia-Steering tool — track vote blocs, last-moment switches, lone divergent votes)
   - **Detective**: Vote Pattern Analysis (lone divergent vote detection, voting bloc tracking, vote-vs-kill-target comparison)
   - **Narrator**: Night Anonymity Rule (no living player names during night)
6. **Archetype Strategy Modifier** — Role-specific behavioural deviation
7. **Voice / Personality Block** — Either the personality's register or the archetype's voice profile
8. **Self-Critique** — Reflexion loop checking for tunneling, circular reasoning, and manipulation
9. **Output Format** — `REASONING:` / `ACTION:` structure with self-vote prevention

### Mafia Threat Check

Mafia agents must answer four questions explicitly in their reasoning before engaging with the room on every turn:
1. Am I personally under suspicion?
2. Is my partner under suspicion?
3. Who is the biggest threat to Mafia **among Town players**? (Partner is explicitly excluded — prevents partner-confusion errors)
4. Is my cover story still consistent?

When the partner has been eliminated, a fifth question fires: which player is most likely to identify me, and what must happen this round to prevent that?

### Framework Distribution by Role

| Role       | Frameworks                                                        |
|------------|-------------------------------------------------------------------|
| **Mafia**  | Game Theory + Sun Tzu + Machiavelli + Carnegie Execution + Strategic Glossary + Incentive Reasoning |
| **Detective** | Game Theory + Sun Tzu + Vote Pattern Analysis + Social Cover + Strategic Glossary + Incentive Reasoning |
| **Doctor** | Game Theory + Sun Tzu + Strategic Glossary + Incentive Reasoning  |
| **Villager** | Carnegie Villager + Behavioural Psychology + Strategic Glossary + Incentive Reasoning |

All agents output in a structured `REASONING:` / `ACTION:` format. Reasoning represents internal thinking (including optional sparse `BELIEF_UPDATE` tags); only the action is visible to other agents.

### Discussion Rules

Discussion-phase output is governed by two layers of enforcement:

**System prompt (`DISCUSSION_RULES` in `builder.py`)** — a block injected into every player's system prompt:
- **Not a vote phase** — conversational argument only; no vote declarations
- **Specific claim requirement** — quote what another player said + explain why it matters
- **Own read first** — establish your position before reacting to others
- **Speak obliquely** — imply and deflect, don't narrate your strategy directly
- **No consensus echoing** — add new evidence or a new angle, never just agree
- **Show, don't tell** — reactions through action, not declaration

**Runtime reminder (`_VOTE_BAN_REMINDER` in `base.py`)** — appended to every discussion prompt at call time, reminding agents not to open with vote declarations. This dual enforcement (system prompt + per-call reminder) significantly reduces vote-leaking during discussion rounds.

### Anti-AI Writing

Every agent prompt includes:
- **Negative Constraints** — 80+ banned AIism phrases from Wikipedia's "Signs of AI writing"
- **Anti-AI Structure** — 11 structural rules (no rule of three, no trailing -ing clauses, no emoji, etc.)
- **GenZ/MLE Slang Register** — Multicultural London English and Gen Z slang injected for texture
- **Corporate-Speak Penalty** — 20 banned boardroom words with natural replacements

---

## Prerequisites

- **Python 3.12+**
- **Azure CLI** (`az`) — [Install guide](https://learn.microsoft.com/cli/azure/install-azure-cli)
- **Azure AI Foundry project** with at least one deployed model (e.g. `gpt-4o-mini`)
- An active `az login` session with access to the Foundry project

---

## Setup

1. **Clone the repository:**

   ```bash
   git clone https://github.com/saaleh-a/MafiaFramework.git
   cd MafiaFramework
   ```

2. **Install dependencies:**

   ```bash
   pip install -r requirements.txt
   ```

   This installs:
   - `agent-framework-foundry` — Microsoft Agent Framework with Azure Foundry integration
   - `azure-identity` — Azure authentication (supports `az login`)
   - `python-dotenv` — `.env` file loading

3. **Authenticate with Azure:**

   ```bash
   az login
   ```

4. **Configure environment variables:**

   Create a `.env` file in the project root:

   ```dotenv
   FOUNDRY_PROJECT_ENDPOINT=https://<your-resource>.services.ai.azure.com
   FOUNDRY_MODEL=gpt-4o-mini
   ```

   | Variable                   | Required | Description                                                    |
   |----------------------------|----------|----------------------------------------------------------------|
   | `FOUNDRY_PROJECT_ENDPOINT` | Yes      | Your Azure AI Foundry project endpoint URL                     |
   | `FOUNDRY_MODEL`            | No       | Model deployment name (defaults to `gpt-4o-mini`)              |

   > **Important:** The model names must exactly match deployed model names in your Azure AI Foundry project.

5. **Verify connectivity:**

   ```bash
   python check.py
   ```

   Expected output: `SETUP OK` followed by `✓ Ready to run the game.`

---

## Usage

### Run a single game

```bash
python main.py
```

### Command-line options

| Flag              | Description                                                     |
|-------------------|-----------------------------------------------------------------|
| `--reveal-roles`  | Show all role assignments (including hidden roles) at the start |
| `--debug`         | Show full agent reasoning without truncation                    |
| `--quiet`         | Show action lines only, hide reasoning                          |
| `--seed <int>`    | Set random seed for reproducible role/model/archetype/personality assignment |
| `--games <int>`   | Run multiple games and print aggregate win statistics            |
| `--demo`          | Restrict personalities to demo-safe subset (TheGhost, TheAnalyst, TheConfessor, TheMartyr) |

### Examples

```bash
# Show all role assignments for debugging
python main.py --reveal-roles

# Full reasoning output (no truncation)
python main.py --debug

# Run 10 games with a fixed seed and print win rates
python main.py --games 10 --seed 42

# Minimal output - actions only
python main.py --quiet

# Demo mode with safe personality subset
python main.py --demo
```

### Output Format

Each agent's turn is displayed in a coloured box showing name, role, archetype, and personality:

```
┌─ [Alice | Villager | Paranoid | TheGhost] ───────┐
│ REASONING:                                        │
│   (internal thinking - hidden from other agents)  │
│                                                    │
│ ACTION:                                            │
│   Something is wrong. Did anyone else notice that? │
└───────────────────────────────────────────────────-┘
```

Roles are colour-coded:
- 🔴 **Mafia** — Red
- 🟡 **Detective** — Yellow
- 🟢 **Doctor** — Green
- 🔵 **Villager** — Blue
- ⚪ **Narrator** — White/Bold

---

## Configuration

### Model Pool

Edit `config/model_registry.py` to add or remove models from the pool. Each game randomly assigns one model per player from `AVAILABLE_MODELS`:

```python
AVAILABLE_MODELS = [
    ModelConfig(name=_display_name(_primary_model), model_id=_primary_model, short="4om"),
]
```

> Every model in the pool **must** have a matching deployment in your Azure AI Foundry project. A missing deployment causes a `DeploymentNotFound` error at runtime.

### Player Names and Role Distribution

Edit `engine/game_manager.py` to change player names or role counts:

```python
PLAYER_NAMES = [
    "Alice", "Bob", "Charlie", "Diana", "Eve", "Frank",
    "Grace", "Hank", "Ivy", "Jack", "Kate",
]

ROLE_DISTRIBUTION = [
    "Mafia", "Mafia",
    "Detective",
    "Doctor",
    "Villager", "Villager", "Villager", "Villager",
    "Villager", "Villager", "Villager",
]
```

The number of player names must match the number of roles.

### Rate Limiting & Session Resilience

These settings are loaded from environment variables (or `.env`) in `config/settings.py`. All have sensible defaults and are optional.

| Variable                           | Default | Description                                                          |
|------------------------------------|---------|----------------------------------------------------------------------|
| `MAFIA_MAX_CONCURRENT_CALLS`       | `5`     | Maximum concurrent API calls (clamped 1–10)                          |
| `MAFIA_RATE_LIMIT_RETRIES`         | `3`     | Retries for 429 rate-limit errors (clamped 1–5)                      |
| `MAFIA_BACKOFF_BASE_DELAY`         | `1.0`   | Base delay in seconds for exponential backoff (clamped ≥ 0.1)        |
| `MAFIA_SESSION_IDLE_THRESHOLD`     | `20.0`  | Seconds of idle time before proactive session refresh                 |
| `MAFIA_SESSION_REFRESH_THRESHOLD`  | `25.0`  | Cumulative backoff delay (seconds) before mid-backoff session refresh |
| `MAFIA_ENABLE_STREAMING_FALLBACK`  | `false` | Retry failed streaming calls as non-streaming                        |

---

## Testing

The test suite validates core game mechanics without requiring Azure credentials or model deployments.

```bash
python -m unittest tests.test_refactor -v
```

**149 tests** across **41 test classes**:

| Test Class                         | Tests | Coverage                                                              |
|------------------------------------|-------|-----------------------------------------------------------------------|
| `TestSelfVotePrevention`           | 7     | VOTE: tag, intent phrases, and last-mentioned-name self-votes blocked |
| `TestTieBreakLogic`                | 5     | Tie detection, three-way tie, decisive voter filtering                |
| `TestPersonalityExclusion`         | 5     | Role–personality exclusions, frequency cap, exhaustion error          |
| `TestActionSplitting`              | 5     | REASONING/ACTION splitting, embedded marker stripping                 |
| `TestGhostFiltering`              | 4     | Eliminated round tracking, public summary role hiding                 |
| `TestArchetypePersonalityExclusion`| 7     | All banned combinations enforced incl. Reactive+VibesVoter, non-excluded archetype allows all |
| `TestReasoningOnlyParser`          | 4     | REASONING-only returns empty action, plain text still works           |
| `TestRecencyWeighting`             | 3     | Current round outweighs old, previous round 0.3 weight               |
| `TestMafiaPromptQuestions`         | 2     | Threat Check questions present, solo question references partner      |
| `TestBeliefInstructionUpdate`      | 2     | "MAY" not "MUST" for BELIEF_UPDATE, archetype texture mentioned       |
| `TestMafiaPartnerConfusionFix`     | 2     | Q3 excludes partner by name, asks only about Town players             |
| `TestDoctorHeuristic`              | 3     | Protection signals present, danger signals present, no "SOCIAL ENGINE" language |
| `TestStrongerRecencyDecay`         | 1     | 10 mentions from 2 rounds ago don't beat 1 current mention            |
| `TestMiddlewareRegistration`       | 3     | ReasoningActionMiddleware and BeliefUpdateMiddleware exist and subclass AgentMiddleware |
| `TestConsensusPersonalityCap`      | 5     | Consensus personalities capped at 1, non-consensus still at 2         |
| `TestManipulativePerformerBan`     | 1     | Manipulative+ThePerformer banned                                      |
| `TestLoneDivergentVoteInstruction` | 2     | Lone divergent vote instruction in Villager and Detective prompts     |
| `TestIndependentArchetypeFloor`    | 2     | Independent archetypes and consensus personalities defined correctly   |
| `TestInMemoryHistoryProvider`      | 1     | InMemoryHistoryProvider importable and instantiable                    |
| `TestAllCombinationBans`           | 3     | Tier 1/2/3 bans present in exclusion tables, Tier 2 role specificity  |
| `TestDiplomaticParasiteTier2`      | 4     | Diplomatic+TheParasite blocked for Detective/Doctor/Mafia, allowed for Villager |
| `TestDiscussionNoVoteFormat`       | 2     | Discussion phase bans vote declarations in system prompt              |
| `TestNightKillPromptLanguage`      | 2     | Night kill prompt uses correct language                                |
| `TestContrarianResistance`         | 2     | Contrarian archetype resists consensus                                 |
| `TestPersonalityVoiceMarkers`      | 8     | Voice markers (sentence_length, evidence_relationship, deflection_style) present for all 8 personalities |
| `TestDiscussionHistoryExcludesSelf`| 2     | format_discussion_prompt only injects messages after agent's last contribution |
| `TestExpandedSlangRegister`        | 2     | GenZ/MLE slang vocabulary present in register                          |
| `TestSessionExpiredErrorDetection` | 3     | ResilientSessionMiddleware detects session expiry errors               |
| `TestHistorySummarization`         | 3     | Session history summarization for recovery                             |
| `TestExtractHistoryFromSession`    | 2     | Extract messages from InMemoryHistoryProvider internal cache           |
| `TestRefreshSession`               | 2     | Session refresh preserves state and injects summary                    |
| `TestSessionHealthMonitor`         | 4     | SessionHealthMonitor touch/idle_seconds/remove methods                 |
| `TestRateLimitErrorDetection`      | 3     | Rate limit error classification (429, 5xx, timeout)                    |
| `TestConversationContinuity`       | 2     | Agent can reference previous messages across sessions                  |
| `TestSettingsConfiguration`        | 3     | Environment variable loading for rate limiting and session settings     |
| `TestSuccessCriteria`              | 1     | Game loop completes without errors                                     |
| `TestSessionRefreshRegistry`       | 2     | _session_refresh_registry mechanism for transparent session replacement |
| `TestNightKillPromptMechanicLanguage` | 2  | Night kill prompt mentions "eliminating" / "removing"                  |
| `TestDiscussionVoteBanRuntimeReminder` | 2 | _VOTE_BAN_REMINDER appended to discussion prompts at runtime           |
| `TestDiscussionOnlyInjectsNewMessages` | 2 | Only inject messages after agent's last contribution                   |
| `TestMAF10MessageAPI`              | 3     | MAF 1.0 Message(contents=[]) API compliance                           |
| `TestMAF10ProviderSignature`       | 2     | MAF 1.0 ContextProvider.before_run(session: AgentSession) signature    |
| `TestMAF10DependencyVersions`      | 2     | agent-framework-foundry>=1.0.0,<2.0.0 pinned correctly                |
| `TestMAF10ImportPaths`             | 4     | FoundryChatClient from agent_framework.foundry import paths            |

---

## Troubleshooting

### `DeploymentNotFound` (404 Error)

```
openai.NotFoundError: Error code: 404 - {'error': {'message': 'The API deployment
for this resource does not exist.', 'code': 'DeploymentNotFound'}}
```

**Cause:** The model deployment name in your `.env` file (or `model_registry.py`) does not match any active deployment in your Azure AI Foundry project.

**Fix:**
1. Open your Azure AI Foundry project and verify the exact deployment names.
2. Update `FOUNDRY_MODEL` in your `.env` to match.
3. If you just created a deployment, wait approximately 5 minutes and retry.
4. Run `python check.py` to verify connectivity before starting a game.

### `FOUNDRY_PROJECT_ENDPOINT is not set`

**Cause:** The `.env` file is missing or does not contain `FOUNDRY_PROJECT_ENDPOINT`.

**Fix:** Create a `.env` file in the project root with your Foundry project endpoint:
```dotenv
FOUNDRY_PROJECT_ENDPOINT=https://<your-resource>.services.ai.azure.com
```

### `az login` / Authentication Errors

**Cause:** The Azure CLI is not authenticated, or the logged-in account lacks access to the Foundry project.

**Fix:**
```bash
az login
# Verify access:
az account show
```

### `check.py` prints `✗ Unexpected output`

**Cause:** The model responded but produced unexpected text, or the model deployment is misconfigured.

**Fix:** Verify that the model deployment in your Foundry project is operational, and that `FOUNDRY_MODEL` in `.env` matches the deployment name exactly.