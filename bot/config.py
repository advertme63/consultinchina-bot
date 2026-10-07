import os


class Config:
    BOT_TOKEN = os.environ["BOT_TOKEN"]
    ADMIN_TELEGRAM_ID = int(os.environ["ADMIN_TELEGRAM_ID"])
    ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
    VOYAGE_API_KEY = os.environ.get("VOYAGE_API_KEY", "")
    DATABASE_URL = os.environ["DATABASE_URL"]
    CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5")
    VOYAGE_MODEL = os.environ.get("VOYAGE_MODEL", "voyage-3-lite")
    # Порог косинусного расстояния лучшего фрагмента (ТЗ 3.4): выше — фрагменты нерелевантны
    RELEVANCE_THRESHOLD = float(os.environ.get("RELEVANCE_THRESHOLD", "0.68"))


config = Config()
