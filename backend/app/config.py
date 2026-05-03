import os
from pathlib import Path
from pydantic_settings import BaseSettings

BASE_DIR = Path(__file__).resolve().parents[1]

class Settings(BaseSettings):
    # Database - SQLite for MVP
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        "sqlite:///./map_chat.db"
    )
    
    # LLM Provider
    LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "anthropic")
    
    # Anthropic (Claude)
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
    ANTHROPIC_MODEL: str = os.getenv("ANTHROPIC_MODEL", "claude-3-haiku-20240307")
    
    # Google APIs
    GOOGLE_API_KEY: str = os.getenv("GOOGLE_API_KEY", "")

    # App
    DEBUG: bool = os.getenv("DEBUG", "True").lower() == "true"
    CORS_ORIGINS: list[str] = ["http://localhost:3000", "http://localhost:5173", "http://localhost:8001"]
    
    class Config:
        env_file = BASE_DIR / ".env"

settings = Settings()
