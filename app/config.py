from typing import Optional, Annotated
from pydantic import field_validator
from pydantic_settings import BaseSettings


def _coerce_int(default: int):
    """Returns a validator that converts empty-string env vars to the field default."""
    def _validate(v):
        if v == "" or v is None:
            return default
        return v
    return _validate


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

    @field_validator("SMTP_PORT", mode="before")
    @classmethod
    def coerce_smtp_port(cls, v):
        return _coerce_int(587)(v)

    @field_validator("ACCESS_TOKEN_EXPIRE_MINUTES", mode="before")
    @classmethod
    def coerce_token_expiry(cls, v):
        return _coerce_int(480)(v)

    @field_validator("TEAMS_WEBHOOK_URL", mode="before")
    @classmethod
    def empty_webhook_to_none(cls, v):
        return None if v == "" else v

    class Config:
        env_file = ".env"


settings = Settings()
