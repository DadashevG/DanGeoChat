import logging
import json
import uuid
from datetime import datetime
from pathlib import Path
from anthropic import Anthropic
from app.config import settings

# --- Setup file logger ---
LOG_DIR = Path(__file__).parent.parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

_file_handler = logging.FileHandler(LOG_DIR / "llm_calls.log", encoding="utf-8")
_file_handler.setFormatter(logging.Formatter("%(message)s"))
logger = logging.getLogger("llm_logger")
logger.setLevel(logging.DEBUG)
logger.addHandler(_file_handler)
logger.propagate = False

def _log(event: str, data: dict):
    entry = {"timestamp": datetime.now().isoformat(), "event": event, **data}
    logger.info(json.dumps(entry, ensure_ascii=False))

# --------------------------

class LLMService:
    def __init__(self):
        self.provider = settings.LLM_PROVIDER
        
        # Check if Anthropic key is configured
        has_anthropic_key = settings.ANTHROPIC_API_KEY and settings.ANTHROPIC_API_KEY.strip()
        
        if self.provider == "anthropic" and has_anthropic_key:
            try:
                self.client = Anthropic(api_key=settings.ANTHROPIC_API_KEY)
                self.model = settings.ANTHROPIC_MODEL
                self.use_mock = False
            except Exception as e:
                print(f"Warning: Could not initialize Anthropic client: {e}")
                self.use_mock = True
        else:
            self.use_mock = True
            self.provider = None
        
    def generate_web_grounded_answer(self, question: str, lat: float, lon: float) -> tuple[str, bool]:
        """Generate answer using Claude's web search tool.

        Returns:
            (answer_text, used_web_search)
        """
        _log("request", {"question": question, "lat": lat, "lon": lon, "mode": "web_search"})

        if self.use_mock:
            answer = "[web search - mock] " + self._mock_response(question, lat, lon)
            _log("response", {"source": "mock", "answer": answer})
            return answer, False

        prompt = f"""You are an expert assistant answering questions about locations in Israel.
Answer in Hebrew.

Question: {question}
Location coordinates: {lat:.4f}, {lon:.4f}

Use web search to find up-to-date and accurate information about this location in Israel.
Provide a helpful, grounded answer with real data."""

        _log("prompt_sent", {"model": self.model, "prompt": prompt, "tool": "web_search"})

        try:
            message = self.client.messages.create(
                model=self.model,
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
                tools=[{
                    "type": "web_search_20250305",
                    "name": "web_search",
                    "max_uses": 3,
                    "user_location": {
                        "type": "approximate",
                        "city": "Tel Aviv",
                        "timezone": "Asia/Jerusalem"
                    }
                }]
            )
            # Extract text blocks from response (may contain tool use blocks too)
            answer_parts = [block.text for block in message.content if hasattr(block, "text")]
            answer = "\n".join(answer_parts).strip()
            usage = message.usage
            _log("response", {
                "source": "claude_web",
                "model": self.model,
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "answer": answer
            })
            return answer, True
        except Exception as e:
            print(f"Error calling Claude with web search: {e}")
            _log("error", {"source": "claude_web", "error": str(e)})
            # If web tool fails (e.g., unsupported SDK/tool config), gracefully fallback
            # to baseline Claude answer so users still get a real model response.
            fallback_answer = self._call_claude(question, lat, lon)
            _log("response", {
                "source": "claude_fallback_no_web",
                "model": self.model,
                "answer": fallback_answer
            })
            return fallback_answer, False

    def generate_baseline_answer(self, question: str, lat: float, lon: float) -> str:
        """Generate baseline answer without spatial grounding."""
        _log("request", {"question": question, "lat": lat, "lon": lon, "mode": "mock" if self.use_mock else "claude"})

        if self.use_mock:
            answer = self._mock_response(question, lat, lon)
            _log("response", {"source": "mock", "answer": answer})
            return answer
        
        try:
            answer = self._call_claude(question, lat, lon)
            return answer
        except Exception as e:
            print(f"Error with LLM service: {e}")
            answer = self._mock_response(question, lat, lon)
            _log("response", {"source": "mock_fallback", "error": str(e), "answer": answer})
            return answer
    
    def _mock_response(self, question: str, lat: float, lon: float) -> str:
        """Generate mock response for testing."""
        return f"""
תשובה בסיסית (baseline) ללא grounding:

שאלה: {question}
מיקום: ({lat:.4f}, {lon:.4f})

זו תשובה כללית שלא מתבססת על נתונים מרחביים אמיתיים. 
בדרך כלל, אזורים עירוניים בישראל יש להם גישה לתחבורה ציבורית,
אך התשובה הזו לא מתחשבת בנתונים ספציפיים של המיקום שנבחר.

זה הוא ה־baseline שעליו נשווה שלבים מתקדמים יותר.
        """.strip()
    
    def _call_claude(self, question: str, lat: float, lon: float) -> str:
        """Call Claude API for answer generation."""
        prompt = f"""You are an expert assistant answering questions about locations in Israel. 
Answer in Hebrew.

Question: {question}
Location coordinates: {lat:.4f}, {lon:.4f}

Provide a helpful answer based on general knowledge about Israeli geography and cities.
This is a baseline response without specific geospatial data."""

        _log("prompt_sent", {"model": self.model, "prompt": prompt})

        try:
            message = self.client.messages.create(
                model=self.model,
                max_tokens=500,
                messages=[{"role": "user", "content": prompt}]
            )
            answer = message.content[0].text
            usage = message.usage
            _log("response", {
                "source": "claude",
                "model": self.model,
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "answer": answer
            })
            return answer
        except Exception as e:
            print(f"Error calling Claude: {e}")
            _log("error", {"source": "claude", "error": str(e)})
            return self._mock_response(question, lat, lon)
    

    def generate_mcp_answer(
        self, question: str, lat: float, lon: float, enabled_tools: list[str],
        use_web_search: bool = False,
    ) -> tuple[str, list[str]]:
        """Generate answer using selected geospatial MCP tools via Claude tool-use loop."""
        from app.services.geo_tools import TOOL_DEFINITIONS, TOOL_FUNCTIONS

        rid = str(uuid.uuid4())[:8]
        _log("request", {
            "request_id": rid,
            "question": question, "lat": lat, "lon": lon,
            "mode": "mcp", "enabled_tools": enabled_tools,
        })

        if self.use_mock:
            answer = "[MCP - mock] " + self._mock_response(question, lat, lon)
            _log("response", {"request_id": rid, "source": "mock", "answer": answer})
            return answer, []

        active_defs = [t for t in TOOL_DEFINITIONS if t["name"] in enabled_tools]
        if use_web_search:
            active_defs = active_defs + [{
                "type": "web_search_20250305",
                "name": "web_search",
                "max_uses": 3,
                "user_location": {
                    "type": "approximate",
                    "city": "Tel Aviv",
                    "timezone": "Asia/Jerusalem",
                },
            }]
        if not active_defs:
            return self.generate_baseline_answer(question, lat, lon), []

        # ── Prompt (forces use of ALL selected tools) ────────────────────────
        REQUIRED_GOOGLE = {"reverse_geocode", "get_area_info", "get_nearby_places", "get_nearby_transit", "get_distance"}
        OPTIONAL_GOOGLE = {"search_places"}
        required = [t for t in enabled_tools if t in REQUIRED_GOOGLE]
        optional = [t for t in enabled_tools if t in OPTIONAL_GOOGLE]
        has_wiki = "get_wikipedia_context" in enabled_tools

        tool_steps = []
        if required:
            tool_steps.append(f"Call ALL of these tools (every single one): {', '.join(required)}.")
        if optional:
            tool_steps.append(
                f"Call {', '.join(optional)} ONLY if you need to look up a specific named place "
                f"(e.g. a market, hospital, or mall by name). Do NOT call it for generic proximity queries."
            )
        if has_wiki:
            tool_steps.append(
                "Then call get_wikipedia_context with city and street from reverse_geocode "
                "and the full nearby_places list."
            )

        tools_instruction = "\n".join(f"{i+1}. {s}" for i, s in enumerate(tool_steps))

        wiki_answer_hint = (
            " Add exactly ONE short contextual sentence in Hebrew based only on what Wikipedia returned. "
            "If nothing relevant was found, skip the sentence entirely."
            if has_wiki else ""
        )

        prompt = (
            f"You are a geospatial assistant.\n"
            f"You MUST call every tool listed below before writing your answer — do not skip any.\n\n"
            f"{tools_instruction}\n\n"
            f"After calling all tools, answer in Hebrew as a single natural paragraph — "
            f"no bullet points, no lists, no emojis, no headers.\n"
            f"Focus on: the street name, nearby major roads or intersections, and important public places "
            f"(parks, squares, cultural sites, transit). "
            f"Describe the spatial context of the area (e.g. near a main road, between two neighborhoods, close to a park). "
            f"Do not list small or generic businesses unless they are clearly significant landmarks. "
            f"Weave everything into a fluent, readable description.{wiki_answer_hint}\n\n"
            f"Question: {question}\n"
            f"Coordinates: {lat:.4f}, {lon:.4f}"
        )

        messages = [{"role": "user", "content": prompt}]
        tools_used: list[str] = []
        tool_results_log: list[dict] = []
        first_call = True

        try:
            for _ in range(7):
                kwargs = dict(
                    model=self.model,
                    max_tokens=800,
                    tools=active_defs,
                    messages=messages,
                )
                # Force at least one tool call on the first round
                if first_call:
                    kwargs["tool_choice"] = {"type": "any"}
                    first_call = False

                response = self.client.messages.create(**kwargs)

                if response.stop_reason == "end_turn":
                    answer = "\n".join(
                        b.text for b in response.content if hasattr(b, "text")
                    ).strip()
                    _log("response", {
                        "request_id": rid,
                        "source": "claude_mcp",
                        "model": self.model,
                        "input_tokens": response.usage.input_tokens,
                        "output_tokens": response.usage.output_tokens,
                        "tools_used": tools_used,
                        "tool_results": tool_results_log,
                        "answer": answer,
                    })
                    return answer, tools_used

                if response.stop_reason == "tool_use":
                    messages.append({"role": "assistant", "content": response.content})
                    tool_results = []

                    for block in response.content:
                        if block.type != "tool_use":
                            continue

                        fn = TOOL_FUNCTIONS.get(block.name)
                        if fn:
                            result = fn(block.input)
                        else:
                            result = {"error": f"unknown tool: {block.name}"}

                        is_error = isinstance(result, dict) and "error" in result
                        if is_error:
                            _log("tool_error", {"request_id": rid, "tool": block.name, "error": result})
                        else:
                            tools_used.append(block.name)
                            tool_results_log.append({"tool": block.name, "input": block.input, "result": result})
                            _log("tool_call", {
                                "request_id": rid,
                                "tool": block.name,
                                "input": block.input,
                                "result": result,
                            })

                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps(result, ensure_ascii=False),
                            **({"is_error": True} if is_error else {}),
                        })

                    messages.append({"role": "user", "content": tool_results})
                else:
                    break

            # Extract any text from the last response as fallback
            answer = "\n".join(
                b.text for b in response.content if hasattr(b, "text")
            ).strip() or self._mock_response(question, lat, lon)
            return answer, tools_used

        except Exception as e:
            print(f"Error in MCP answer: {e}")
            _log("error", {"request_id": rid, "source": "claude_mcp", "error": str(e)})
            return self.generate_baseline_answer(question, lat, lon), tools_used


llm_service = LLMService()
