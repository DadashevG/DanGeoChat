from pydantic import BaseModel
from datetime import datetime
from typing import Optional

# === Query Schemas ===
class QueryCreate(BaseModel):
    lat: float
    lon: float
    question: str
    scenario_type: str = "baseline"
    enabled_tools: list[str] = []
    use_web_search: bool = False

class QueryResponse(BaseModel):
    id: int
    timestamp: datetime
    lat: float
    lon: float
    question: str
    scenario_type: str
    answer: Optional[str]
    model_used: Optional[str]
    tokens_used: Optional[int]
    tools_used: Optional[str]
    
    class Config:
        from_attributes = True

# === Test Scenario Schemas ===
class TestScenarioCreate(BaseModel):
    name: str
    lat: float
    lon: float
    question: str
    expected_answer: Optional[str] = None
    category: Optional[str] = None
    description: Optional[str] = None

class TestScenarioResponse(BaseModel):
    id: int
    name: str
    lat: float
    lon: float
    question: str
    expected_answer: Optional[str]
    category: Optional[str]
    description: Optional[str]
    created_at: datetime
    
    class Config:
        from_attributes = True

# === Evaluation Schemas ===
class EvaluationResultCreate(BaseModel):
    query_id: int
    correctness: Optional[float] = None
    groundedness: Optional[float] = None
    relevance: Optional[float] = None
    spatial_awareness: Optional[float] = None
    hallucination_score: Optional[float] = None
    notes: Optional[str] = None
    evaluator_notes: Optional[str] = None

class EvaluationResultResponse(BaseModel):
    id: int
    query_id: int
    correctness: Optional[float]
    groundedness: Optional[float]
    relevance: Optional[float]
    spatial_awareness: Optional[float]
    hallucination_score: Optional[float]
    notes: Optional[str]
    evaluator_notes: Optional[str]
    evaluated_at: datetime
    
    class Config:
        from_attributes = True
