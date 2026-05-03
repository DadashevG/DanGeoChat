from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app import models, schemas
from app.database import get_db

router = APIRouter(prefix="/api/v1", tags=["evaluation"])

@router.post("/evaluate", response_model=schemas.EvaluationResultResponse)
def create_evaluation(
    evaluation: schemas.EvaluationResultCreate,
    db: Session = Depends(get_db)
):
    """Create evaluation for a query."""
    db_eval = models.EvaluationResult(**evaluation.model_dump())
    db.add(db_eval)
    db.commit()
    db.refresh(db_eval)
    return db_eval

@router.get("/evaluate/{query_id}", response_model=list[schemas.EvaluationResultResponse])
def get_evaluations(query_id: int, db: Session = Depends(get_db)):
    """Get evaluations for a query."""
    evals = db.query(models.EvaluationResult).filter(
        models.EvaluationResult.query_id == query_id
    ).all()
    return evals

@router.post("/compare-scenarios/{scenario_id}")
def compare_scenario(scenario_id: int, db: Session = Depends(get_db)):
    """
    Run baseline answer on a test scenario and return result.
    שלב א׳: Just run baseline, no comparison yet.
    """
    from app.services.llm_service import llm_service
    
    scenario = db.query(models.TestScenario).filter(
        models.TestScenario.id == scenario_id
    ).first()
    
    if not scenario:
        return {"error": "Scenario not found"}
    
    baseline_answer = llm_service.generate_baseline_answer(
        question=scenario.question,
        lat=scenario.lat,
        lon=scenario.lon
    )
    
    return {
        "scenario": {
            "id": scenario.id,
            "name": scenario.name,
            "lat": scenario.lat,
            "lon": scenario.lon,
            "question": scenario.question
        },
        "baseline_answer": baseline_answer,
        "expected_answer": scenario.expected_answer,
        "phase": "stage_a_baseline_only"
    }
