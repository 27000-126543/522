from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    APP_NAME: str = "智慧风电运维与故障预警调度系统"
    DEBUG: bool = True

    DATABASE_URL: str = "sqlite:///./wind_power.db"

    SECRET_KEY: str = "windpower-secret-key-change-in-production-2024"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7

    HEALTH_SCORE_WEIGHT_VIBRATION: float = 0.30
    HEALTH_SCORE_WEIGHT_TEMPERATURE: float = 0.25
    HEALTH_SCORE_WEIGHT_POWER: float = 0.20
    HEALTH_SCORE_WEIGHT_NOISE: float = 0.15
    HEALTH_SCORE_WEIGHT_OTHER: float = 0.10

    WARNING_THRESHOLD_YELLOW: float = 70.0
    WARNING_THRESHOLD_ORANGE: float = 50.0
    WARNING_THRESHOLD_RED: float = 30.0

    ORDER_AUTO_ASSIGN_TIMEOUT_MINUTES: int = 30
    ORDER_ESCALATE_TIMEOUT_MINUTES: int = 60 * 2

    PERSISTENT_FAULT_DAYS: int = 30

    class Config:
        env_file = ".env"


settings = Settings()
