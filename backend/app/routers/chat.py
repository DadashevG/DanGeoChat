import json
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app import models, schemas
from app.config import settings
from app.database import get_db
from app.services.llm_service import llm_service

router = APIRouter(prefix="/api/v1", tags=["chat"])

@router.post("/ask", response_model=schemas.QueryResponse)
def ask_question(
    query: schemas.QueryCreate,
    db: Session = Depends(get_db)
):
    tools_used_list: list[str] = []

    if query.scenario_type == "mcp":
        answer, tools_used_list = llm_service.generate_mcp_answer(
            question=query.question,
            lat=query.lat,
            lon=query.lon,
            enabled_tools=query.enabled_tools,
            use_web_search=query.use_web_search,
        )
        suffix = "+mcp+web_search" if query.use_web_search else "+mcp"
        model_used = settings.ANTHROPIC_MODEL + suffix

    elif query.scenario_type == "web_grounded":
        answer, used_web_search = llm_service.generate_web_grounded_answer(
            question=query.question,
            lat=query.lat,
            lon=query.lon,
        )
        if llm_service.use_mock:
            model_used = "web-mock"
        elif used_web_search:
            model_used = settings.ANTHROPIC_MODEL + "+web_search"
        else:
            model_used = settings.ANTHROPIC_MODEL + "+fallback_no_web"

    else:
        answer = llm_service.generate_baseline_answer(
            question=query.question,
            lat=query.lat,
            lon=query.lon,
        )
        model_used = settings.ANTHROPIC_MODEL if not llm_service.use_mock else "baseline-mock"

    # Save to DB (tools_used not persisted — returned only in response)
    db_query = models.Query(
        lat=query.lat,
        lon=query.lon,
        question=query.question,
        scenario_type=query.scenario_type,
        answer=answer,
        model_used=model_used,
    )
    db.add(db_query)
    db.commit()
    db.refresh(db_query)

    return {
        "id": db_query.id,
        "timestamp": db_query.timestamp,
        "lat": db_query.lat,
        "lon": db_query.lon,
        "question": db_query.question,
        "scenario_type": db_query.scenario_type,
        "answer": db_query.answer,
        "model_used": db_query.model_used,
        "tokens_used": db_query.tokens_used,
        "tools_used": json.dumps(tools_used_list, ensure_ascii=False) if tools_used_list else None,
    }

@router.get("/queries", response_model=list[schemas.QueryResponse])
def get_queries(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    """Get all queries."""
    queries = db.query(models.Query).offset(skip).limit(limit).all()
    return queries

@router.get("/queries/{query_id}", response_model=schemas.QueryResponse)
def get_query(query_id: int, db: Session = Depends(get_db)):
    """Get a specific query."""
    query = db.query(models.Query).filter(models.Query.id == query_id).first()
    if not query:
        return {"error": "Query not found"}
    return query
