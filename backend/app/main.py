import traceback
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from app.config import settings
from app.database import engine, Base
from app.routers import chat, test_scenarios, evaluation, exam, gnn

# Create database tables
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Map Chat - Stage A",
    description="Baseline LLM without spatial grounding",
    version="0.1.0"
)

# CORS middleware — allow all origins in dev mode
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(chat.router)
app.include_router(test_scenarios.router)
app.include_router(evaluation.router)
app.include_router(exam.router)
app.include_router(gnn.router)

@app.exception_handler(Exception)
async def debug_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"error": str(exc), "traceback": traceback.format_exc()},
    )

@app.get("/health")
def health_check():
    return {"status": "ok", "stage": "a_baseline"}

@app.get("/")
def root():
    return {
        "message": "Map Chat - Stage A (Baseline)",
        "endpoints": {
            "ask": "POST /api/v1/ask",
            "queries": "GET /api/v1/queries",
            "test-scenarios": "GET /api/v1/test-scenarios",
            "compare": "POST /api/v1/compare-scenarios/{id}"
        }
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
