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

    if query.scenario_type == "gnn":
        from app.services.gnn_service import gnn_service, is_in_trained_area
        if not is_in_trained_area(query.lat, query.lon):
            answer = "המיקום שנבחר מחוץ לאזור האימון של מודל ה-GNN (תל אביב-יפו)."
            model_used = "MetroGNN"
        else:
            gnn_result = gnn_service.infer(query.lat, query.lon)
            tools_used_list = ["gnn"]

            # Optional Wikipedia enrichment (street name from OSM graph, no Google needed)
            wiki_context = ""
            if "get_wikipedia_context" in query.enabled_tools:
                from app.services.geo_tools import get_wikipedia_context
                try:
                    street = gnn_result.get("street_name")
                    wiki_result = get_wikipedia_context(
                        city="תל אביב-יפו", street=street, nearby_places=[]
                    )
                    parts = []
                    if wiki_result.get("city_context", {}).get("summary"):
                        parts.append(wiki_result["city_context"]["summary"])
                    if wiki_result.get("street_context", {}).get("summary"):
                        parts.append(wiki_result["street_context"]["summary"])
                    if parts:
                        wiki_context = "\n".join(parts)
                        tools_used_list.append("get_wikipedia_context")
                        print(f"[GNN] Wikipedia OK — {len(wiki_context)} chars, street={street}")
                    else:
                        print(f"[GNN] Wikipedia returned nothing for street={street}, result={wiki_result}")
                except Exception as _e:
                    print(f"[GNN] Wikipedia failed: {_e}")

            answer = llm_service.generate_gnn_answer(
                question=query.question,
                lat=query.lat,
                lon=query.lon,
                gnn_result=gnn_result,
                wiki_context=wiki_context,
            )
            model_used = "MetroGNN+" + settings.ANTHROPIC_MODEL

    elif query.scenario_type == "gnn_mcp":
        from app.services.gnn_service import gnn_service, is_in_trained_area
        if not is_in_trained_area(query.lat, query.lon):
            answer = "המיקום שנבחר מחוץ לאזור האימון של מודל ה-GNN (תל אביב-יפו)."
            model_used = "MetroGNN"
        else:
            from app.services.geo_tools import (
                reverse_geocode, get_nearby_places, get_nearby_transit, get_wikipedia_context
            )
            gnn_result = gnn_service.infer(query.lat, query.lon)
            tools_used_list = ["gnn"]

            geo, places, transit = {}, [], []
            try:
                geo = reverse_geocode(query.lat, query.lon) or {}
                tools_used_list.append("reverse_geocode")
            except Exception: pass
            try:
                places = get_nearby_places(lat=query.lat, lon=query.lon, radius_meters=400) or []
                tools_used_list.append("get_nearby_places")
            except Exception: pass
            try:
                transit = get_nearby_transit(lat=query.lat, lon=query.lon, radius_meters=600) or []
                tools_used_list.append("get_nearby_transit")
            except Exception: pass

            wiki_context = ""
            if "get_wikipedia_context" in query.enabled_tools:
                try:
                    city = geo.get("city", "תל אביב-יפו")
                    street = geo.get("street") or gnn_result.get("street_name")
                    wiki_result = get_wikipedia_context(city=city, street=street, nearby_places=places)
                    parts = []
                    if wiki_result.get("city_context", {}).get("summary"):
                        parts.append(wiki_result["city_context"]["summary"])
                    if wiki_result.get("street_context", {}).get("summary"):
                        parts.append(wiki_result["street_context"]["summary"])
                    if parts:
                        wiki_context = "\n".join(parts)
                        tools_used_list.append("get_wikipedia_context")
                except Exception: pass

            answer = llm_service.generate_gnn_mcp_answer(
                question=query.question,
                lat=query.lat,
                lon=query.lon,
                gnn_result=gnn_result,
                geo=geo,
                places=places,
                transit=transit,
                wiki_context=wiki_context,
            )
            model_used = "MetroGNN+mcp+" + settings.ANTHROPIC_MODEL

    elif query.scenario_type == "mcp":
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
