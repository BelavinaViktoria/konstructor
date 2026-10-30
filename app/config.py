"""Настройки приложения. Секреты берутся только из окружения или файла .env."""
import os
import secrets
import warnings
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _secret_key(env: str) -> str:
    key = os.environ.get("SECRET_KEY", "")
    if key:
        return key
    if env == "production":
        raise RuntimeError("Задайте SECRET_KEY: без него приложение на сервере не запускается.")
    warnings.warn("SECRET_KEY не задан: создан временный ключ, сессии сбросятся при перезапуске.", stacklevel=2)
    return secrets.token_hex(32)


def load_config() -> dict:
    env = os.environ.get("APP_ENV", "development")
    return {
        "APP_ENV": env,
        "SECRET_KEY": _secret_key(env),
        "SQLALCHEMY_DATABASE_URI": os.environ.get("DATABASE_URL", f"sqlite:///{BASE_DIR / 'instance' / 'app.db'}"),
        "MAX_CONTENT_LENGTH": 10 * 1024 * 1024,
        "SESSION_COOKIE_HTTPONLY": True,
        "SESSION_COOKIE_SAMESITE": "Lax",
        "SESSION_COOKIE_SECURE": env == "production",
        "COMPANY_NAME": os.environ.get("COMPANY_NAME", "Ваша компания"),
        "BASE_URL": os.environ.get("BASE_URL", "http://localhost:5000"),
        "PASSWORD_HASH_METHOD": "pbkdf2:sha256:600000",
        "SMTP_HOST": os.environ.get("SMTP_HOST", ""),
        "SMTP_PORT": int(os.environ.get("SMTP_PORT", "587")),
        "SMTP_USER": os.environ.get("SMTP_USER", ""),
        "SMTP_PASSWORD": os.environ.get("SMTP_PASSWORD", ""),
        "SMTP_FROM": os.environ.get("SMTP_FROM", ""),
        "POTOK_API_KEY": os.environ.get("POTOK_API_KEY", ""),
        "FORMS_API_KEY": os.environ.get("FORMS_API_KEY", ""),
        "ANTHROPIC_API_KEY": os.environ.get("ANTHROPIC_API_KEY", ""),
        "ANTHROPIC_MODEL": os.environ.get("ANTHROPIC_MODEL", "claude-opus-5-5"),
    }
