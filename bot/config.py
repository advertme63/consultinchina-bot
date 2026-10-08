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
    # Группа «CinC Отчёты» (отчёт 09:00, недельный анализ); после миграции — settings.reports_chat_id
    REPORTS_CHAT_ID = int(os.environ["REPORTS_CHAT_ID"]) if os.environ.get("REPORTS_CHAT_ID") else None
    # Цены Claude, USD за 1 млн токенов (claude-sonnet-5, страница цен Anthropic; значения — из .env)
    PRICE_INPUT = float(os.environ.get("PRICE_INPUT", "2"))
    PRICE_CACHE_WRITE_5M = float(os.environ.get("PRICE_CACHE_WRITE_5M", "2.5"))
    PRICE_CACHE_WRITE_1H = float(os.environ.get("PRICE_CACHE_WRITE_1H", "4"))
    PRICE_CACHE_READ = float(os.environ.get("PRICE_CACHE_READ", "0.20"))
    PRICE_OUTPUT = float(os.environ.get("PRICE_OUTPUT", "10"))
    # Планировщик (отчёты, сводка, анализ, очистка) — только в рабочем процессе main.py
    SCHEDULER_ENABLED = os.environ.get("SCHEDULER_ENABLED", "1") == "1"
    # Данные mp_commissions старше — цифры в итоге селлера не показываем (Э3.5, раздел 2)
    COMMISSIONS_MAX_AGE_DAYS = int(os.environ.get("COMMISSIONS_MAX_AGE_DAYS", "60"))
    DAILY_LIMIT_GUEST = int(os.environ.get("DAILY_LIMIT_GUEST", "15"))
    # 15 для всех — решение Ивана 08.10 (было: клиент 30). Админ — без лимита
    DAILY_LIMIT_CLIENT = int(os.environ.get("DAILY_LIMIT_CLIENT", "15"))


config = Config()
