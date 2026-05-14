import csv
import json
import re
import html as html_module
import asyncio
from pathlib import Path
from datetime import datetime
from typing import List
import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/v1", tags=["exam"])

EXAM_FILE = Path(__file__).resolve().parents[3] / "exam" / "Places.csv"
REPORTS_DIR = Path(__file__).resolve().parents[3] / "evaluation"

from app.config import settings
_OPENAI_KEY = settings.OPENAI_API_KEY
_GEMINI_KEY = settings.GEMINI_API_KEY

# ── Prompts (use {{ }} for literal braces in the JSON example) ─────────────────

_GEMINI_PROMPT = """\
You are a strict transportation factuality evaluation judge.

Your task is to evaluate ONLY the transportation quality and geographic grounding of a Hebrew paragraph describing transportation near coordinates X,Y.

You must evaluate:

* transportation correctness
* nearby transport infrastructure
* geographic grounding
* specificity to the exact location
* transport mode correctness
* distance realism
* hallucinations

Do NOT evaluate writing style unless it directly harms transportation clarity.

Coordinate:
{lat}, {lon}

Ground truth transport data:
{ground_truth}

Candidate paragraph:
{answer}

Evaluation rubric:

1. Factuality (0-35)

* Are the mentioned transport entities real and geographically correct?
* Penalize invented stations, incorrect infrastructure, wrong transport modes, or false geographic claims.
* Penalize confusing heavy rail, light rail, metro, cable car, buses, etc.

2. Transport coverage (0-25)

* Does the paragraph mention the major nearby transportation assets?
* Consider rail stations, light rail, bus stops, roads, transit hubs, bike infrastructure, etc.

3. Spatial specificity (0-20)

* Does the paragraph clearly demonstrate understanding of the exact location?
* Reward mention of nearby landmarks, named stations, major roads, districts, or hubs.
* Penalize generic city-wide descriptions that could apply to many locations.

4. Distance accuracy (0-10)

* Are distances approximately correct and geographically reasonable?

5. Hallucination control (0-10)

* Penalize invented infrastructure, fabricated nearby services, outdated transport systems, or unsupported claims.

Important instructions:

* Be strict and conservative.
* Generic transportation descriptions should receive low scores.
* High scores require strong geographic grounding and high information value.
* Do not reward answers simply for sounding informative.
* Do not assign scores above 90 unless the paragraph is highly accurate, localized, concise, and transport-focused.

Score calibration guidelines:

90-100: Exceptional transportation paragraph with highly accurate, localized, dense, and relevant transport information.
75-89: Good transportation paragraph with minor verbosity, omissions, or small relevance issues.
50-74: Moderately useful paragraph with generic descriptions, weak grounding, or noticeable factual/relevance issues.
25-49: Weak transportation grounding, poor specificity, or multiple factual problems.
0-24: Severely flawed, generic, hallucinated, or misleading paragraph.

Return ONLY valid JSON:

{{
  "factuality": 0,
  "transport_coverage": 0,
  "spatial_specificity": 0,
  "distance_accuracy": 0,
  "hallucination_control": 0,
  "transport_score": 0,
  "hallucinated_entities": [],
  "missing_major_assets": [],
  "wrong_transport_claims": [],
  "error_tags": [],
  "short_explanation": ""
}}"""

