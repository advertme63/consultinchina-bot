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

## Памятка: меню и подписка на канал (Э6, с 12.10.2026)

Меню (4 ряда):

```
[ 📚 Справочник-консультант           ]
[ 📊 Подходит ли мне компания в Китае ]
[ 👤 Связаться со специалистом        ]
[ 📄 Материалы ] [ ℹ️ Мой лимит        ]
```

Старые кнопки «💬 Задать вопрос» и «📚 Справочник» работают как «📚 Справочник-консультант».

**Стена подписки.** Справочник (кнопка, вопрос текстом, подсказки под ответами) — только подписчикам канала
`SUB_CHANNEL` (@consultinchina), с подпиской — 15 вопросов в сутки (00:00 по Шанхаю), пробных вопросов нет.
Без подписки бот показывает стену: «📢 Перейти в канал» + «✅ Я подписался». Вопрос, написанный до подписки,
сохраняется (событие `sub_gate_shown`, сутки), лимит не списывается; после «✅ Я подписался» бот на него отвечает.
Без подписки работают: квалификация, «👤 Связаться со специалистом», «📝 Заказать», «📄 Материалы», «ℹ️ Мой лимит».

**Без проверки подписки:** админ (`ADMIN_TELEGRAM_ID`), роль `client` (`/grant_full`), пришедшие по `?start=call`
(метка `call` в `users.source` или в событии start), тестовые (`source = 'test'` или ID < 0).
Проверяя стену своим аккаунтом, временно уберите у него `client` и `test` — иначе стены не будет.

**Проверка — getChatMember**: бот должен быть администратором канала (права можно все выключить).
Кэш статуса — 10 мин; «✅ Я подписался» проверяет без кэша. Ошибка Telegram → пропускаем как подписанного,
WARNING в логе и событие `sub_check_failed`. События: `sub_gate_shown`, `sub_confirmed`, `sub_check_failed`;
в отчёте 09:00, `/stats` и недельном анализе — «Стена подписки: показали N · подписались M (K%)».

**Выключатель** — `.env`: `SUB_GATE_ENABLED=1` (стена включена), `0` или нет строки — всё как до Э6 (без стены,
старое приветствие и «Мой лимит»; новое меню остаётся). После правки `.env`:
`docker compose up -d --no-deps --force-recreate bot` (пересборка не нужна).

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
