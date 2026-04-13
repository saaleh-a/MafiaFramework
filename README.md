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
- [Prompt Engineering](#prompt-engineering)
- [Prerequisites](#prerequisites)
- [Setup](#setup)
- [Usage](#usage)
- [Configuration](#configuration)
- [Testing](#testing)
- [Troubleshooting](#troubleshooting)
- [Further Documentation](#further-documentation)

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
         │  win detection            │
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
    └──────────┘ └─────────┘ └───────────┘
            │
    ┌───────▼──────────┐
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
| `agents/middleware.py`       | MAF middleware — `corporate_speak_middleware` (slang enforcement), `ReasoningActionMiddleware` (REASONING/ACTION parsing), `BeliefUpdateMiddleware` (BELIEF_UPDATE extraction), `ResilientSessionMiddleware` (session expiration recovery), `RateLimitMiddleware` (429 backoff + proactive refresh). |
| `agents/game_tools.py`       | MAF `@tool`-decorated functions — `cast_vote` and `choose_target` for structured game actions. |
| `agents/rate_limiter.py`     | Global rate limiting — asyncio semaphore, exponential backoff with jitter, error classification (429 retry / 5xx fail fast). |
| `agents/memory.py`           | Persistent cross-game memory — role-aware learnings stored as JSON, loaded each game.        |
| `agents/narrator.py`         | Narrator agent — omniscient, impartial game master announcements.                            |
| `agents/mafia.py`            | Mafia agent — day discussion, voting, night kill target, Syndicate coordination.             |
| `agents/detective.py`        | Detective agent — day discussion, voting, night investigation, Last Stand Protocol reveal.         |
| `agents/doctor.py`           | Doctor agent — day discussion, voting, night protection, Value-Protection Heuristic.         |
| `agents/villager.py`         | Villager agent — day discussion, voting, Voter Consistency tracking.                         |
| `config/model_registry.py`   | Model pool definition and Azure Foundry client factory.                                      |
| `config/settings.py`         | Environment variable loader with rate-limiting, streaming, game-balance, and session-resilience configuration.                      |
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
│   ├── middleware.py           # MAF middleware (corporate-speak, REASONING/ACTION parsing, belief updates, session resilience, rate limiting)
│   ├── rate_limiter.py        # Global rate limiting with exponential backoff
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
│   └── test_refactor.py        # 329 tests across 84 test classes
├── docs/
│   ├── METHODOLOGY.md          # Design philosophy and decision rationale
│   ├── ARCHITECTURE.md         # Complete technical reference
│   └── ONBOARDING.md           # Setup guide and developer handbook
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
- **BeliefStateProvider** — Injects suspicion state, frustration warnings, overconfidence gates, scum-tell flags, temporal slip alerts, and Last Stand Protocol reveal instructions.
- **CrossGameMemoryProvider** — Injects relevant learnings from previous games.

### Tools and Middleware

Agents use MAF-native structured actions:
- `cast_vote` — Submit a vote during day phase
- `choose_target` — Select a target during night phase

Three middleware components run on every player agent:
- **`corporate_speak_middleware`** — If an agent's action contains 3+ corporate/boardroom words, the response is re-invoked with a slang enforcement hint.
- **`ReasoningActionMiddleware`** — Parses the `REASONING:`/`ACTION:` split from every response and stores parsed values on `context.metadata` so the orchestrator can read them cleanly.
- **`BeliefUpdateMiddleware`** — Extracts `BELIEF_UPDATE` tags from reasoning text and stores the parsed updates on `context.metadata` for automatic belief state application.

Two additional resilience middleware wrap all others (registered first on every agent):
- **`ResilientSessionMiddleware`** — Catches `previous_response_not_found` errors when Azure sessions expire, reconstructs conversation from `InMemoryHistoryProvider`, and retries on a fresh session.
- **`RateLimitMiddleware`** — Intercepts 429 rate-limit errors with exponential backoff (1s → 2s → 4s → 8s + jitter, up to 3 retries). Proactively refreshes sessions that have been idle longer than `MAFIA_SESSION_IDLE_THRESHOLD` (default 20s).

### Multi-Turn Conversational Memory

Each player agent includes an `InMemoryHistoryProvider` from the MAF framework. This gives agents genuine multi-turn memory — each agent remembers what it and others actually said in prior rounds as proper message objects rather than a concatenated string. This reduces reasoning drift and partner-confusion errors.

### Last Stand Protocol

When other agents' collective suspicion of a Detective or Doctor rises, the system instructs them to reveal their role in graduated steps to survive. A dead Detective/Doctor helps nobody.

Three levels trigger based on average suspicion from other agents:
- **Soft Hint** (≥ 0.35) — Hint at having information without revealing role
- **Hard Claim** (≥ 0.45) — Conditionally claim role: "I am the Detective. I will reveal if the vote redirects."
- **Full Reveal** (≥ 0.55) — Immediately reveal role with all evidence

If the Detective holds an unshared red check (confirmed Mafia finding), all thresholds are lowered by 0.10 because information preservation outweighs survival risk.

### Graceful Degradation

When API calls fail entirely, per-phase fallback methods in `engine/orchestrator.py` produce reasonable default behaviour:
- **Discussion** — "I'll listen for now" (pass turn)
- **Voting** — Vote for highest-suspicion target from belief state
- **Mafia Night Kill** — Target lowest-suspicion Town player (biggest threat)
- **Investigation** — Investigate most suspicious unchecked player
- **Protection** — Self-protect if suspicion > 0.3, otherwise random ally

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
   - **Detective**: Claim Protocol (mandatory red-check announcement), Last Stand Protocol (identity reveal), Red Check Reveal Strategy, Innocent Result Sharing
   - **Doctor**: Value-Protection Heuristic (protect the reasoner — evidence-based predictions, bandwagon resistance — not the loudest voice), Last Stand Protocol
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

   **Advanced configuration** (all optional, with sensible defaults):

   | Variable                           | Default | Description                                                |
   |------------------------------------|---------|----------------------------------------------------------------|
   | `MAFIA_MAX_CONCURRENT_CALLS`       | 5       | Max simultaneous API calls (global semaphore, max 10)        |
   | `MAFIA_RATE_LIMIT_RETRIES`         | 3       | Retry attempts on 429 rate-limit errors (max 5)              |
   | `MAFIA_BACKOFF_BASE_DELAY`         | 1.0     | Base delay for exponential backoff (seconds)                 |
   | `MAFIA_ENABLE_STREAMING_FALLBACK`  | false   | Retry streaming calls as non-streaming on error              |
   | `MAFIA_DETECTIVE_VOTE_WEIGHT`      | 2       | Detective vote multiplier (max 5)                            |
   | `MAFIA_VOTE_CONFIDENCE_THRESHOLD`  | 0.45    | Min belief certainty before engine overrides vote            |
   | `MAFIA_CONSENSUS_SHORTLIST_SIZE`   | 3       | Size of the pressure wagon (max 5)                           |
   | `MAFIA_EVASION_BONUS`              | 0.08    | Suspicion boost for evasive players                          |
   | `MAFIA_SESSION_IDLE_THRESHOLD`     | 20.0    | Proactive session refresh after N seconds idle               |
   | `MAFIA_SESSION_REFRESH_THRESHOLD`  | 25.0    | Force refresh after N seconds cumulative backoff             |

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

Edit `engine/game_manager.py` to change player names. Role distribution scales automatically with player count:

```python
PLAYER_NAMES = [
    "Alice", "Bob", "Charlie", "Diana", "Eve", "Frank",
    "Grace", "Hank", "Ivy", "Jack", "Kate",
]
```

Role distribution is computed by `_build_role_distribution()`:
- ≤10 players: 2 Mafia
- ≤15 players: 3 Mafia
- 15+ players: 4 Mafia
- Detective added if 5+ players
- Doctor added if 6+ players
- Remaining slots: Villagers

---

## Testing

The test suite validates core game mechanics without requiring Azure credentials or model deployments.

```bash
python -m unittest tests.test_refactor -v
```

**329 tests** across **84 test classes** covering:

| Category                          | Classes | Tests | Coverage                                                              |
|-----------------------------------|---------|-------|-----------------------------------------------------------------------|
| Vote parsing & tie-break          | 3       | 16    | Self-vote prevention, intent phrases, tie detection, decisive vote    |
| Personality constraints           | 7       | 24    | All 3 ban tiers, frequency caps, cap relaxation, Tier 2 role bans    |
| Action parsing                    | 4       | 16    | REASONING/ACTION split, tool trace normalisation, recovery            |
| Belief state & Last Stand         | 7       | 35    | Suspicion tracking, staleness, Last Stand Protocol, overconfidence    |
| Session resilience                | 8       | 30    | Session recovery, rate limiting, health monitoring, refresh registry  |
| Game state                        | 7       | 40    | Win conditions, elimination, night actions, voting, summaries         |
| Prompt structure                  | 5       | 18    | Discussion rules, voice markers, slang register, vote ban reminder    |
| Memory                            | 1       | 5     | GameMemoryStore load/save/inject                                      |
| Summary agent                     | 6       | 20    | Recency weighting, compression, evidence extraction, vote summary     |
| Rate limiter                      | 3       | 12    | Error classification, backoff calculation, retry stats                |
| Game manager                      | 5       | 18    | Archetype/personality assignment, player names, constraint enforcement |
| Settings & config                 | 2       | 8     | Environment variable parsing, configuration validation                |
| MAF API compliance                | 4       | 11    | Import paths, dependency versions, provider signatures                |
| Middleware                        | 2       | 8     | Registration, class hierarchy, narrator configuration                 |
| Other                             | 20      | 68    | Discussion format, console setup, conversation continuity, etc.       |

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

---

## Further Documentation

| Document | Description |
|----------|-------------|
| **[docs/METHODOLOGY.md](docs/METHODOLOGY.md)** | Design philosophy, belief state architecture, prompt engineering methodology, combination ban rationale, anti-AI writing enforcement, error recovery philosophy |
| **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** | Complete technical reference — every module, data flow, middleware stack, context provider architecture, concurrency model, state management |
| **[docs/ONBOARDING.md](docs/ONBOARDING.md)** | Step-by-step setup guide, CLI reference, output format guide, customisation guide, debugging tips, FAQ, glossary |

Each document includes **TL;DR** and **ELI5** sections at the top.