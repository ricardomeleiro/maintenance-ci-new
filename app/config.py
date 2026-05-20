from typing import Optional
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    POSTGRES_DB: str = "manutencao"
    POSTGRES_USER: str = "manutencao_user"
    POSTGRES_PASSWORD: str = ""
    DATABASE_URL: str = ""

    SECRET_KEY: str = "change_me"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 480

    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    EMAIL_FROM: str = ""
    EMAIL_FROM_NAME: str = "Sistema de Manutenção"

    TEAMS_WEBHOOK_URL: Optional[str] = None

    APP_NAME: str = "Sistema de Manutenção Predial"
    BASE_URL: str = "http://localhost"

    ADMIN_EMAIL: str = "admin@example.com"
    ADMIN_PASSWORD: str = "Admin@123456"
    ADMIN_NAME: str = "Administrador"

    class Config:
        env_file = ".env"


settings = Settings()