_OPENAI_PROMPT = """\
You are a strict Hebrew language and user usefulness evaluation judge.

Your task is to evaluate ONLY the readability, usefulness, relevance, and information quality of a Hebrew paragraph describing transportation near coordinates X,Y.

Do NOT heavily judge transportation factuality unless it significantly harms usefulness.

Focus on:

* clarity
* conciseness
* relevance
* usefulness
* information density
* geographic contextual awareness

Coordinate:
{lat}, {lon}

Candidate paragraph:
{answer}

Evaluation rubric:

1. Clarity (0-25)

* Is the paragraph easy to understand?
* Is the structure coherent, readable, and natural?

2. Conciseness (0-20)

* Is the paragraph concise and efficient?
* Penalize verbosity, repetition, filler, and unnecessary narration.

3. Relevance (0-20)

* Does the paragraph stay focused on transportation near the coordinates?
* Penalize Wikipedia-style history, demographics, tourism descriptions, or unrelated city facts.

4. User usefulness (0-20)

* Is the information practically useful to someone near this location?
* Reward actionable nearby transportation information.

5. Information density and locality (0-15)

* Reward paragraphs with concrete nearby infrastructure, landmarks, roads, or stations.
* Penalize generic transportation descriptions that could apply to many locations.
* Penalize low-signal or vague explanations.

Important instructions:

* Be strict and conservative with high scores.
* Do not reward paragraphs simply for sounding informative.
* High scores require concise, high-signal, location-aware transportation descriptions.
* Penalize unnecessary historical or geographic narration.
* Prefer dense, practical, coordinate-aware information.

Score calibration guidelines:

90-100: Exceptional paragraph. Highly concise, relevant, location-aware, and maximally useful.
75-89: Good paragraph with minor verbosity or relevance issues.
50-74: Moderately useful paragraph with noticeable filler, generic content, or weak locality.
25-49: Weak usefulness, low information density, or poor relevance.
0-24: Very poor, generic, confusing, or largely unhelpful paragraph.

Return ONLY valid JSON:

{{
  "clarity": 0,
  "conciseness": 0,
  "relevance": 0,
  "user_usefulness": 0,
  "information_density_locality": 0,
  "language_score": 0,
  "verbosity_issues": [],
  "irrelevant_information": [],
  "quality_issues": [],
  "short_explanation": ""
}}"""


# ── Helpers ────────────────────────────────────────────────────────────────────

def _score_color(score: float) -> str:
    if score >= 80:
        return "#28a745"
    if score >= 60:
        return "#ffc107"
    if score >= 40:
        return "#fd7e14"
    return "#dc3545"


def _get_ground_truth_text(lat: float, lon: float) -> str:
    try:
        from app.services.geo_tools import get_nearby_transit, get_nearby_places
        transit = get_nearby_transit(lat=lat, lon=lon, radius_meters=1000)
        places = get_nearby_places(lat=lat, lon=lon, radius_meters=500)
        lines: List[str] = []
        if isinstance(transit, list) and transit and "error" not in transit[0]:
            lines.append("Nearby transit stops:")
            for t in transit[:8]:
                lines.append(f"  - {t.get('name','')} ({t.get('type','')}, ~{t.get('distance_m','?')}m)")
        if isinstance(places, list) and places and "error" not in places[0]:
            lines.append("Nearby places:")
            for p in places[:5]:
                lines.append(f"  - {p.get('name','')} ({p.get('type','')}, ~{p.get('distance_m','?')}m)")
        return "\n".join(lines) if lines else "Ground truth not available"
    except Exception as e:
        return f"Ground truth unavailable: {e}"


# Only models that exist in v1beta AND support google_search grounding
_GEMINI_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.5-flash-preview-05-20",
]


async def _call_gemini(lat: float, lon: float, answer: str, ground_truth: str) -> dict:
    prompt = _GEMINI_PROMPT.format(lat=lat, lon=lon, ground_truth=ground_truth, answer=answer)
    last_err = ""
    for model in _GEMINI_MODELS:
        for attempt in range(4):
            try:
                body: dict = {"contents": [{"parts": [{"text": prompt}]}]}
                # google_search grounding — supported by gemini-2.x
                if model.startswith("gemini-2"):
                    body["tools"] = [{"google_search": {}}]
                async with httpx.AsyncClient() as client:
                    res = await client.post(
                        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={_GEMINI_KEY}",
                        json=body,
                        timeout=60,
                    )
                if res.status_code in (503, 529):
                    last_err = f"{res.status_code} unavailable (model={model}, attempt={attempt+1})"
                    await asyncio.sleep(3 * (attempt + 1))
                    continue
                if res.status_code in (404, 400):
                    last_err = f"HTTP {res.status_code} for {model}: {res.text[:150]}"
                    break  # this model won't work, try next
                if res.status_code != 200:
                    last_err = f"HTTP {res.status_code}: {res.text[:150]}"
                    await asyncio.sleep(2)
                    continue
                text = res.json()["candidates"][0]["content"]["parts"][0]["text"]
                text = re.sub(r"```(?:json)?\s*|\s*```", "", text).strip()
                data = json.loads(text)
                data["transport_score"] = (
                    data.get("factuality", 0) + data.get("transport_coverage", 0) +
                    data.get("spatial_specificity", 0) + data.get("distance_accuracy", 0) +
                    data.get("hallucination_control", 0)
                )
                return data
            except (json.JSONDecodeError, KeyError) as e:
                last_err = f"parse error ({model}): {e}"
                break
            except Exception as e:
                last_err = str(e)
                await asyncio.sleep(2)
    return {
        "error": last_err, "factuality": 0, "transport_coverage": 0,
        "distance_accuracy": 0, "hallucination_control": 0, "transport_score": 0,
        "hallucinated_entities": [], "missing_major_assets": [],
        "wrong_transport_claims": [], "short_explanation": f"Gemini error: {last_err}",
    }


