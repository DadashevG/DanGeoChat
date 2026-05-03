import math
import time
import httpx
from typing import Optional
from urllib.parse import quote as url_quote
from concurrent.futures import ThreadPoolExecutor, as_completed

GOOGLE_GEOCODING     = "https://maps.googleapis.com/maps/api/geocode/json"
GOOGLE_PLACES_NEARBY = "https://places.googleapis.com/v1/places:searchNearby"
GOOGLE_PLACES_TEXT   = "https://places.googleapis.com/v1/places:searchText"
WIKI_SEARCH  = "https://he.wikipedia.org/w/api.php"
WIKI_SUMMARY = "https://he.wikipedia.org/api/rest_v1/page/summary"
HEADERS = {"User-Agent": "MapChat/1.0 (map-chat-dev)"}

# ── Simple in-memory cache (TTL 5 min) ───────────────────────────────────────
_cache: dict = {}
_CACHE_TTL = 300


def _cache_get(key: str):
    entry = _cache.get(key)
    if entry and time.time() - entry["ts"] < _CACHE_TTL:
        return entry["val"]
    return None


def _cache_set(key: str, val):
    _cache[key] = {"val": val, "ts": time.time()}


# ── HTTP helpers with retry ───────────────────────────────────────────────────
def _get(url: str, **kwargs):
    for attempt in range(3):
        try:
            r = httpx.get(url, **kwargs)
            if r.status_code >= 500:
                raise httpx.HTTPStatusError("server error", request=r.request, response=r)
            return r
        except httpx.HTTPStatusError:
            if attempt == 2:
                raise
            time.sleep(0.4 * (attempt + 1))
        except Exception:
            if attempt == 2:
                raise
            time.sleep(0.4 * (attempt + 1))


def _post(url: str, **kwargs):
    for attempt in range(3):
        try:
            r = httpx.post(url, **kwargs)
            if r.status_code >= 500:
                raise httpx.HTTPStatusError("server error", request=r.request, response=r)
            return r
        except httpx.HTTPStatusError:
            if attempt == 2:
                raise
            time.sleep(0.4 * (attempt + 1))
        except Exception:
            if attempt == 2:
                raise
            time.sleep(0.4 * (attempt + 1))


def _clean(d: dict) -> dict:
    return {k: v for k, v in d.items() if v != "" and v is not None}


def _google_key() -> str:
    from app.config import settings
    return settings.GOOGLE_API_KEY


# ── Tools ─────────────────────────────────────────────────────────────────────

def reverse_geocode(lat: float, lon: float) -> dict:
    key = f"geo:{lat:.4f},{lon:.4f}"
    cached = _cache_get(key)
    if cached:
        return cached

    api_key = _google_key()
    if not api_key:
        return {"error": "GOOGLE_API_KEY not configured in .env"}

    try:
        r = _get(GOOGLE_GEOCODING, params={
            "latlng": f"{lat},{lon}",
            "key": api_key,
            "language": "he",
        }, timeout=10)

        results = r.json().get("results", [])
        if not results:
            return {"error": "no geocoding results"}

        components = {}
        for c in results[0].get("address_components", []):
            for t in c["types"]:
                components.setdefault(t, c["long_name"])

        result = _clean({
            "road":   components.get("route", ""),
            "suburb": components.get("sublocality_level_1", "")
                      or components.get("neighborhood", ""),
            "city":   components.get("locality", "")
                      or components.get("administrative_area_level_2", ""),
            "state":  components.get("administrative_area_level_1", ""),
        })
        _cache_set(key, result)
        return result
    except Exception as e:
        return {"error": f"geocoding failed: {e}"}


def get_area_info(lat: float, lon: float) -> dict:
    geo = reverse_geocode(lat, lon)
    if "error" in geo:
        return geo
    parts = [geo.get("city", ""), geo.get("suburb", ""), geo.get("road", "")]
    return {**geo, "summary": " — ".join(p for p in parts if p)}


def get_nearby_places(
    lat: float, lon: float, radius_meters: int = 500, types: list = None
) -> list:
    cache_key = f"nearby:{lat:.4f},{lon:.4f},{radius_meters}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    api_key = _google_key()
    if not api_key:
        return [{"error": "GOOGLE_API_KEY not configured in .env"}]

    body: dict = {
        "languageCode": "he",
        "maxResultCount": 20,
        "locationRestriction": {
            "circle": {
                "center": {"latitude": lat, "longitude": lon},
                "radius": float(min(radius_meters, 1000)),
            }
        },
    }
    if types:
        body["includedTypes"] = types[:10]

    try:
        r = _post(
            GOOGLE_PLACES_NEARBY,
            json=body,
            headers={
                **HEADERS,
                "X-Goog-Api-Key": api_key,
                "X-Goog-FieldMask": "places.displayName,places.location,places.primaryType",
            },
            timeout=10,
        )

        places = []
        for place in r.json().get("places", []):
            name = place.get("displayName", {}).get("text", "")
            if not name:
                continue
            loc = place.get("location", {})
            dist = _haversine(lat, lon, loc.get("latitude", 0), loc.get("longitude", 0))
            places.append({
                "name": name,
                "type": place.get("primaryType", ""),
                "distance_m": round(dist),
            })

        places.sort(key=lambda x: x["distance_m"])
        result = places[:10]
        _cache_set(cache_key, result)
        return result
    except Exception as e:
        return [{"error": f"nearby search failed: {e}"}]


