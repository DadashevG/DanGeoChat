# Evaluator Prompt — LLM-as-Judge

משמש לקריאת Claude (מודל חזק יותר) להעריך תשובות של Map Chat.

---

## System Prompt

```
You are an expert evaluator for a geospatial question-answering system operating in Israel.
Your task is to score a Map Chat answer on multiple dimensions and return a single valid JSON object.

Rules:
- Be strict. Do not award high scores unless the answer is genuinely accurate and grounded.
- Every factual claim in the answer must be traceable to the provided tools output or ground truth.
- If tools were available but not used, groundedness must be 0.0.
- If the answer contradicts the tool output, answer_consistent_with_data must be false.
- Identify every failure mode that applies — an empty array is acceptable only if the answer is correct.
```

---

## User Prompt Template

```
Evaluate the following Map Chat response.

=== QUESTION ===
{question}

=== COORDINATES ===
lat: {lat}, lon: {lon}

=== SCENARIO TYPE ===
{scenario_type}   (baseline | web_grounded | mcp)

=== GROUND TRUTH ===
Address: {verified_address}
Verified nearby places: {verified_places}

=== ANSWER TO EVALUATE ===
{answer}

=== TOOLS USED (reported by system) ===
{tools_used}

---

Return ONLY a valid JSON object with this exact structure.
Do not include any text before or after the JSON.

{
  "scores": {
    "location_accuracy": {
      "score": <0.0-1.0>,
      "city_correct": <bool>,
      "suburb_correct": <bool|null>,
      "road_correct": <bool|null>,
      "error_distance_km": <number|null>,
      "reasoning": "<string>"
    },
    "place_names_accuracy": {
      "score": <0.0-1.0>,
      "mentioned_places": ["<string>", ...],
      "verified_count": <int>,
      "hallucinated_count": <int>,
      "unverifiable_count": <int>,
      "reasoning": "<string>"
    },
    "relevance": {
      "score": <0.0-1.0>,
      "reasoning": "<string>"
    },
    "groundedness": {
      "score": <0.0-1.0>,
      "tool_was_used": <bool>,
      "data_cited": <bool>,
      "data_used_correctly": <bool>,
      "answer_consistent_with_data": <bool>,
      "reasoning": "<string>"
    },
    "hallucination": {
      "score": <0.0-1.0>,
      "detected": <bool>,
      "examples": ["<string>", ...],
      "severity": "<none|minor|major>",
      "reasoning": "<string>"
    },
    "conciseness": {
      "score": <0.0-1.0>,
      "word_count": <int>,
      "reasoning": "<string>"
    },
    "spatial_awareness": {
      "score": <0.0-1.0>,
      "reasoning": "<string>"
    },
    "uncertainty_handling": {
      "score": <0.0-1.0>,
      "expressed_uncertainty_when_needed": <bool>,
      "overclaimed_precision": <bool>,
      "reasoning": "<string>"
    },
    "granularity": {
      "score": <0.0-1.0>,
      "expected_level": "<poi|street|neighborhood|city|region|unknown>",
      "predicted_level": "<poi|street|neighborhood|city|region|unknown>",
      "match": <bool>,
      "reasoning": "<string>"
    }
  },
  "failure_modes": [
    "<zero or more of: wrong_city, wrong_neighborhood, wrong_street, hallucinated_poi,
      missing_poi, tool_not_called, tool_called_wrong_params, answer_contradicts_tool_output,
      ignored_uncertainty, overclaimed_precision, language_error, off_topic>"
  ],
  "total_score": <0.0-1.0>,
  "flags": {
    "needs_human_review": <bool>,
    "is_edge_case": <bool>,
    "notes": "<string>"
  }
}
```

---

## Scoring Guide

### location_accuracy
| Score | Meaning |
|-------|---------|
| 1.0 | Exact street + suburb + city match |
| 0.75 | Correct suburb + city, wrong/missing street |
| 0.5 | Correct city only |
| 0.25 | Wrong city but correct region |
| 0.0 | Wrong city (geographically distant) |

Set `error_distance_km` to the approximate geographic error when city is wrong.

### place_names_accuracy
```
score = verified_count / (verified_count + hallucinated_count)
```
Use `unverifiable_count` for places not in ground truth but plausible (OSM may be incomplete) — these do not count against score.

### groundedness
| Score | Condition |
|-------|-----------|
| 1.0 | Tool used + correct params + data cited + answer consistent |
| 0.75 | Tool used + data cited, but minor inconsistency |
| 0.5 | Tool used but answer vague or partially consistent |
| 0.25 | Tool used but answer ignores or contradicts results |
| 0.0 | No tool used (baseline) or tool failed and model guessed |

`data_used_correctly` = tool was called with sensible coordinates and radius.
`answer_consistent_with_data` = answer does not contradict tool output.

### hallucination
| Score | Severity |
|-------|---------|
| 1.0 | No hallucination (`severity: none`) |
| 0.6 | Minor hallucination (`severity: minor`) — one small wrong detail |
| 0.0 | Major hallucination (`severity: major`) — fabricated locations or facts |

### uncertainty_handling
- `expressed_uncertainty_when_needed`: true if the model said "I don't know" or hedged when data was partial/missing.
- `overclaimed_precision`: true if the model stated specific distances or facts that tools did not return.
- Score 1.0 when model correctly expressed uncertainty; 0.0 when it confidently fabricated.

### granularity
- `expected_level`: inferred from the question ("מה יש כאן?" → poi, "באיזה שכונה?" → neighborhood).
- `predicted_level`: what the answer actually delivers.
- Score 1.0 when `predicted_level >= expected_level`; penalize proportionally when the answer is too coarse.

---

## חישוב total_score

```python
WEIGHTS = {
    "location_accuracy":    0.25,
    "place_names_accuracy": 0.20,
    "groundedness":         0.20,
    "hallucination":        0.10,
    "relevance":            0.10,
    "spatial_awareness":    0.05,
    "uncertainty_handling": 0.05,
    "granularity":          0.05,
}
# conciseness excluded from weighted total — tracked separately

total_score = sum(scores[dim]["score"] * w for dim, w in WEIGHTS.items())
```