async def _call_openai(lat: float, lon: float, answer: str) -> dict:
    prompt = _OPENAI_PROMPT.format(lat=lat, lon=lon, answer=answer)
    last_err = ""
    for attempt in range(3):
        try:
            async with httpx.AsyncClient() as client:
                res = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {_OPENAI_KEY}", "Content-Type": "application/json"},
                    json={
                        "model": "gpt-4o-mini",
                        "messages": [{"role": "user", "content": prompt}],
                        "response_format": {"type": "json_object"},
                    },
                    timeout=45,
                )
            if res.status_code in (429, 500, 503):
                last_err = f"HTTP {res.status_code}"
                await asyncio.sleep(2 ** attempt)
                continue
            if res.status_code != 200:
                raise ValueError(f"HTTP {res.status_code}: {res.text[:200]}")
            data = json.loads(res.json()["choices"][0]["message"]["content"])
            data["language_score"] = (
                data.get("clarity", 0) + data.get("conciseness", 0) +
                data.get("relevance", 0) + data.get("user_usefulness", 0) +
                data.get("information_density_locality", 0)
            )
            return data
        except (json.JSONDecodeError, KeyError) as e:
            last_err = f"parse error: {e}"
            break
        except Exception as e:
            last_err = str(e)
            await asyncio.sleep(2 ** attempt)
    return {
        "error": last_err, "clarity": 0, "conciseness": 0,
        "relevance": 0, "user_usefulness": 0, "language_score": 0,
        "verbosity_issues": [], "irrelevant_information": [],
        "short_explanation": f"OpenAI error: {last_err}",
    }


# ── HTML Report ────────────────────────────────────────────────────────────────

