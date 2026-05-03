from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app import models, schemas
from app.database import get_db

router = APIRouter(prefix="/api/v1", tags=["test-scenarios"])

@router.post("/test-scenarios", response_model=schemas.TestScenarioResponse)
def create_test_scenario(
    scenario: schemas.TestScenarioCreate,
    db: Session = Depends(get_db)
):
    """Create a new test scenario."""
    db_scenario = models.TestScenario(**scenario.model_dump())
    db.add(db_scenario)
    db.commit()
    db.refresh(db_scenario)
    return db_scenario

@router.get("/test-scenarios", response_model=list[schemas.TestScenarioResponse])
def get_test_scenarios(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    """Get all test scenarios."""
    scenarios = db.query(models.TestScenario).offset(skip).limit(limit).all()
    return scenarios

@router.get("/test-scenarios/{scenario_id}", response_model=schemas.TestScenarioResponse)
def get_test_scenario(scenario_id: int, db: Session = Depends(get_db)):
    """Get a specific test scenario."""
    scenario = db.query(models.TestScenario).filter(
        models.TestScenario.id == scenario_id
    ).first()
    if not scenario:
        return {"error": "Scenario not found"}
    return scenario