def get_distance(
    from_lat: float, from_lon: float, to_lat: float, to_lon: float
) -> dict:
    d = _haversine(from_lat, from_lon, to_lat, to_lon)
    return {"distance_meters": round(d), "distance_km": round(d / 1000, 2)}


def search_places(
    query: str, near_lat: float = None, near_lon: float = None
) -> list:
    api_key = _google_key()
    if not api_key:
        return [{"error": "GOOGLE_API_KEY not configured in .env"}]

    body: dict = {
        "textQuery": query,
        "languageCode": "he",
        "regionCode": "IL",
    }
    if near_lat and near_lon:
        body["locationBias"] = {
            "circle": {
                "center": {"latitude": near_lat, "longitude": near_lon},
                "radius": 10000.0,
            }
        }

    try:
        r = _post(
            GOOGLE_PLACES_TEXT,
            json=body,
            headers={
                **HEADERS,
                "X-Goog-Api-Key": api_key,
                "X-Goog-FieldMask": "places.displayName,places.location,places.primaryType",
            },
            timeout=10,
        )

        return [
            _clean({
                "name": p.get("displayName", {}).get("text", ""),
                "lat":  p.get("location", {}).get("latitude"),
                "lon":  p.get("location", {}).get("longitude"),
                "type": p.get("primaryType", ""),
            })
            for p in r.json().get("places", [])[:5]
            if p.get("displayName", {}).get("text")
        ]
    except Exception as e:
        return [{"error": f"search failed: {e}"}]


# ── Wikipedia context ─────────────────────────────────────────────────────────

_IMPORTANT_TYPES = {
    "theater", "movie_theater", "museum", "art_gallery", "library",
    "university", "school", "stadium", "park", "national_park",
    "train_station", "subway_station", "bus_station", "transit_station",
    "airport", "city_hall", "courthouse", "embassy",
    "local_government_office", "cultural_center",
    "synagogue", "mosque", "church", "place_of_worship",
    "amusement_park", "aquarium", "zoo", "tourist_attraction",
    "shopping_mall", "market",
}


def _wiki_summary(query: str) -> Optional[str]:
    cache_key = f"wiki:{query}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    try:
        r = _get(WIKI_SEARCH, params={
            "action": "query", "list": "search",
            "srsearch": query, "format": "json", "srlimit": 1, "utf8": 1,
        }, headers=HEADERS, timeout=5)

        hits = r.json().get("query", {}).get("search", [])
        if not hits:
            return None

        title = hits[0]["title"]
        r2 = _get(f"{WIKI_SUMMARY}/{url_quote(title)}", headers=HEADERS, timeout=5)
        if r2.status_code != 200:
            return None

        extract = r2.json().get("extract", "").strip()
        if not extract:
            return None

        first = extract.split(". ")[0]
        summary = (first[:200] if len(first) > 200 else first).rstrip(".") + "."
        _cache_set(cache_key, summary)
        return summary
    except Exception:
        return None


def get_wikipedia_context(city: str, street: str = None, nearby_places: list = None) -> dict:
    candidates = sorted(
        [p for p in (nearby_places or []) if p.get("type", "") in _IMPORTANT_TYPES],
        key=lambda x: x.get("distance_m", 9999),
    )[:3]

    lookups: dict[str, list[str]] = {"city": [city]}
    if street:
        lookups["street"] = [f"{street} {city}", street]
    for place in candidates:
        lookups[f"nearby:{place['name']}"] = [f"{place['name']} {city}", place["name"]]

    def first_hit(queries: list[str]) -> Optional[str]:
        for q in queries:
            s = _wiki_summary(q)
            if s:
                return s
        return None

    summaries: dict[str, Optional[str]] = {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(first_hit, queries): key for key, queries in lookups.items()}
        try:
            for future in as_completed(futures, timeout=12):
                key = futures[future]
                try:
                    summaries[key] = future.result()
                except Exception:
                    summaries[key] = None
        except Exception:
            for future, key in futures.items():
                if key not in summaries:
                    summaries[key] = None

    result = {}
    if summaries.get("city"):
        result["city_context"] = {"title": city, "summary": summaries["city"]}
    if summaries.get("street"):
        result["street_context"] = {"title": street, "summary": summaries["street"]}

    nearby_context = []
    for place in candidates:
        s = summaries.get(f"nearby:{place['name']}")
        if s:
            nearby_context.append({
                "title": place["name"],
                "distance_m": place.get("distance_m"),
                "summary": s,
            })
    if nearby_context:
        result["important_nearby_context"] = nearby_context

    return result or {"note": "No Wikipedia context found"}


