"""Loads and validates all environment variables from .env."""

import os
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from dotenv import load_dotenv
from loguru import logger

load_dotenv()

REQUIRED_VARS = [
    "AZURE_AI_API_KEY",
    "AZURE_AI_ENDPOINT",
    "AZURE_AI_DEPLOYMENT",
    "APIFY_API_KEY",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
]

OPTIONAL_VARS = [
    "GOOGLE_SHEETS_ID",
    "GOOGLE_CREDENTIALS_PATH",
    "DEFAULT_JOB_KEYWORD",
    "DEFAULT_LOCATION",
]


def validate() -> None:
    """Raise EnvironmentError if any required env var is missing."""
    missing = [v for v in REQUIRED_VARS if not os.getenv(v)]
    if missing:
        raise EnvironmentError(
            f"Missing required environment variables: {', '.join(missing)}\n"
            "Copy .env.example to .env and fill in all values."
        )
    logger.info("[CONFIG] All required environment variables are present.")


AZURE_AI_API_KEY: str = os.getenv("AZURE_AI_API_KEY", "")
AZURE_AI_ENDPOINT: str = os.getenv("AZURE_AI_ENDPOINT", "")
AZURE_AI_DEPLOYMENT: str = os.getenv("AZURE_AI_DEPLOYMENT", "kimi-k2.6")

def _parse_api_version(endpoint: str, fallback: str) -> str:
    """Extract api-version from the endpoint URL query string if present."""
    qs = parse_qs(urlparse(endpoint).query)
    versions = qs.get("api-version", [])
    return versions[0] if versions else fallback

AZURE_AI_API_VERSION: str = _parse_api_version(
    AZURE_AI_ENDPOINT,
    os.getenv("AZURE_AI_API_VERSION", "2024-05-01-preview"),
)
GPT5_AZURE_OPENAI_ENDPOINT: str = os.getenv("GPT5_AZURE_OPENAI_ENDPOINT", "").strip()
GPT5_AZURE_OPENAI_API_KEY: str = os.getenv("GPT5_AZURE_OPENAI_API_KEY", "").strip()
GPT5_AZURE_OPENAI_DEPLOYMENT: str = os.getenv("GPT5_AZURE_AI_DEPLOYMENT", "gpt-5.4").strip()

APIFY_API_KEY: str = os.getenv("APIFY_API_KEY", "")
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")
GOOGLE_SHEETS_ID: str = os.getenv("GOOGLE_SHEETS_ID", "")
GOOGLE_CREDENTIALS_PATH: str = os.getenv("GOOGLE_CREDENTIALS_PATH", "credentials.json")
LINKEDIN_EMAIL: str = os.getenv("LINKEDIN_EMAIL", "")
LINKEDIN_PASSWORD: str = os.getenv("LINKEDIN_PASSWORD", "")
SSL_NO_VERIFY: bool = os.getenv("SSL_NO_VERIFY", "").lower() in ("true", "1", "yes")
DEFAULT_JOB_KEYWORD: str = os.getenv("DEFAULT_JOB_KEYWORD", "data analyst")
DEFAULT_LOCATION: str = os.getenv("DEFAULT_LOCATION", "remote")

BASE_DIR = Path(__file__).parent
APPLICATIONS_DIR = BASE_DIR / "applications"
APPLICATIONS_DIR.mkdir(exist_ok=True)

PROFILE_PATH = BASE_DIR / "profile.md"
PREFERENCES_PATH = BASE_DIR / "preferences.md"


if __name__ == "__main__":
    validate()
    logger.info(f"[CONFIG] Keyword={DEFAULT_JOB_KEYWORD!r}, Location={DEFAULT_LOCATION!r}")
    logger.info(f"[CONFIG] Applications dir: {APPLICATIONS_DIR}")
