# Map Chat — Stage A (Complete)

אפליקציית צ'אט גיאוגרפי — לוחצים על מפה, מקבלים תיאור של המיקום מ-Claude עם נתונים אמיתיים מ-Google ו-Wikipedia.

---

## ארכיטקטורה

```
frontend/index.html               backend/app/
  Leaflet.js map      →  POST /api/v1/ask  →  routers/chat.py
  MCP panel           ←  JSON response   ←  llm_service.py
  Web toggle                                     ↓  (tool-use loop ×7)
  2 question buttons                     geo_tools.py
  Badge display                            ↓           ↓
                                      Google APIs   Wikipedia
                                      (Geocoding +   (Hebrew REST
                                       Places New)    + Search)
```

---

## מצבי פעולה

| מצב | scenario_type | use_web_search | מה קורה |
|-----|--------------|----------------|---------|
| Claude Basic | `baseline` | false | Claude כללי בלבד |
| Claude Web | `web_grounded` | true | Claude + web_search מובנה |
| MCP | `mcp` | false | Claude + כלי Geo/Wikipedia |
| MCP + Web | `mcp` | true | כל הכלים ביחד |

---

## כלי MCP

| כלי | API | סטטוס | תיאור |
|-----|-----|-------|-------|
| `reverse_geocode` | Google Geocoding API | חובה | קואורדינטות → כתובת + עיר |
| `get_area_info` | Google Geocoding API | חובה | כתובת + משפט סיכום |
| `get_nearby_places` | Google Places API (New) — searchNearby | חובה | מקומות כלליים (ללא תחבורה) |
| `get_nearby_transit` | Google Places API (New) — searchNearby | חובה | תחנות תחבורה בלבד |
| `get_distance` | Haversine (מקומי) | חובה | מרחק בקו ישר |
| `search_places` | Google Places API (New) — searchText | optional | חיפוש מקום ספציפי בשם |
| `get_wikipedia_context` | Hebrew Wikipedia REST + Search | חובה | סיכומי עיר, רחוב, מקומות חשובים |

> `get_nearby_places` — מוציא תחבורה (`excludedTypes`). לשאילתות קרבה כלליות.
> `get_nearby_transit` — רק תחנות/תחנות (`includedTypes`), רדיוס ברירת מחדל 1000m.
> `search_places` — optional, רק לחיפוש מקום ספציפי בשם.

---

## כפתורי שאלה

| כפתור | שאלה ל-LLM | מתי להשתמש |
|-------|-----------|------------|
| מה יש כאן? | `מה יש כאן?` | תיאור כללי של המיקום |
| מה יש כאן תחבורתית? | `מה יש כאן תחבורתית?` | מידע תחבורה ציבורית |

---

## זרימת MCP

```
[משתמש לוחץ על מפה ובוחר שאלה]
        ↓
POST /api/v1/ask
  { lat, lon, question, scenario_type:"mcp", enabled_tools:[...], use_web_search:bool }
        ↓
generate_mcp_answer() — לולאה עד 7 סיבובים
   ├─ reverse_geocode      → Google Geocoding
   ├─ get_area_info        → Google Geocoding
   ├─ get_nearby_places    → Google Places searchNearby (excludedTypes: transit)
   ├─ get_nearby_transit   → Google Places searchNearby (includedTypes: transit)
   ├─ get_distance         → Haversine (מקומי)
   ├─ [search_places]      → Google Places searchText (optional)
   ├─ get_wikipedia_context→ Wikipedia parallel (ThreadPoolExecutor×6, timeout 12s)
   └─ [web_search]         → Anthropic built-in server-side (אם use_web_search=true)
        ↓
תשובה בעברית — פסקה אחת, ללא bullets
Badge: [💬 Claude · 🏙️ Google API · 🚌 Transit · 📖 Wikipedia]
```

---

## פאנל MCP

| Checkbox | כלים שנשלחים | מה מוחזר |
|----------|-------------|---------|
| 🏙️ מקומות קרובים | reverse_geocode, get_area_info, get_nearby_places, get_distance, search_places | מקומות כלליים |
| 🚌 תחבורה קרובה | get_nearby_transit | תחנות בלבד |
| 📖 Wikipedia | get_wikipedia_context | סיכומי Wikipedia |

---

## הפעלה

```bash
# Backend (port 8010)
cd backend && uvicorn app.main:app --reload --port 8010

# Frontend (port 8001)
cd frontend && python -m http.server 8001
```

VSCode: `Ctrl+Shift+B`

---

## קבצים מרכזיים

```
backend/
  app/
    routers/chat.py          — POST /api/v1/ask
    services/llm_service.py  — baseline / web_grounded / mcp
    services/geo_tools.py    — כלי MCP: TOOL_DEFINITIONS, TOOL_FUNCTIONS
    schemas.py               — QueryCreate (lat, lon, enabled_tools, use_web_search)
    config.py                — הגדרות מ-.env
  .env                       — ANTHROPIC_API_KEY, GOOGLE_API_KEY
  logs/llm_calls.log         — לוג מלא: בקשות, כלים, טוקנים

frontend/
  index.html                 — Leaflet + chat + MCP panel + 2 question buttons + badge
```

---

## .env נדרש

```env
ANTHROPIC_API_KEY=...
ANTHROPIC_MODEL=claude-haiku-4-5-20251001
GOOGLE_API_KEY=...
DATABASE_URL=sqlite:///./map_chat.db
```

Google Cloud Console — נדרש להפעיל:
- **Geocoding API**
- **Places API (New)**