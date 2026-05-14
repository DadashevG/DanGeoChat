# Map Chat

A geospatial chat app — click anywhere on the map, get a real-world description of the location powered by Claude with live data from Google APIs and Hebrew Wikipedia.

---

## Architecture

```
frontend/index.html               backend/app/
  Leaflet.js map      →  POST /api/v1/ask       →  routers/chat.py
  MCP panel           ←  JSON response          ←  llm_service.py
  Web toggle                                          ↓  (tool-use loop ×7)
  2 question buttons                          geo_tools.py
  Badge display                                ↓              ↓
  🧪 Exam modal       →  POST /api/v1/exam/*  →  routers/exam.py
                                                     ↓              ↓
                                              Google APIs      Wikipedia
                                              (Geocoding +     (Hebrew REST
                                               Places New)      + Search)
```

---

## Operation Modes

| Mode | scenario_type | use_web_search | What happens |
|------|--------------|----------------|--------------|
| Claude Basic | `baseline` | false | Claude general knowledge only |
| Claude Web | `web_grounded` | true | Claude + built-in web_search tool |
| MCP | `mcp` | false | Claude + Geo/Wikipedia tools |
| MCP + Web | `mcp` | true | All tools combined |

---

## MCP Tools

| Tool | API | Status | Description |
|------|-----|--------|-------------|
| `reverse_geocode` | Google Geocoding API | required | Coordinates → street address + city |
| `get_area_info` | Google Geocoding API | required | Address + one-line area summary |
| `get_nearby_places` | Google Places API (New) — searchNearby | required | General POIs, transit excluded |
| `get_nearby_transit` | Google Places API (New) — searchNearby | required | Transit stops and stations only |
| `get_distance` | Haversine (local) | required | Straight-line distance between two points |
| `search_places` | Google Places API (New) — searchText | optional | Search for a specific named place only |
| `get_wikipedia_context` | Hebrew Wikipedia REST + Search | required | City, street, and landmark summaries |

> `get_nearby_places` — excludes transit types (`excludedTypes`). Use for general proximity queries.
> `get_nearby_transit` — transit only (`includedTypes`), default radius 1000m.
> `search_places` — optional, called only when looking up a specific place by name (e.g. "Carmel Market", "Ichilov Hospital").

---

## Question Buttons

| Button | Question sent to LLM | Use case |
|--------|---------------------|----------|
| מה יש כאן? | `מה יש כאן?` | General location description |
| מה יש כאן תחבורתית? | `מה יש כאן תחבורתית?` | Public transit at this location |

---

## MCP Flow

```
[User clicks map and selects a question]
        ↓
POST /api/v1/ask
  { lat, lon, question, scenario_type:"mcp", enabled_tools:[...], use_web_search:bool }
        ↓
generate_mcp_answer() — loop up to 7 rounds
   ├─ reverse_geocode      → Google Geocoding
   ├─ get_area_info        → Google Geocoding
   ├─ get_nearby_places    → Google Places searchNearby (excludedTypes: transit)
   ├─ get_nearby_transit   → Google Places searchNearby (includedTypes: transit)
   ├─ get_distance         → Haversine (local)
   ├─ [search_places]      → Google Places searchText (optional)
   ├─ get_wikipedia_context→ Wikipedia parallel (ThreadPoolExecutor×6, timeout 12s)
   └─ [web_search]         → Anthropic built-in server-side (if use_web_search=true)
        ↓
Answer in Hebrew — single natural paragraph, no bullets
Badge: [💬 Claude · 🏙️ Google API · 🚌 Transit · 📖 Wikipedia]
```

---

## MCP Panel

| Checkbox | Tools sent | Returns |
|----------|-----------|---------|
| 🏙️ מקומות קרובים | reverse_geocode, get_area_info, get_nearby_places, get_distance, search_places | General POIs |
| 🚌 תחבורה קרובה | get_nearby_transit | Transit stops only |
| 📖 Wikipedia | get_wikipedia_context | Wikipedia summaries |

---

## Evaluation / Test Suite (🧪)

The **Exam modal** (`🧪 בדיקה` button) runs batch evaluations over a fixed set of 30 locations across Gush Dan.

### Test set

`exam/Places.csv` — 30 Israeli locations across Tel Aviv, Jaffa, Ramat Gan, Bnei Brak, Petah Tikva, Holon, Bat Yam, Ra'anana, Kfar Saba.

### Exam flow

```
[Select places + tools + question + runs]
        ↓
runExam() — sequential POST /api/v1/ask per place
        ↓
[Optional] generateJudgingReport()
   ├─ POST /api/v1/exam/judge-one (per place, with progress bar)
   │     ├─ Gemini (transport judge) — factuality, coverage, specificity, distance, hallucinations
   │     └─ OpenAI GPT-4o mini (language judge) — clarity, conciseness, relevance, usefulness, density
   └─ POST /api/v1/exam/build-report → saves HTML report to evaluation/
```

### Scoring formula

```
FinalScore = 0.7 × TransportScore (Gemini /100) + 0.3 × LanguageScore (OpenAI /100)
```

### Exam endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/exam/places` | GET | Returns the full test-set from `exam/Places.csv` |
| `/api/v1/exam/judge-one` | POST | Judge a single result (Gemini + OpenAI in parallel) |
| `/api/v1/exam/build-report` | POST | Build and save HTML report from pre-computed judgments |
| `/api/v1/exam/report` | POST | Judge all results and build report in one call |

---

## Running Locally

```bash
# Backend (port 8010)
cd backend && uvicorn app.main:app --reload --port 8010

# Frontend (port 8001)
cd frontend && python -m http.server 8001
```

Or in VSCode: `Ctrl+Shift+B`

---

## Key Files

```
backend/
  app/
    routers/chat.py           — POST /api/v1/ask endpoint
    routers/exam.py           — exam endpoints + judging logic + HTML report builder
    services/llm_service.py   — baseline / web_grounded / mcp logic
    services/geo_tools.py     — all MCP tools: TOOL_DEFINITIONS, TOOL_FUNCTIONS
    schemas.py                — QueryCreate (lat, lon, enabled_tools, use_web_search)
    config.py                 — settings loaded from .env
  .env                        — API keys (never commit)
  logs/llm_calls.log          — full request/tool/token log

frontend/
  index.html                  — Leaflet map + chat + MCP panel + buttons + badge + exam modal

exam/
  Places.csv                  — 30 test locations (INDEX, PLACE, city, X, Y)

evaluation/
  report_*.html               — generated judging reports (auto-saved, not committed)
```

---

## Environment Variables (.env)

```env
ANTHROPIC_API_KEY=...
ANTHROPIC_MODEL=claude-haiku-4-5-20251001
GOOGLE_API_KEY=...
DATABASE_URL=sqlite:///./map_chat.db

# Required for the exam judging report
OPENAI_API_KEY=...
GEMINI_API_KEY=...
```

Enable both in Google Cloud Console:
- **Geocoding API**
- **Places API (New)**

---

## License

MIT — see [LICENSE](LICENSE)
