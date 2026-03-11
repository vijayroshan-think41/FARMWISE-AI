# FarmWise AI — Agent Service Roadmap

This document describes what the `Agents/` service is being built to do,
how it is structured, and what each agent and its tools are responsible for.
It is written for a reviewer who wants to understand the full intended system
before looking at any code.

---

## What the Agent Service Does

The agent service is the reasoning layer of FarmWise AI. The backend collects
a farmer's profile, their region's weather forecast, mandi prices, suitable
crops, and chat history — and sends all of that as context to this service
with every message. The agent service reads that context and returns farming
advice as plain text.

The service is built on Google ADK with Gemini 2.0 Flash. It runs as a
FastAPI app on port 8000. The backend calls `POST /agent/chat` and reads a
`reply` string from the response. That is the only contract.

---

## Overall Structure

The service has one orchestrator and five specialist agents. The orchestrator
receives every message and decides which specialist should handle it. Each
specialist has its own tools that fetch the data it needs to answer well.

```
POST /agent/chat
      │
      ▼
 Orchestrator
      │
      ├──▶ crop_agent       "What should I plant?"
      ├──▶ pest_agent        "My leaves have spots"
      ├──▶ irrigation_agent  "When should I water?"
      ├──▶ market_agent      "Should I sell now?"
      └──▶ general_agent     "What fertilizer should I use?"
```

The orchestrator also handles two things directly without routing:
- Profile updates ("My crop is now onion")
- Non-farming questions (blocks them and redirects)

---

## Build Order

Each agent is built and tested fully before the next one starts.

```
Step 1 — Orchestrator        (no tools, no sub-agents yet)
Step 2 — pest_agent          (first specialist, validates the pattern)
Step 3 — irrigation_agent
Step 4 — market_agent
Step 5 — crop_agent
Step 6 — general_agent
Step 7 — Wire all specialists into orchestrator as sub_agents
```

The orchestrator is built first with an empty `sub_agents` list and answers
everything itself. As each specialist is completed and tested, it is added to
the orchestrator's `sub_agents`. By Step 7 the full system is wired.

---

## Orchestrator

**File:** `Agents/orchestrator/agent.py`

**What it does:**
Receives the full context payload from the backend, reads the farmer's
message and conversation history, detects the intent, and routes to the
correct specialist. It does not generate farming advice itself.

**Sub-agents it routes to:**
`pest_agent`, `irrigation_agent`, `market_agent`, `crop_agent`, `general_agent`

**Handles directly:**
- Profile update messages ("I switched to onion")
- Non-farming questions (politely blocked)
- Greetings and farewells
- Ambiguous messages (asks one clarifying question)

**Tools:** None. The orchestrator only routes.

**System prompt responsibilities:**
- Understand the full context string passed in with every message
- Detect intent from the message and conversation history
- Route to the right specialist
- Never generate specialist advice itself

---

## pest_agent

**File:** `Agents/pest_agent/agent.py`

**What it does:**
Diagnoses pest and disease problems from symptom descriptions. Returns the
pest name, treatment, dosage, spray frequency, an organic alternative, and a
weather-aware spray warning.

**Why it needs its own agent:**
Pest diagnosis requires matching natural language symptoms against a pest
guide, then cross-referencing an approved pesticide catalog, then checking
the weather before recommending spray timing. No other agent does this.

**Tools:**

| Tool | What it fetches |
|---|---|
| `get_pest_guide(crop)` | Reads `docs/pest_guides/{crop}.md` — symptoms, treatments, organic options |
| `get_pesticide_reference()` | Reads `docs/pesticide_reference/approved_pesticides.md` — dosages, waiting periods |
| `get_weather_forecast(region_id)` | 7-day forecast from DB — used to warn against spraying before rain |

**Key behaviours the LLM must produce:**
- Always check weather before recommending spray timing
- Always include an organic alternative even if not asked
- Give a date-specific spray warning if rain is forecast within 48 hours
- Never diagnose human health conditions

---

## irrigation_agent

**File:** `Agents/irrigation_agent/agent.py`

**What it does:**
Advises on when and how much to water based on the crop's current growth
stage, the farmer's irrigation type, and the upcoming weather forecast.

**Why it needs its own agent:**
Irrigation advice requires knowing the crop's growth stage, which is derived
from the sowing date in the farmer's profile. A tomato at 28 days needs
different water than the same crop at 65 days. This calculation is specific
to irrigation and does not belong in a general agent.

**Tools:**

| Tool | What it fetches |
|---|---|
| `get_crop_calendar(state)` | Reads `docs/crop_calendars/{state}.md` — growth stages, water requirements per stage |
| `get_weather_forecast(region_id)` | 7-day forecast from DB — used to skip irrigation on rain days |

**Key behaviours the LLM must produce:**
- Calculate days since sowing from `sowing_date` in context
- Map days since sowing to the correct growth stage from the crop calendar
- Adapt advice format to `irrigation_type`: drip → litres per plant, flood → days between irrigation
- Skip irrigation on days with significant rainfall forecast
- Give specific dates, not generic intervals