_CSS = """
body{font-family:'Segoe UI',Tahoma,Arial,sans-serif;background:#f0f2f5;color:#333;margin:0;padding:20px;direction:rtl}
.hdr{background:linear-gradient(135deg,#667eea,#764ba2);color:#fff;padding:22px 28px;border-radius:12px;margin-bottom:20px}
.hdr h1{margin:0 0 8px;font-size:21px}
.meta{display:flex;gap:16px;font-size:12px;opacity:.88;flex-wrap:wrap;margin-top:6px}
.meta b{opacity:.75;font-weight:normal}
.card{background:#fff;border-radius:10px;padding:18px 22px;margin-bottom:18px;box-shadow:0 2px 8px rgba(0,0,0,.07)}
.card h2{margin:0 0 12px;font-size:15px;color:#444}
table{width:100%;border-collapse:collapse;font-size:13px}
thead th{background:#f5f5f5;padding:9px 12px;text-align:right;border-bottom:2px solid #e0e0e0;font-weight:600}
tbody td{padding:8px 12px;border-bottom:1px solid #f0f0f0}
tbody tr:last-child td{border-bottom:none}
tbody tr:hover td{background:#fafafa}
a{color:#667eea;text-decoration:none}
a:hover{text-decoration:underline}
.pc{background:#fff;border-radius:10px;padding:20px 24px;margin-bottom:18px;box-shadow:0 2px 8px rgba(0,0,0,.07)}
.pc h2{margin:0 0 4px;font-size:17px;display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.ctag{font-size:12px;font-weight:normal;color:#888;background:#f0f0f0;padding:2px 8px;border-radius:10px}
.fbadge{color:#fff;padding:4px 12px;border-radius:12px;font-size:14px;font-weight:bold}
.coords{font-size:11px;color:#aaa;margin-bottom:12px}
.ans{background:#fafbff;border:1px solid #e8ecf8;border-radius:8px;padding:12px 16px;margin-bottom:14px;font-size:13px;line-height:1.65}
.anslbl{font-size:10px;color:#aaa;margin-bottom:4px;text-transform:uppercase;letter-spacing:.5px}
.jgrid{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:12px}
.jbox{border:1px solid #e0e0e0;border-radius:8px;padding:14px 16px}
.jbox.gm{border-color:#4285f4;background:#f8fbff}
.jbox.oa{border-color:#10a37f;background:#f4fff9}
.jbox h3{margin:0 0 10px;font-size:13px}
.st td{padding:3px 8px;font-size:12px;border-bottom:none}
.st tr.tot td{border-top:1px solid #ddd;padding-top:7px;font-weight:bold}
.issue{font-size:11px;color:#666;margin:3px 0}
.expl{font-size:12px;color:#666;font-style:italic;border-top:1px solid #eee;padding-top:8px;margin-top:8px}
.formula{background:#f8f9fa;border-radius:6px;padding:9px 14px;font-size:13px}
.avgsep td{border-top:2px solid #ccc;background:#f5f5f5;font-weight:bold}
.runsep{text-align:center;font-size:13px;font-weight:bold;color:#667eea;background:#f0f0ff;border-radius:8px;padding:8px;margin:10px 0}
.rbadge{display:inline-block;background:#667eea;color:#fff;font-size:10px;padding:1px 7px;border-radius:10px;margin-left:5px;font-weight:normal;vertical-align:middle}
.noise-box{background:#fff8e1;border:1px solid #ffe082;border-radius:8px;padding:10px 14px;margin-bottom:12px;font-size:12px}
.noise-box h4{margin:0 0 6px;font-size:13px;color:#795548}
.noise-row{display:flex;gap:6px;flex-wrap:wrap;margin:4px 0}
.noise-chip{background:#fff;border:1px solid #ddd;border-radius:6px;padding:3px 8px;font-size:11px;color:#555}
@media(max-width:650px){.jgrid{grid-template-columns:1fr}}
"""


def _esc(t: str) -> str:
    return html_module.escape(str(t)).replace("\n", "<br>")


def _issues(items: list, label: str) -> str:
    if not items:
        return ""
    return f'<div class="issue"><strong>{label}:</strong> {", ".join(_esc(str(x)) for x in items)}</div>'


def _sc(val: float, mx: float) -> str:
    return _score_color(val / mx * 100 if mx else 0)


