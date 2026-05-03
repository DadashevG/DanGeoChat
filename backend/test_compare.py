"""
test_compare.py — השווה תשובות baseline vs web_grounded לאותה קואורדינאטה.

הרצה:
    cd backend
    python test_compare.py
"""

import json
import sys
import os

# הוסף את תיקיית backend ל-path כדי לייבא את ה-service ישירות
sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv()

from app.services.llm_service import llm_service

# ─── קואורדינאטות לבדיקה ───────────────────────────────────────────────
TESTS = [
    {"name": "תל אביב מרכז",   "lat": 32.0853,  "lon": 34.7818},
    {"name": "פתח תקווה",       "lat": 32.0878,  "lon": 34.8859},
    {"name": "אזור מדברי",      "lat": 31.5,     "lon": 35.0},
]
QUESTION = "מה יש באזור הזה ואיך מגיעים אליו בתחבורה ציבורית?"

# ─── הרצה ──────────────────────────────────────────────────────────────
SEP = "─" * 72

def run():
    if llm_service.use_mock:
        print("⚠️  השירות פועל במצב MOCK — בדוק שה-ANTHROPIC_API_KEY מוגדר ב-.env")
        return

    for t in TESTS:
        lat, lon, name = t["lat"], t["lon"], t["name"]
        print(f"\n{'═'*72}")
        print(f"  📍 {name}  ({lat}, {lon})")
        print('═'*72)

        # --- Baseline (ללא אינטרנט) ---
        print("\n🔵 BASELINE (ללא חיפוש אינטרנט):")
        print(SEP)
        baseline_answer = llm_service.generate_baseline_answer(QUESTION, lat, lon)
        print(baseline_answer)

        # --- Web grounded (עם אינטרנט) ---
        print(f"\n🟢 WEB GROUNDED (עם חיפוש אינטרנט):")
        print(SEP)
        web_answer, used_web = llm_service.generate_web_grounded_answer(QUESTION, lat, lon)
        status = "✅ חיפוש אינטרנט הופעל" if used_web else "⚠️  חיפוש נכשל, חזרה ל-baseline"
        print(f"[{status}]")
        print(web_answer)

        print()

if __name__ == "__main__":
    run()