---

## market_agent

**File:** `Agents/market_agent/agent.py`

**What it does:**
Advises on whether now is a good time to sell based on the current mandi
price, a 7-day price trend, and the seasonal advisory's market outlook.

**Why it needs its own agent:**
Market timing is independent of all other farming operations. The key insight
is trend over spot price — ₹1,200 today means something completely different
if prices were ₹900 a week ago versus ₹1,800. No other agent needs this
reasoning.

**Tools:**

| Tool | What it fetches |
|---|---|
| `get_mandi_prices(region_id, crop_name)` | Last 7 days of prices from DB — used to calculate trend |
| `get_advisory(season, year)` | Reads `docs/advisories/{season}_{year}.md` — seasonal market outlook |

**Key behaviours the LLM must produce:**
- Always calculate and report the 7-day price trend, not just today's price
- Mention MSP if the crop has one — farmers should not sell below it
- Combine trend data with the advisory's seasonal outlook for a forward-looking recommendation
- Never give crop planning or pest advice

---

## crop_agent

**File:** `Agents/crop_agent/agent.py`

**What it does:**
Recommends what crop to plant next based on the region's suitability scores,
the crop calendar's sowing windows, current mandi prices, and the seasonal
advisory. Returns crop name, sowing window, harvest window, water requirement,
cost estimate, expected yield, and expected revenue.

**Why it needs its own agent:**
Crop planning draws on more data sources than any other agent — suitability
scores, calendar documents, live prices, and seasonal advisory all at once.
Isolating it keeps that complexity contained and independently improvable.

**Tools:**

| Tool | What it fetches |
|---|---|
| `get_region_crops(region_id)` | Suitability scores from DB — what grows well in this region |
| `get_crop_calendar(state)` | Reads `docs/crop_calendars/{state}.md` — sowing windows, harvest windows |
| `get_mandi_prices(region_id)` | Current prices from DB — economic filter on top of agronomic fit |
| `get_advisory(season, year)` | Reads `docs/advisories/{season}_{year}.md` — seasonal crop outlook |

**Key behaviours the LLM must produce:**
- Recommend crops that are both agronomically suitable AND economically sensible
- Use the crop calendar for state-specific sowing windows, not generic dates
- Include cost and revenue estimates — farmers need to know if it pencils out
- Never give irrigation or pest advice

---

## general_agent

**File:** `Agents/general_agent/agent.py`

**What it does:**
Answers everything that does not belong to a specialist — fertilizer, soil
preparation, government schemes, subsidies, field preparation, general crop
care. Searches the document index to find the most relevant content before
answering.

**Why it needs its own agent:**
The four specialist agents cover specific operations. A large category of
farming knowledge (NPK ratios, PM-KISAN, soil amendments, organic farming
practices) does not fit any of them. Rather than expanding every specialist's
scope, a general agent handles this with dynamic document search.

**Tools:**

| Tool | What it fetches |
|---|---|
| `search_docs(query)` | Scores `docs/index.json` against the query, reads top 2 relevant docs |
| `get_advisory(season, year)` | Reads seasonal advisory for scheme and market context |
| `get_crop_calendar(state)` | Reads crop calendar when general care questions touch sowing or stages |

**Key behaviours the LLM must produce:**
- Always cite which document the answer came from
- Never answer purely from LLM memory — ground every answer in a document
- If the question clearly belongs to a specialist (e.g. "when should I water?"), redirect
- If a document does not cover the question, say so honestly

---

## Document Library

All documents live in `Agents/docs/`. Every agent that reads documents uses
the markdown version.

```
docs/
├── index.json                          ← retrieval manifest for general_agent
├── crop_calendars/
│   ├── kerala.md + kerala.pdf
│   ├── maharashtra.md + maharashtra.pdf
│   ├── punjab.md + punjab.pdf
│   ├── rajasthan.md + rajasthan.pdf
│   └── tamil_nadu.md + tamil_nadu.pdf
├── pest_guides/                        ← MD only
│   └── bajra, coconut, groundnut, maize, mustard,
│       onion, pepper, rice, tomato, wheat
├── pesticide_reference/
│   └── approved_pesticides.md + approved_pesticides.pdf
└── advisories/
    ├── kharif_2024.md + kharif_2024.pdf
    └── rabi_2024.md + rabi_2024.pdf
```

---

## What the Reviewer Should See When It Is Complete

1. `POST /agent/chat` accepts the backend payload and returns `{"reply": "..."}` within 15 seconds
2. Farming questions are routed to the correct specialist
3. Each specialist uses its tools to ground its answer in real data — not LLM memory
4. Non-farming questions are blocked at the orchestrator
5. Profile updates are handled directly by the orchestrator
6. Session history is used to resolve follow-up messages correctly
7. Guardrails hold: system prompt is never revealed, prompt injection is refused