def _one_card(j: dict, question: str, run_label: str = "") -> str:
    p = j["place"]
    g = j["gemini"]
    o = j["openai"]
    fs = j["final_score"]
    ts = g.get("transport_score", 0)
    ls = o.get("language_score", 0)
    g_iss = (
        _issues(g.get("hallucinated_entities", []), "🔴 ישויות מומצאות") +
        _issues(g.get("missing_major_assets", []), "🟡 נכסים חסרים") +
        _issues(g.get("wrong_transport_claims", []), "⚠️ טענות שגויות") +
        _issues(g.get("error_tags", []), "🏷️ תגיות שגיאה")
    )
    o_iss = (
        _issues(o.get("verbosity_issues", []), "📝 ורבוזיות") +
        _issues(o.get("irrelevant_information", []), "🔍 מידע לא רלוונטי") +
        _issues(o.get("quality_issues", []), "⚠️ בעיות איכות")
    )
    badge = f'<span class="rbadge">{_esc(run_label)}</span>' if run_label else ""
    return f"""
<div class="jgrid" style="margin-bottom:12px">
  <div class="jbox gm">
    <h3>🔵 Gemini — תחבורה {badge}</h3>
    <table class="st">
      <tr><td>Factuality</td><td>/35</td><td style="color:{_sc(g.get('factuality',0),35)};font-weight:bold">{g.get('factuality',0)}</td></tr>
      <tr><td>Transport Coverage</td><td>/25</td><td style="color:{_sc(g.get('transport_coverage',0),25)};font-weight:bold">{g.get('transport_coverage',0)}</td></tr>
      <tr><td>Spatial Specificity</td><td>/20</td><td style="color:{_sc(g.get('spatial_specificity',0),20)};font-weight:bold">{g.get('spatial_specificity',0)}</td></tr>
      <tr><td>Distance Accuracy</td><td>/10</td><td style="color:{_sc(g.get('distance_accuracy',0),10)};font-weight:bold">{g.get('distance_accuracy',0)}</td></tr>
      <tr><td>Hallucination Control</td><td>/10</td><td style="color:{_sc(g.get('hallucination_control',0),10)};font-weight:bold">{g.get('hallucination_control',0)}</td></tr>
      <tr class="tot"><td colspan="2">TransportScore</td><td style="color:{_score_color(ts)};font-size:1.15em">{ts}/100</td></tr>
    </table>
    {g_iss}<div class="expl">💬 {_esc(g.get('short_explanation',''))}</div>
  </div>
  <div class="jbox oa">
    <h3>🟢 OpenAI — שפה {badge}</h3>
    <table class="st">
      <tr><td>Clarity</td><td>/25</td><td style="color:{_sc(o.get('clarity',0),25)};font-weight:bold">{o.get('clarity',0)}</td></tr>
      <tr><td>Conciseness</td><td>/20</td><td style="color:{_sc(o.get('conciseness',0),20)};font-weight:bold">{o.get('conciseness',0)}</td></tr>
      <tr><td>Relevance</td><td>/20</td><td style="color:{_sc(o.get('relevance',0),20)};font-weight:bold">{o.get('relevance',0)}</td></tr>
      <tr><td>User Usefulness</td><td>/20</td><td style="color:{_sc(o.get('user_usefulness',0),20)};font-weight:bold">{o.get('user_usefulness',0)}</td></tr>
      <tr><td>Info Density &amp; Locality</td><td>/15</td><td style="color:{_sc(o.get('information_density_locality',0),15)};font-weight:bold">{o.get('information_density_locality',0)}</td></tr>
      <tr class="tot"><td colspan="2">LanguageScore</td><td style="color:{_score_color(ls)};font-size:1.15em">{ls}/100</td></tr>
    </table>
    {o_iss}<div class="expl">💬 {_esc(o.get('short_explanation',''))}</div>
  </div>
</div>
<div class="ans" style="margin-bottom:8px">
  <div class="anslbl">תשובה ({_esc(j.get('question', question))})</div>
  {_esc(j['answer'])}
</div>
<div class="formula">FinalScore = 0.7 × {ts} + 0.3 × {ls} = <strong style="color:{_score_color(fs)};font-size:1.1em">{fs:.1f}</strong></div>
"""


def _noise_box(group: list) -> str:
    fs_list = [j["final_score"] for j in group]
    ts_list = [j["gemini"].get("transport_score", 0) for j in group]
    ls_list = [j["openai"].get("language_score", 0) for j in group]
    n = len(group)

    def _std(vals):
        if n < 2:
            return 0.0
        mean = sum(vals) / n
        return (sum((v - mean) ** 2 for v in vals) / (n - 1)) ** 0.5

    chips_fs = " ".join(
        f'<span class="noise-chip" style="color:{_score_color(fs)}">ריצה {j["place"].get("run",i+1)}: {fs:.1f}</span>'
        for i, (j, fs) in enumerate(zip(group, fs_list))
    )
    return f"""
<div class="noise-box">
  <h4>📊 ניתוח נויז ({n} ריצות)</h4>
  <div class="noise-row">{chips_fs}</div>
  <div style="font-size:11px;color:#666;margin-top:6px">
    FinalScore — ממוצע: <b>{sum(fs_list)/n:.1f}</b> &nbsp;|&nbsp;
    טווח: <b>{max(fs_list)-min(fs_list):.1f}</b> &nbsp;|&nbsp;
    סטיית תקן: <b>{_std(fs_list):.1f}</b>
    &nbsp;&nbsp;|&nbsp;&nbsp;
    Transport avg: <b>{sum(ts_list)/n:.1f}</b> טווח: <b>{max(ts_list)-min(ts_list)}</b>
    &nbsp;|&nbsp;
    Language avg: <b>{sum(ls_list)/n:.1f}</b> טווח: <b>{max(ls_list)-min(ls_list)}</b>
  </div>
</div>"""


