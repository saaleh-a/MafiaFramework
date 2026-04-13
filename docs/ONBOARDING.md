# Onboarding

Everything you need to go from zero to running your first MafiaFramework game — and understanding what happens under the hood.

---

## TL;DR

1. Install Python 3.12+, Azure CLI, clone the repo, `pip install -r requirements.txt`
2. `az login`, create a `.env` with `FOUNDRY_PROJECT_ENDPOINT=https://your-endpoint.services.ai.azure.com`
3. `python check.py` → should print "SETUP OK"
4. `python main.py` → watch 11 AI agents play Mafia
5. Use `--reveal-roles` to see who's who, `--debug` for full reasoning, `--games 10 --seed 42` for reproducible multi-game runs
6. Tests: `python -m unittest tests.test_refactor -v` (332 tests, no Azure needed)

---

## ELI5

You're setting up a virtual game night where 11 AI players will play Mafia. First, you need to get a key to the building (Azure login). Then you tell the game where to find the AI brains (the Foundry endpoint). After that, you just press play and watch the AIs argue about who's the bad guy. You can peek at everyone's roles (`--reveal-roles`), read their private thoughts (`--debug`), or run the game 100 times to see which team wins more often (`--games 100`). The AIs even remember lessons from previous games, so they get better over time.

---

## Table of Contents

- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Configuration](#configuration)
- [Verify Setup](#verify-setup)
- [Running Your First Game](#running-your-first-game)
- [Understanding the Output](#understanding-the-output)
- [CLI Reference](#cli-reference)
- [Running Tests](#running-tests)
- [Project Layout](#project-layout)
- [Key Concepts](#key-concepts)
- [Customisation Guide](#customisation-guide)
- [Debugging Tips](#debugging-tips)
- [Troubleshooting](#troubleshooting)
- [Frequently Asked Questions](#frequently-asked-questions)
- [Glossary](#glossary)

---

## Prerequisites

### Required Software

| Software       | Version | Purpose                                        | Install Guide                                                     |
|----------------|---------|------------------------------------------------|-------------------------------------------------------------------|
| **Python**     | 3.12+   | Runtime                                        | [python.org](https://www.python.org/downloads/)                   |
| **Azure CLI**  | Latest  | Authentication with Azure AI Foundry           | [Install Azure CLI](https://learn.microsoft.com/cli/azure/install-azure-cli) |
| **Git**        | Latest  | Clone the repository                           | [git-scm.com](https://git-scm.com/)                              |

### Required Azure Resources

| Resource                          | Purpose                                    |
|-----------------------------------|--------------------------------------------|
| **Azure AI Foundry project**      | Hosts the LLM deployments the agents use   |
| **At least one model deployment** | e.g., `gpt-4o-mini` — the AI brain(s)     |
| **Azure subscription access**     | Your `az login` account must have access   |

### Setting Up Azure AI Foundry

If you don't already have a Foundry project:

1. Go to [Azure AI Foundry](https://ai.azure.com)
2. Create a new project (or use an existing one)
3. Deploy at least one model (e.g., `gpt-4o-mini`)
4. Note the **Project endpoint** — it looks like `https://your-resource.services.ai.azure.com`
5. Note the **Deployment name** — this is the model ID you'll use

---

## Installation

### Step 1: Clone the Repository

```bash
git clone https://github.com/saaleh-a/MafiaFramework.git
cd MafiaFramework
```

### Step 2: Create a Virtual Environment (recommended)

```bash
python -m venv .venv

# macOS/Linux:
source .venv/bin/activate

# Windows:
.venv\Scripts\activate
```

### Step 3: Install Dependencies

```bash
pip install -r requirements.txt
```

This installs three packages:

| Package                    | Purpose                                                  |
|----------------------------|----------------------------------------------------------|
| `agent-framework-foundry`  | Microsoft Agent Framework with Azure Foundry integration |
| `azure-identity`           | Azure authentication (supports `az login`)               |
| `python-dotenv`            | Loads `.env` file for configuration                      |

### Step 4: Authenticate with Azure

```bash
az login
```

Follow the browser prompts to authenticate. Your account must have access to the Azure AI Foundry project.

> **Note:** No API keys are stored anywhere. The system uses `AzureCliCredential`, which authenticates via your active `az login` session.

---

## Configuration

### Create Your `.env` File

Create a file named `.env` in the project root:

```dotenv
FOUNDRY_PROJECT_ENDPOINT=https://your-resource.services.ai.azure.com
FOUNDRY_MODEL=gpt-4o-mini
```

### Environment Variables

| Variable                          | Required | Default        | Description                                    |
|-----------------------------------|----------|----------------|------------------------------------------------|
| `FOUNDRY_PROJECT_ENDPOINT`        | **Yes**  | —              | Your Azure AI Foundry project endpoint URL     |
| `FOUNDRY_MODEL`                   | No       | `gpt-4o-mini`  | Model deployment name                          |

### Advanced Configuration

These environment variables control game balance and resilience. You don't need to set any of them — the defaults work well.

#### Rate Limiting

| Variable                        | Default | Max | Description                                      |
|---------------------------------|---------|-----|--------------------------------------------------|
| `MAFIA_MAX_CONCURRENT_CALLS`    | 5       | 10  | Max simultaneous API calls                       |
| `MAFIA_RATE_LIMIT_RETRIES`      | 3       | 5   | Retry attempts on 429 errors                     |
| `MAFIA_BACKOFF_BASE_DELAY`      | 1.0     | —   | Base delay for exponential backoff (seconds)     |
| `MAFIA_ENABLE_STREAMING_FALLBACK` | false | —   | Retry streaming as non-streaming on error        |

#### Game Balance

| Variable                           | Default | Max | Description                                   |
|------------------------------------|---------|-----|-----------------------------------------------|
| `MAFIA_DETECTIVE_VOTE_WEIGHT`      | 2       | 5   | Detective's vote counts as X votes            |
| `MAFIA_VOTE_CONFIDENCE_THRESHOLD`  | 0.45    | —   | Min belief certainty before engine overrides   |
| `MAFIA_CONSENSUS_SHORTLIST_SIZE`   | 3       | 5   | Number of players in the pressure wagon       |
| `MAFIA_EVASION_BONUS`              | 0.08    | —   | Suspicion boost for evasive players           |

#### Session Resilience

| Variable                          | Default | Description                                        |
|-----------------------------------|---------|----------------------------------------------------|
| `MAFIA_SESSION_IDLE_THRESHOLD`    | 20.0    | Proactive session refresh after N seconds idle     |
| `MAFIA_SESSION_REFRESH_THRESHOLD` | 25.0    | Force refresh after N seconds cumulative backoff   |

---

## Verify Setup

Before running a game, verify your configuration:

```bash
python check.py
```

**Expected output:**

```
Endpoint : https://your-resource.services.ai.azure.com
Model    : gpt-4o-mini

Calling model...
✓ Ready to run the game.
```

**If it fails:** See [Troubleshooting](#troubleshooting).

---

## Running Your First Game

### Basic Run

```bash
python main.py
```

This starts a single game with 11 AI players. The game runs in your terminal with coloured output showing each player's actions.

### Recommended First Run

```bash
python main.py --reveal-roles --debug
```

This reveals all role assignments and shows full reasoning, so you can see exactly what's happening and learn how the system works.

### What Happens During a Game

1. **Setup** — 11 players are created with random roles, archetypes, personalities, and models
2. **Day Discussion** (2 rounds) — Each player speaks, reacting to others
3. **Day Vote** — Players vote to eliminate someone. Ties trigger a defence + re-vote
4. **Night** — Mafia kills, Detective investigates, Doctor protects
5. **Repeat** until Town eliminates all Mafia, or Mafia equals/outnumbers Town

A typical game runs 4–8 rounds and takes 5–15 minutes depending on API response times.

---

## Understanding the Output

### Player Action Box

Each agent's turn is displayed in a coloured box:

```
┌─ [Alice | Villager | Paranoid | TheGhost] ───────────────────┐
│ REASONING:                                                    │
│   Something about Bob's silence doesn't sit right. He's been │
│   quiet every round and only speaks to agree with the last... │
│                                                                │
│ ACTION:                                                        │
│   Bob's been real quiet. That's all I'm saying.               │
└───────────────────────────────────────────────────────────────┘
```

**Colour coding:**
- 🔴 **Red** — Mafia
- 🟡 **Yellow** — Detective
- 🟢 **Green** — Doctor
- 🔵 **Blue** — Villager
- ⚪ **White/Bold** — Narrator

**The four labels:**
1. **Name** — Player identity (Alice, Bob, etc.)
2. **Role** — Game role (Mafia, Detective, Doctor, Villager)
3. **Archetype** — Strategy style (Paranoid, Analytical, etc.)
4. **Personality** — Communication style (TheGhost, TheConfessor, etc.)

### Reasoning vs Action

- **REASONING** — Internal thinking, hidden from other agents in-game. Includes belief updates, strategy, deception planning.
- **ACTION** — What the agent actually says to the room. This is all other agents see.

In `--debug` mode, you see full reasoning. In normal mode, reasoning is truncated to 500 characters. In `--quiet` mode, only actions are shown.

### Vote Tally

```
═══════════════════════════════════════
         VOTE TALLY — Round 1
═══════════════════════════════════════
  Bob: ████████ 4 votes (Alice, Charlie, Diana, Eve)
  Frank: ████ 2 votes (Bob, Grace)
  Alice: ██ 1 vote (Frank)
  
  RESULT: Bob eliminated (Villager)
═══════════════════════════════════════
```

Weighted votes are shown when the Detective is alive (their vote counts double).

### Night Results

```
═══════════════════════════════════════
              NIGHT 1
═══════════════════════════════════════
  Alice was killed! (Role: Villager)
```

Or if the Doctor saved someone:

```
  The Doctor saved their target! No one died tonight.
```

### Game Over

```
═══════════════════════════════════════
           GAME OVER — Town Wins!
═══════════════════════════════════════
  Player Assignments:
    Alice    | Villager   | Paranoid      | TheGhost
    Bob      | Mafia      | Manipulative  | TheAnalyst
    ...
```

---

## CLI Reference

| Flag               | Type   | Default | Description                                                          |
|--------------------|--------|---------|----------------------------------------------------------------------|
| `--reveal-roles`   | bool   | False   | Show all role assignments (including hidden roles) at game start     |
| `--debug`          | bool   | False   | Show full agent reasoning without truncation                         |
| `--quiet`          | bool   | False   | Show action lines only, hide reasoning                               |
| `--seed <int>`     | int    | None    | Random seed for reproducible role/model/archetype/personality assignment |
| `--games <int>`    | int    | 1       | Run multiple games and print aggregate win statistics                |
| `--demo`           | bool   | False   | Restrict personalities to safe subset (TheGhost, TheAnalyst, TheConfessor, TheMartyr) |

### Usage Examples

```bash
# Standard game
python main.py

# See everything (great for learning)
python main.py --reveal-roles --debug

# Reproducible game (same seed = same setup)
python main.py --seed 42

# Run 10 games, compare same seed
python main.py --games 10 --seed 42

# Minimal output for batch runs
python main.py --games 100 --quiet

# Demo-safe personalities only
python main.py --demo

# Combine flags
python main.py --reveal-roles --debug --seed 42 --games 5
```

### Multi-Game Statistics

When using `--games N`, the system prints aggregate statistics after all games:

```
═══════════════════════════════════════
         AGGREGATE STATISTICS
═══════════════════════════════════════
  Games played: 10
  Town wins: 6 (60.0%)
  Mafia wins: 4 (40.0%)
═══════════════════════════════════════
```

---

## Running Tests

The test suite validates all core game mechanics without requiring Azure credentials or model deployments.

```bash
python -m unittest tests.test_refactor -v
```

**Test Suite Stats:**
- **332 tests** across **84 test classes**
- **3,718 lines** of test code
- **Zero** external dependencies (no Azure, no API calls)

### What's Tested

| Category                   | Tests | What It Validates                                           |
|----------------------------|-------|-------------------------------------------------------------|
| Vote parsing               | 16    | Self-vote prevention, intent detection, tie-break           |
| Personality constraints    | 24    | All 3 ban tiers, frequency caps, cap relaxation             |
| Action parsing             | 16    | REASONING/ACTION split, tool responses, edge cases          |
| Belief state               | 35    | Suspicion tracking, staleness, Iroh Protocol, overconfidence |
| Session resilience         | 30    | Session recovery, rate limits, health monitoring            |
| Game state                 | 40    | Win conditions, elimination, night actions, voting          |
| Prompt structure           | 18    | Discussion rules, voice markers, slang register             |
| Memory                     | 5     | Cross-game memory load/save/inject                          |
| Summary agent              | 20    | Recency weighting, compression, evidence extraction         |
| Rate limiter               | 12    | Error classification, backoff calculation                   |
| Game manager               | 18    | Archetype/personality assignment, constraint enforcement     |
| Settings                   | 8     | Environment variable parsing                                |
| MAF API compliance         | 11    | Import paths, dependency versions, provider API             |
| Other                      | 79    | Discussion format, narrator, middleware, frameworks, etc.   |

### Running Specific Tests

```bash
# Run a single test class
python -m unittest tests.test_refactor.TestSelfVotePrevention -v

# Run a single test method
python -m unittest tests.test_refactor.TestSelfVotePrevention.test_explicit_vote_tag_self -v

# Run all tests matching a pattern (requires pytest)
# pip install pytest
# pytest tests/test_refactor.py -k "belief" -v
```

---

## Project Layout

Here's what each file does and when you'd need to look at it:

### Files You'll Touch Most

| File                        | When to Look                                      |
|-----------------------------|---------------------------------------------------|
| `main.py`                   | Changing CLI arguments or game loop logic          |
| `.env`                      | Changing Azure endpoint or model                   |
| `config/model_registry.py`  | Adding new models to the pool                      |
| `config/settings.py`        | Changing default configuration values              |
| `engine/game_manager.py`    | Changing player count, names, or constraint rules  |

### Files You'll Read for Understanding

| File                        | What You'll Learn                                  |
|-----------------------------|---------------------------------------------------|
| `engine/orchestrator.py`    | How the game loop works (1,220 lines)              |
| `agents/belief_state.py`    | How agents track suspicion (650+ lines)            |
| `agents/base.py`            | How agent calls work with retries (596 lines)      |
| `prompts/builder.py`        | How system prompts are assembled                   |
| `prompts/archetypes.py`     | All 13 archetype definitions                       |
| `prompts/personalities.py`  | All 8 personality definitions                      |

### Files You Rarely Need to Touch

| File                        | Why                                                |
|-----------------------------|----------------------------------------------------|
| `agents/middleware.py`      | Session resilience works automatically              |
| `agents/rate_limiter.py`    | Rate limiting works automatically                  |
| `agents/providers.py`       | Context injection works automatically              |
| `agents/game_tools.py`      | Tool definitions are stable (40 lines)             |
| `engine/game_log.py`        | Terminal rendering is cosmetic                     |

---

## Key Concepts

### Roles

**Mafia** (2 players) — Know each other. Coordinate via the "Syndicate Channel" (shared reasoning). Must kill Town without being identified. Appear as Town during the day.

**Detective** (1 player) — Investigates one player per night, learning if they are Mafia or Innocent. Vote counts double. Can reveal findings publicly. Has the Iroh Protocol for emergency self-preservation.

**Doctor** (1 player) — Protects one player per night from being killed. Cannot protect the same player two nights in a row. Has the Iroh Protocol.

**Villager** (7 players) — No special abilities. Must identify Mafia through discussion and voting. Tracks vote patterns for anti-Mafia-steering.

### Archetypes (How Agents Think)

Archetypes introduce specific *flaws* in reasoning. This is intentional — perfect play is boring. Each archetype mirrors a real human cognitive bias:

| Archetype     | Human Equivalent                              | In-Game Effect                               |
|---------------|-----------------------------------------------|----------------------------------------------|
| Paranoid      | Anxiety-driven threat perception              | Sees threats everywhere, occasional spirals  |
| Overconfident | Confirmation bias                             | First read is final, ignores counter-evidence |
| Impulsive     | Snap judgment                                 | Acts on instinct, occasionally brilliant     |
| Passive       | Analysis paralysis                            | Requires overwhelming evidence               |
| Reactive      | Emotional reasoning                           | Accusations override strategy                |
| Contrarian    | Devil's advocacy                              | Questions consensus even when correct        |
| Analytical    | Methodical logic                              | Closest to optimal, failure is predictability |
| Methodical    | Evidence-based reasoning (Villager only)      | Thorough but slow, anchors on early reads    |
| Diplomatic    | Conflict avoidance                            | Softens accusations into suggestions         |
| Stubborn      | Anchoring bias                                | Round-one read is load-bearing               |
| Volatile      | Recency bias                                  | Position shifts with latest information      |
| Manipulative  | Social engineering                            | Engineers group conclusions through questions |
| Charming      | Likability heuristic                          | Builds warmth rapidly to influence           |

### Personalities (How Agents Talk)

Personalities control **only** communication style. They have zero effect on strategy:

| Personality   | Sound Like                                          |
|---------------|-----------------------------------------------------|
| TheGhost      | Minimal, declarative. "Bob's wrong." End of turn.  |
| TheAnalyst    | Count-framing. "Three people have now accused..."   |
| TheConfessor  | ADHD energy. Bold claims, partial walk-backs        |
| TheParasite   | Agreeable. Claims credit for others' reads          |
| TheMartyr     | Formal acceptance. "If you must eliminate me..."    |
| ThePerformer  | In-character. Non-sequiturs with conviction         |
| VibesVoter    | Emotional impressions. "Something feels off about..." |
| MythBuilder   | Narrative framing. "The story so far tells us..."   |

### Belief State

Each agent carries a `SuspicionState` — a dictionary of player names to suspicion scores (0.0 = definitely innocent, 1.0 = definitely Mafia). This is "structured intuition" — the agent assigns numbers based on conversational evidence, and the system uses those numbers for:
- Vote recommendations
- Iroh Protocol triggers
- Overconfidence gating
- Graceful degradation fallbacks

### The Iroh Protocol

Named after Uncle Iroh from Avatar. When a Detective or Doctor is about to be eliminated (other agents collectively suspect them), the system instructs them to reveal their role to survive. Three graduated levels:
- **Soft Hint** (suspicion ≥ 0.35) — Hint at having information
- **Hard Claim** (suspicion ≥ 0.45) — Claim role conditionally
- **Full Reveal** (suspicion ≥ 0.55) — Reveal role with all evidence

### Cross-Game Memory

After each game, agents record what they learned. Before the next game, those learnings are injected into their context. A Detective who correctly identified Mafia carries that pattern forward. Stored as JSON in the `memory/` directory.

### Middleware Stack

Five middleware layers handle cross-cutting concerns automatically:
1. **ResilientSessionMiddleware** — Recovers from expired sessions
2. **RateLimitMiddleware** — Handles API rate limits with backoff
3. **corporate_speak_middleware** — Forces natural speech
4. **ReasoningActionMiddleware** — Parses agent output structure
5. **BeliefUpdateMiddleware** — Extracts belief updates from reasoning

---

## Customisation Guide

### Adding Models to the Pool

Edit `config/model_registry.py`:

```python
AVAILABLE_MODELS = [
    ModelConfig(name="GPT-4O-MINI", model_id="gpt-4o-mini", short="4om"),
    ModelConfig(name="GPT-4O", model_id="gpt-4o", short="4o"),
    # Add more deployments here
]
```

> **Important:** Every model name must match an active deployment in your Azure AI Foundry project. Mismatches cause `DeploymentNotFound` errors.

### Changing Player Count

Edit `engine/game_manager.py`:

```python
PLAYER_NAMES = [
    "Alice", "Bob", "Charlie", "Diana", "Eve", "Frank",
    "Grace", "Hank", "Ivy", "Jack", "Kate",
    # Add or remove names — role distribution adjusts automatically
]
```

Role distribution scales with player count:
- ≤10 players: 2 Mafia
- ≤15 players: 3 Mafia
- 15+ players: 4 Mafia
- Detective added if 5+ players
- Doctor added if 6+ players
- Remaining slots: Villagers

### Adding New Archetypes

1. Add the archetype definition to `prompts/archetypes.py`:
```python
ARCHETYPES["YourArchetype"] = {
    "strategy_modifier": "How this archetype deviates from optimal play",
    "voice": {
        "prohibited": ["phrases", "this", "archetype", "never", "uses"],
        "register": "How this archetype sounds",
        "examples": ["Example phrase 1", "Example phrase 2"],
    },
}
```

2. Add to `ALL_ARCHETYPES` list
3. Optionally add to `VILLAGER_ARCHETYPES` if appropriate
4. Add any framework extras in `prompts/builder.py` (`ARCHETYPE_FRAMEWORK_EXTRAS`)
5. Add any combination bans in `engine/game_manager.py`
6. Add tests in `tests/test_refactor.py`

### Adding New Personalities

1. Add the personality definition to `prompts/personalities.py`:
```python
PERSONALITIES["YourPersonality"] = {
    "register": "Energy, cadence, sentence rhythm description",
    "voice_markers": {
        "sentence_length": "short/medium/long",
        "evidence_relationship": "how they relate to evidence",
        "deflection_style": "how they deflect accusations",
    },
    "prohibited": ["phrases", "this", "personality", "never", "uses"],
    "examples": ["5 in-game dialogue lines"],
    "when_accused": ["3 lines for when directly targeted"],
    "late_game_shift": "How behaviour changes in rounds 3+",
    "role_note": "Mafia vs Town differences",
    "performance_note": "How this personality wraps archetype strategy",
}
```

2. Add to `ALL_PERSONALITIES` list
3. Optionally add to `DEMO_PERSONALITIES` if safe for demo mode
4. Add to `CONSENSUS_PERSONALITIES` set if it follows consensus
5. Add any combination bans
6. Add tests

### Tuning Game Balance

All game balance constants are in `config/settings.py` and can be overridden via environment variables:

```dotenv
# Make Detective votes count triple
MAFIA_DETECTIVE_VOTE_WEIGHT=3

# Larger pressure wagon
MAFIA_CONSENSUS_SHORTLIST_SIZE=5

# Higher evasion penalty
MAFIA_EVASION_BONUS=0.15

# More aggressive rate limiting
MAFIA_MAX_CONCURRENT_CALLS=3
```

---

## Debugging Tips

### See Full Reasoning

```bash
python main.py --debug
```

This shows every agent's complete REASONING block. Look for:
- `BELIEF_UPDATE:` tags — how the agent is updating suspicion scores
- Mafia Threat Check answers — whether Mafia agents are maintaining cover
- Iroh Protocol mentions — whether special roles are considering reveal
- Frustration state — whether an agent is stuck in a reasoning loop

### Reproduce a Game

```bash
python main.py --seed 42 --reveal-roles
```

The `--seed` flag makes role/model/archetype/personality assignment deterministic. If you see an interesting game, note the seed number to replay it.

> **Note:** Same seed = same setup, but model responses may vary. The seed controls randomisation, not the AI's actual responses.

### Check Configuration

```bash
python check.py
```

If this fails, your Azure setup isn't right. Common issues:
- `az login` expired — run `az login` again
- Wrong endpoint URL — check your Foundry project settings
- Model deployment doesn't exist — check Foundry deployment names

### View Cross-Game Memory

```bash
cat memory/detective_learnings.json | python -m json.tool
cat memory/global_patterns.json | python -m json.tool
```

Memory files show what agents have learned across games. Delete the `memory/` directory to reset all learning.

### Watch Belief State Evolution

In `--debug` mode, watch for `BELIEF_UPDATE:` tags in reasoning. These show exactly when and why agents change their suspicion scores:

```
BELIEF_UPDATE: Bob=0.72 because Bob deflected when asked directly about his voting pattern
BELIEF_UPDATE: Alice=0.15 because Alice's accusation of Charlie was consistent with her earlier suspicion
```

---

## Troubleshooting

### `DeploymentNotFound` (404 Error)

```
openai.NotFoundError: Error code: 404 - {'error': {'message': 'The API deployment
for this resource does not exist.', 'code': 'DeploymentNotFound'}}
```

**Cause:** The model deployment name in your `.env` file doesn't match any deployment in your Azure AI Foundry project.

**Fix:**
1. Open your Azure AI Foundry project → Deployments
2. Copy the exact deployment name
3. Update `FOUNDRY_MODEL` in `.env` to match exactly
4. If you just created the deployment, wait ~5 minutes for it to activate
5. Run `python check.py` to verify

### `FOUNDRY_PROJECT_ENDPOINT is not set`

**Cause:** Missing or empty `.env` file.

**Fix:** Create `.env` in project root:
```dotenv
FOUNDRY_PROJECT_ENDPOINT=https://your-resource.services.ai.azure.com
```

### `az login` / Authentication Errors

**Cause:** Azure CLI not authenticated, or account lacks Foundry project access.

**Fix:**
```bash
az login
az account show  # Verify the correct account is active
```

### `check.py` prints `✗ Unexpected output`

**Cause:** Model responded but produced unexpected text.

**Fix:**
- Verify the model deployment is healthy in Azure AI Foundry
- Try a different model deployment
- Check if the model has content filters that might be blocking simple responses

### Rate Limit Errors (429)

**Cause:** Too many API calls hitting Azure rate limits.

**Fix:** Adjust rate limiting settings in `.env`:
```dotenv
MAFIA_MAX_CONCURRENT_CALLS=3    # Reduce concurrent calls
MAFIA_BACKOFF_BASE_DELAY=2.0    # Increase backoff delay
MAFIA_RATE_LIMIT_RETRIES=5      # Allow more retries
```

### Game Takes Very Long

**Cause:** API response times are slow, or rate limits are causing many retries.

**Fix:**
- Use `--quiet` mode for less terminal output overhead
- Reduce concurrent calls: `MAFIA_MAX_CONCURRENT_CALLS=3`
- Check your Azure model deployment's throughput limits

### Empty or Garbled Agent Responses

**Cause:** Content filter blocking game content, or model producing malformed output.

**Fix:**
- The system retries automatically (up to 2 times per call)
- Falls back to non-streaming if streaming fails
- Falls back to graceful degradation if all API calls fail
- Check Azure AI Foundry content filter settings

### `ModuleNotFoundError: No module named 'agent_framework'`

**Cause:** Dependencies not installed.

**Fix:**
```bash
pip install -r requirements.txt
```

### Windows Box-Drawing Characters Look Broken

**Cause:** Console doesn't support UTF-8 box-drawing characters.

**Fix:**
- Use Windows Terminal (supports UTF-8 natively)
- Or set `chcp 65001` before running
- The system attempts UTF-8 encoding automatically via `_configure_console_encoding()`

---

## Frequently Asked Questions

### Q: Does this cost money?

**A:** Yes — each agent call is an API call to Azure AI Foundry. A typical game makes 80–150 API calls (11 agents × ~6 rounds × 2–3 calls per agent per round). Cost depends on your model and Azure pricing tier.

### Q: Can I use local models instead of Azure?

**A:** Not directly. The system uses `agent-framework-foundry` which requires Azure AI Foundry. You would need to modify `config/model_registry.py` to use a different MAF client implementation.

### Q: How long does a game take?

**A:** 5–15 minutes depending on API response times and model speed. Games with rate limit retries may take longer.

### Q: Can I have more than 11 players?

**A:** Yes. Edit `PLAYER_NAMES` in `engine/game_manager.py`. Role distribution adjusts automatically. The system handles up to ~20 players well; beyond that, API rate limits may become an issue.

### Q: Can agents play the same game differently twice?

**A:** Yes, even with the same seed. The seed controls the initial assignment (roles, archetypes, etc.) but model responses are non-deterministic. Two runs with `--seed 42` will have the same setup but different conversations and outcomes.

### Q: What happens if the API goes down mid-game?

**A:** The system degrades gracefully. Each phase has a fallback:
- Discussion: agent passes turn
- Voting: votes using belief state
- Night: uses heuristic targeting
The game continues without crashing.

### Q: Can I watch a game that already happened?

**A:** Not directly — there's no replay system. Use `--seed` and `--reveal-roles` to re-create similar setups. The game log is printed to terminal only; pipe to a file to save it: `python main.py > game_log.txt 2>&1`

### Q: How does cross-game memory work?

**A:** After each game, agents record learnings (correct reads, failed strategies, etc.) to JSON files in `memory/`. Before the next game, those learnings are injected into agent context. Delete `memory/` to reset.

### Q: Why do agents sometimes sound weird?

**A:** The anti-AI writing system is aggressive. Agents use Gen Z/MLE slang, avoid corporate language, and have personality-specific speech patterns. If an agent sounds unnatural, it's usually the personality layer producing an unusual voice. This is intentional — homogeneous polished output would make all agents indistinguishable.

### Q: Can two agents have the same personality?

**A:** Regular personalities can appear up to 2 times per game. Consensus personalities (TheParasite, TheConfessor, ThePerformer, MythBuilder) are capped at 1 per game. No two agents can have the same archetype + personality combination — but two can share just the archetype or just the personality.

---

## Glossary

| Term                        | Definition                                                                     |
|-----------------------------|--------------------------------------------------------------------------------|
| **Archetype**               | One of 13 strategy profiles that controls how an agent reasons (imperfectly)   |
| **Belief State**            | An agent's dictionary of suspicion scores (0.0–1.0) for every other player     |
| **BeliefGraph**             | Scum-tell detector that flags suspicious voting/speaking patterns              |
| **Combination Ban**         | A rule preventing specific archetype–personality pairs from being assigned      |
| **Consensus Personality**   | TheParasite/TheConfessor/ThePerformer/MythBuilder — capped at 1 per game       |
| **ContextProvider**         | MAF class that injects dynamic context into each agent call                    |
| **Corporate-Speak**         | Formal/business language that the system actively suppresses                   |
| **Decisive Vote**           | The second vote in a tie-break where only non-tied players vote                |
| **Demo Mode**               | `--demo` flag restricting personalities to a safe subset                       |
| **Evasion Score**           | Count of times a player dodged a direct question                               |
| **Foundry**                 | Azure AI Foundry — the cloud service hosting the LLM deployments              |
| **Frequency Cap**           | Max 2 of any personality per game (1 for consensus personalities)              |
| **Frustration State**       | Triggered when beliefs don't change for 2+ rounds, forces new analysis        |
| **GameMemoryStore**         | Persistent cross-game learning storage                                        |
| **GenZ/MLE Register**       | Multicultural London English and Gen Z slang terms used for natural speech     |
| **Graceful Degradation**    | Fallback behaviour when API calls fail                                        |
| **Independent Archetype**   | Contrarian/Analytical/Impulsive/Stubborn — floor of 2 per game                |
| **Instahammer**             | Casting a decisive vote before discussion occurs — a scum tell                |
| **Iroh Protocol**           | Graduated role-reveal system for at-risk Detective/Doctor                     |
| **Late Bandwagon**          | Joining a vote majority without adding reasoning — a scum tell                |
| **MAF**                     | Microsoft Agent Framework                                                     |
| **Middleware**               | MAF middleware — cross-cutting concerns (resilience, parsing, enforcement)    |
| **Narrator**                | Omniscient impartial agent that announces game events                          |
| **Overconfidence Gate**      | Softens declarations when certainty is below 70%                              |
| **Personality**              | One of 8 communication styles (zero effect on strategy)                       |
| **Pressure Wagon**          | The top N most-suspected players (default 3)                                   |
| **Reasoning/Action**        | Output format: REASONING (internal thinking) and ACTION (visible to others)    |
| **Red Check**               | Detective finding that a player is Mafia                                      |
| **Redirect**                | Deflecting from the consensus target — a scum tell                            |
| **Scum Tell**               | Behavioural pattern that may indicate Mafia alignment                         |
| **Session Resilience**      | Automatic recovery from Azure session expiration                              |
| **Staleness Detection**     | Detects when beliefs haven't changed for 2+ rounds                            |
| **Structured Intuition**    | The system's approach to belief tracking — LLM assigns scores, system uses them |
| **SuspicionState**          | Per-agent dictionary of player → suspicion probability                        |
| **Syndicate Channel**       | Mafia partner coordination via shared previous-night reasoning                |
| **Temporal Slip**           | Agent referencing impossible past events — flagged as confabulation           |
| **Tie-Break**               | Two-stage protocol: defence phase → decisive re-vote                          |
| **Vote Coordination**       | System that recommends vote targets based on aggregated suspicion             |
| **Vote Weight**             | Detective's vote counts as N votes (default 2)                                |