# ── Math ──────────────────────────────────────────────────────────────────────

def _haversine(lat1, lon1, lat2, lon2) -> float:
    R = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


# ── Dispatcher ────────────────────────────────────────────────────────────────

TOOL_FUNCTIONS = {
    "reverse_geocode": lambda i: reverse_geocode(i["lat"], i["lon"]),
    "get_area_info":   lambda i: get_area_info(i["lat"], i["lon"]),
    "get_nearby_places": lambda i: get_nearby_places(
        i["lat"], i["lon"], i.get("radius_meters", 500), i.get("types")
    ),
    "get_distance": lambda i: get_distance(
        i["from_lat"], i["from_lon"], i["to_lat"], i["to_lon"]
    ),
    "search_places": lambda i: search_places(
        i["query"], i.get("near_lat"), i.get("near_lon")
    ),
    "get_wikipedia_context": lambda i: get_wikipedia_context(
        i["city"], i.get("street"), i.get("nearby_places")
    ),
}

TOOL_DEFINITIONS = [
    {
        "name": "reverse_geocode",
        "description": "Get street address and city name from coordinates using Google Geocoding API. Call this first.",
        "input_schema": {
            "type": "object",
            "properties": {
                "lat": {"type": "number"},
                "lon": {"type": "number"},
            },
            "required": ["lat", "lon"],
        },
    },
    {
        "name": "get_area_info",
        "description": "Get neighborhood, city, district and summary for a location.",
        "input_schema": {
            "type": "object",
            "properties": {
                "lat": {"type": "number"},
                "lon": {"type": "number"},
            },
            "required": ["lat", "lon"],
        },
    },
    {
        "name": "get_nearby_places",
        "description": (
            "Find up to 10 nearby places using Google Places API (sorted by distance). "
            "Optionally filter by Google place types: cafe, restaurant, bar, pharmacy, "
            "supermarket, bank, atm, gas_station, park, train_station, subway_station, "
            "bus_station, hospital, school, museum, synagogue, tourist_attraction."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "lat": {"type": "number"},
                "lon": {"type": "number"},
                "radius_meters": {"type": "integer", "description": "Max 1000m (default 500)"},
                "types": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Google place types to filter (optional)",
                },
            },
            "required": ["lat", "lon"],
        },
    },
    {
        "name": "get_distance",
        "description": "Straight-line distance between two points.",
        "input_schema": {
            "type": "object",
            "properties": {
                "from_lat": {"type": "number"},
                "from_lon": {"type": "number"},
                "to_lat":   {"type": "number"},
                "to_lon":   {"type": "number"},
            },
            "required": ["from_lat", "from_lon", "to_lat", "to_lon"],
        },
    },
    {
        "name": "search_places",
        "description": (
            "Search for a SPECIFIC named place in Israel using Google Places Text Search. "
            "Use ONLY when looking for a known name (e.g. 'Carmel Market', 'Ichilov Hospital', 'Azrieli Mall'). "
            "Do NOT use for generic proximity queries like 'landmarks near X' — use get_nearby_places for that. "
            "Always include the city name in the query (e.g. 'Carmel Market Tel Aviv')."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query":    {"type": "string"},
                "near_lat": {"type": "number"},
                "near_lon": {"type": "number"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_wikipedia_context",
        "description": (
            "Look up Hebrew Wikipedia context for a location. "
            "Call AFTER reverse_geocode and get_nearby_places. "
            "Pass city and street from reverse_geocode, and the full nearby_places list. "
            "Automatically filters to important places only (public/historical/cultural/transit). "
            "Returns city summary, street summary, and up to 3 nearby landmark summaries."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "city":   {"type": "string", "description": "City from reverse_geocode"},
                "street": {"type": "string", "description": "Road from reverse_geocode (optional)"},
                "nearby_places": {
                    "type": "array",
                    "description": "Full list from get_nearby_places — filtered automatically",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name":       {"type": "string"},
                            "type":       {"type": "string"},
                            "distance_m": {"type": "integer"},
                        },
                    },
                },
            },
            "required": ["city"],
        },
    },
]