def _build_html(judgments: list, question: str, scenario_type: str, tools: List[str]) -> str:
    from collections import defaultdict

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    tools_str = ", ".join(tools) if tools else "ללא כלים (Baseline)"
    max_run = max((j["place"].get("run", 1) for j in judgments), default=1)
    multi = max_run > 1

    # group by place index, preserving order
    by_place: dict = defaultdict(list)
    seen_order: list = []
    for j in judgments:
        idx = j["place"]["index"]
        if idx not in by_place:
            seen_order.append(idx)
        by_place[idx].append(j)

    # ── summary table ────────────────────────────────────────────
    rows = ""
    grand = [0.0, 0.0, 0.0]
    place_count = len(seen_order)

    for idx in seen_order:
        group = by_place[idx]
        p = group[0]["place"]
        ts_list = [g["gemini"].get("transport_score", 0) for g in group]
        ls_list = [g["openai"].get("language_score", 0) for g in group]
        fs_list = [g["final_score"] for g in group]
        ts_avg = sum(ts_list) / len(ts_list)
        ls_avg = sum(ls_list) / len(ls_list)
        fs_avg = sum(fs_list) / len(fs_list)
        grand[0] += ts_avg; grand[1] += ls_avg; grand[2] += fs_avg

        if multi:
            noise_ts = f' <small style="color:#999">±{max(ts_list)-min(ts_list):.0f}</small>'
            noise_ls = f' <small style="color:#999">±{max(ls_list)-min(ls_list):.0f}</small>'
            noise_fs = f' <small style="color:#999">±{max(fs_list)-min(fs_list):.1f}</small>'
        else:
            noise_ts = noise_ls = noise_fs = ""

        rows += (
            f'<tr><td>{p["index"]}</td>'
            f'<td><a href="#p{p["index"]}">{_esc(p["name"])}</a></td>'
            f'<td>{_esc(p["city"])}</td>'
            f'<td style="color:{_score_color(ts_avg)};font-weight:bold">{ts_avg:.1f}{noise_ts}</td>'
            f'<td style="color:{_score_color(ls_avg)};font-weight:bold">{ls_avg:.1f}{noise_ls}</td>'
            f'<td style="color:{_score_color(fs_avg)};font-weight:bold;font-size:1.1em">{fs_avg:.1f}{noise_fs}</td></tr>\n'
        )

    if place_count:
        av = [t / place_count for t in grand]
        label = f"ממוצע ({place_count} מקומות × {max_run} ריצות)" if multi else f"ממוצע ({place_count} מקומות)"
        rows += (
            f'<tr class="avgsep"><td colspan="3">{label}</td>'
            f'<td style="color:{_score_color(av[0])}">{av[0]:.1f}</td>'
            f'<td style="color:{_score_color(av[1])}">{av[1]:.1f}</td>'
            f'<td style="color:{_score_color(av[2])};font-size:1.1em">{av[2]:.1f}</td></tr>\n'
        )

    # ── detail cards ─────────────────────────────────────────────
    cards = ""
    for idx in seen_order:
        group = by_place[idx]
        p = group[0]["place"]
        fs_list = [g["final_score"] for g in group]
        fs_avg = sum(fs_list) / len(fs_list)

        inner = ""
        if multi:
            inner += _noise_box(group)
            for j in group:
                run_num = j["place"].get("run", "?")
                inner += f'<div class="runsep">ריצה {run_num}</div>'
                inner += _one_card(j, question, f"ריצה {run_num}")
        else:
            inner = _one_card(group[0], question)

        cards += f"""
<div class="pc" id="p{p['index']}">
  <h2>{p['index']}. {_esc(p['name'])} <span class="ctag">{_esc(p['city'])}</span>
    <span class="fbadge" style="background:{_score_color(fs_avg)}">{fs_avg:.1f}</span></h2>
  <div class="coords">📍 {p['lat']:.5f}, {p['lon']:.5f}</div>
  {inner}
</div>
"""

    noise_note = f" | {max_run} ריצות לניתוח נויז" if multi else ""
    return f"""<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>דוח שפיטה — Map Chat</title>
<style>{_CSS}</style>
</head>
<body>
<div class="hdr">
  <h1>📊 דוח שפיטה — Map Chat</h1>
  <div class="meta">
    <span><b>תאריך:</b> {now}</span>
    <span><b>שאלה:</b> {_esc(question)}</span>
    <span><b>מצב:</b> {_esc(scenario_type)}</span>
    <span><b>כלים:</b> {_esc(tools_str)}{_esc(noise_note)}</span>
  </div>
</div>
<div class="card">
  <h2>📋 סיכום — {place_count} מקומות{' (ממוצע ריצות)' if multi else ''}</h2>
  <table>
    <thead><tr><th>#</th><th>מקום</th><th>עיר</th>
      <th>TransportScore{'±טווח' if multi else ''}<br><small style="font-weight:normal">Gemini /100</small></th>
      <th>LanguageScore{'±טווח' if multi else ''}<br><small style="font-weight:normal">OpenAI /100</small></th>
      <th>FinalScore{'±טווח' if multi else ''}</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
</div>
{cards}
</body>
</html>"""


