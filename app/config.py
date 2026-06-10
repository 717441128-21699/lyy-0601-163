from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    DATABASE_URL: str = "sqlite:///./tournament.db"
    API_V1_PREFIX: str = "/api/v1"
    PROJECT_NAME: str = "桌游赛事计分系统"
    DEBUG: bool = True
    SECRET_KEY: str = "tournament-secret-key-2024"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24

    class Config:
        env_file = ".env"


settings = Settings()
