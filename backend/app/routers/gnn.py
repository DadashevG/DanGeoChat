from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.services.gnn_service import gnn_service, is_in_trained_area

router = APIRouter(prefix="/api/v1/gnn", tags=["gnn"])


class GNNRequest(BaseModel):
    lat: float
    lon: float


@router.get("/status")
def gnn_status():
    return {
        "available": gnn_service.available,
        "ready":     gnn_service._ready,
        "error":     gnn_service._error,
    }


@router.get("/area-check")
def area_check(lat: float, lon: float):
    return {"in_trained_area": is_in_trained_area(lat, lon)}


@router.post("/infer")
def infer(req: GNNRequest):
    if not is_in_trained_area(req.lat, req.lon):
        raise HTTPException(
            status_code=400,
            detail=f"Location ({req.lat:.4f}, {req.lon:.4f}) is outside the trained area (Tel Aviv-Yafo)."
        )
    if not gnn_service.available:
        raise HTTPException(status_code=503, detail="GNN model files not found.")
    try:
        result = gnn_service.infer(req.lat, req.lon)
        result["in_trained_area"] = True
        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