# ── Models ─────────────────────────────────────────────────────────────────────

class ExamResultItem(BaseModel):
    index: int
    name: str
    city: str
    lat: float
    lon: float
    question: str
    answer: str
    scenario_type: str
    tools_used: List[str] = []
    run: int = 1


class ReportRequest(BaseModel):
    results: List[ExamResultItem]
    question: str = "מה יש כאן?"
    scenario_type: str = "baseline"
    tools_used: List[str] = []


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.get("/exam/places")
def get_exam_places():
    if not EXAM_FILE.exists():
        raise HTTPException(status_code=404, detail="exam/Places.csv not found")
    places = []
    with open(EXAM_FILE, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            places.append({
                "index": int(row["INDEX"]),
                "name": row["PLACE"],
                "city": row["city"],
                "lat": float(row["X"]),
                "lon": float(row["Y"]),
            })
    return places


@router.post("/exam/report")
async def generate_exam_report(req: ReportRequest):
    judgments = []
    for item in req.results:
        gt = _get_ground_truth_text(item.lat, item.lon)
        gemini_eval, openai_eval = await asyncio.gather(
            _call_gemini(item.lat, item.lon, item.answer, gt),
            _call_openai(item.lat, item.lon, item.answer),
        )
        ts = gemini_eval.get("transport_score", 0)
        ls = openai_eval.get("language_score", 0)
        judgments.append({
            "place": item.model_dump(),
            "question": item.question,
            "answer": item.answer,
            "gemini": gemini_eval,
            "openai": openai_eval,
            "final_score": round(0.7 * ts + 0.3 * ls, 1),
        })

    html = _build_html(judgments, req.question, req.scenario_type, req.tools_used)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
    (REPORTS_DIR / filename).write_text(html, encoding="utf-8")
    return {"filename": filename, "html": html}


@router.post("/exam/judge-one")
async def judge_one(item: ExamResultItem):
    """Judge a single exam result — called per-item from frontend for progress tracking."""
    gt = _get_ground_truth_text(item.lat, item.lon)
    gemini_eval, openai_eval = await asyncio.gather(
        _call_gemini(item.lat, item.lon, item.answer, gt),
        _call_openai(item.lat, item.lon, item.answer),
    )
    ts = gemini_eval.get("transport_score", 0)
    ls = openai_eval.get("language_score", 0)
    return {
        "gemini": gemini_eval,
        "openai": openai_eval,
        "final_score": round(0.7 * ts + 0.3 * ls, 1),
    }


class JudgmentItem(BaseModel):
    place: dict
    question: str
    answer: str
    gemini: dict
    openai: dict
    final_score: float


class BuildReportRequest(BaseModel):
    judgments: List[JudgmentItem]
    question: str = "מה יש כאן?"
    scenario_type: str = "baseline"
    tools_used: List[str] = []


@router.post("/exam/build-report")
def build_report(req: BuildReportRequest):
    """Build and save the HTML report from pre-computed judgments."""
    judgments_dicts = [j.model_dump() for j in req.judgments]
    html = _build_html(judgments_dicts, req.question, req.scenario_type, req.tools_used)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
    (REPORTS_DIR / filename).write_text(html, encoding="utf-8")
    return {"filename": filename, "html": html}
