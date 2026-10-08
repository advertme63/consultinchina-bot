# ConsultInChina Telegram-бот — MVP (Фаза 1)

## Развёртывание

```bash
cd /opt/consultinchina-bot
docker compose build
docker compose up -d
docker compose logs -f bot
```

## Секреты (.env, chmod 600)

```
BOT_TOKEN=...
ADMIN_TELEGRAM_ID=...
ANTHROPIC_API_KEY=
VOYAGE_API_KEY=
POSTGRES_PASSWORD=...
DATABASE_URL=postgresql://cinc:<POSTGRES_PASSWORD>@db:5432/cinc
```

После добавления `ANTHROPIC_API_KEY` и `VOYAGE_API_KEY`:

```bash
docker compose restart bot
docker compose exec bot python scripts/ingest_initial.py
```

## Админ-команды (только владелец, ADMIN_TELEGRAM_ID)

- `/grant_trial <telegram_id> <дней>` — выдать пробный доступ
- `/grant_full <telegram_id>` — открыть полный доступ (клиент)
- `/revoke <telegram_id>` — закрыть доступ
- `/status <telegram_id>` — статус пользователя
- `/list_access` — список активных доступов
- `/upload_doc` — загрузить новый PDF в справочник (с тегами темы/типа)
- `/add_file` — добавить файл в раздел «Документы»

## Проверка состояния

```bash
docker ps
docker stats --no-stream
docker compose logs --tail 100 bot
docker compose logs --tail 100 db
```

## E2E-тест бота (симулятор)

Поддельные Update от тестовых пользователей −2001…−2099 → настоящий Dispatcher; все вызовы Bot API перехватываются,
в Telegram и в группу лидов ничего не уходит. Реальные БД, поиск и Claude используются; тестовые пользователи удаляются
до и после прогона. Подробно — `bot/scripts/e2e/README.md`.

```bash
bot/scripts/e2e/run.sh bot/scripts/e2e/selftest.json          # самотест предохранителя (без Claude)
bot/scripts/e2e/run.sh /root/e2e_runs/scenarios.json          # полный прогон сценариев тестировщика (~$2, ~40 мин)
bot/scripts/e2e/run.sh --cleanup                              # только очистка тестовых пользователей
```
