"""Application configuration loader."""

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

# Load .env file
load_dotenv()

# Project root
PROJECT_ROOT = Path(__file__).parent.parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
DATA_DIR = PROJECT_ROOT / "data"
LOGS_DIR = PROJECT_ROOT / "logs"


def _load_yaml(filename: str) -> dict[str, Any]:
    """Load and parse a YAML config file."""
    filepath = CONFIG_DIR / filename
    if not filepath.exists():
        return {}
    with open(filepath, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def get_sources_config() -> dict[str, Any]:
    """Get information sources configuration."""
    cfg = _load_yaml("sources.yaml")
    return cfg.get("sources", {})


def get_keywords_config() -> dict[str, Any]:
    """Get keyword filtering configuration."""
    return _load_yaml("keywords.yaml")


def get_notifications_config() -> dict[str, Any]:
    """Get notification configuration."""
    return _load_yaml("notifications.yaml")


def get_database_path() -> str:
    """Get the SQLite database path."""
    path = os.getenv("DATABASE_PATH", str(DATA_DIR / "news.db"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


def get_openai_api_key() -> str:
    return os.getenv("OPENAI_API_KEY", "")


def get_anthropic_api_key() -> str:
    return os.getenv("ANTHROPIC_API_KEY", "")


def get_deepseek_api_key() -> str:
    return os.getenv("DEEPSEEK_API_KEY", "")


def get_deepseek_base_url() -> str:
    return os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")


def get_telegram_config() -> dict[str, str]:
    return {
        "bot_token": os.getenv("TELEGRAM_BOT_TOKEN", ""),
        "chat_id": os.getenv("TELEGRAM_CHAT_ID", ""),
    }


def get_email_config() -> dict[str, str]:
    return {
        "smtp_host": os.getenv("SMTP_HOST", "smtp.gmail.com"),
        "smtp_port": int(os.getenv("SMTP_PORT", "587")),
        "user": os.getenv("EMAIL_USER", ""),
        "password": os.getenv("EMAIL_PASSWORD", ""),
        "from_address": os.getenv("EMAIL_FROM", ""),
        "to_address": os.getenv("EMAIL_TO", ""),
    }


def get_log_level() -> str:
    return os.getenv("LOG_LEVEL", "INFO")


def is_configured() -> bool:
    """Check if the application has minimal configuration."""
    return bool(get_openai_api_key() or get_anthropic_api_key() or get_deepseek_api_key())
