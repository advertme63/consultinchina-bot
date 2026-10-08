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
    # Группа «CinC Лиды»; после миграции в супергруппу актуальный ID берётся из settings.leads_chat_id
    LEADS_CHAT_ID = int(os.environ["LEADS_CHAT_ID"]) if os.environ.get("LEADS_CHAT_ID") else None
    # Данные mp_commissions старше — цифры в итоге селлера не показываем (Э3.5, раздел 2)
    COMMISSIONS_MAX_AGE_DAYS = int(os.environ.get("COMMISSIONS_MAX_AGE_DAYS", "60"))
    DAILY_LIMIT_GUEST = int(os.environ.get("DAILY_LIMIT_GUEST", "15"))
    # 15 для всех — решение Ивана 08.10 (было: клиент 30). Админ — без лимита
    DAILY_LIMIT_CLIENT = int(os.environ.get("DAILY_LIMIT_CLIENT", "15"))


config = Config()
