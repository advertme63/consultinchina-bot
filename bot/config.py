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
    # Вес полнотекстового ранга в RRF гибридного поиска (подобран на тестах Э2)
    FTS_WEIGHT = float(os.environ.get("FTS_WEIGHT", "2.0"))
    DAILY_LIMIT_GUEST = int(os.environ.get("DAILY_LIMIT_GUEST", "15"))
    DAILY_LIMIT_CLIENT = int(os.environ.get("DAILY_LIMIT_CLIENT", "30"))


config = Config()
