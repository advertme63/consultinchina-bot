# E2E-симулятор бота

Подаёт в настоящий Dispatcher поддельные Update от тестовых пользователей (ID −2001…−2099, source = `test`),
перехватывает все вызовы Bot API (send_message, edit_message, edit_markup, answer_callback) и пишет их в лог.
Реальные БД, поиск и Claude — используются. В Telegram (в том числе в группу «CinC Лиды» и админу) не уходит ничего:
фиктивный токен, своя сессия Bot API без сети, плюс блокировка любых запросов к api.telegram.org.
До и после прогона тестовые пользователи удаляются из messages, leads, events, unanswered_questions, tickets, users.

## Запуск

```bash
/opt/consultinchina-bot/bot/scripts/e2e/run.sh /opt/consultinchina-bot/bot/scripts/e2e/selftest.json
```

Вывод: `/root/e2e_runs/<дата_время>/transcript.md` (читаемый), `transcript.jsonl`, `summary.json`
(вызовы и токены Claude, оценка $, проверка предохранителя, итоги очистки).

## Как дождаться конца прогона

`run.sh` работает на переднем плане и возвращается только после конца прогона (код выхода = код симулятора).
В конце он всегда пишет маркер `<папка_вывода>/DONE` со строкой `exit=<код> <время>`.

- Проще всего — на переднем плане: `bot/scripts/e2e/run.sh сценарии.json папка` (~10 мин на полный набор).
- В фоне — ждать по PID: `bot/scripts/e2e/run.sh сценарии.json папка > папка.log 2>&1 & pid=$!; wait $pid; echo "exit=$?"`.
- Из другой сессии — ждать маркер: `until [ -f папка/DONE ]; do sleep 15; done; cat папка/DONE`.
- **Не ждать через `pgrep -f '…simulate.py'`**: шаблон есть в командной строке самой оболочки ожидания,
  поэтому pgrep находит её и цикл не заканчивается (08.10.2026 так ожидание провисело 2 часа).

## Наборы сценариев

В репозитории: `bot/scripts/e2e/scenarios/scenarios.json` (основной, 24 сценария), `scenarios_rerun.json` (J2–J5), `scenarios_e4.json`, `scenarios_e5.json`, `scenarios_e6.json`, `scenarios_e7.json` (комиссии, C01–C15). Рабочие копии и выводы прогонов — в `/root/e2e_runs/`. Подмножество для прогона: выбрать сценарии по `id` в отдельный файл.

## Формат сценариев

```json
{"scenarios": [
  {"id": "S01", "title": "…", "user": -2002, "fresh": true, "first_name": "Иван", "steps": [
    {"start": ""},                                  // /start (или {"start": "reels"} — /start reels)
    {"click": "🛒 Продаю на маркетплейсах"},         // нажать inline-кнопку по тексту (последнее сообщение, где она есть)
    {"send": "текст"},                              // текстовое сообщение, в т.ч. кнопки обычной клавиатуры меню
    {"contact": "+79990000000"},                    // «Поделиться контактом»
    {"preset_questions_used": 11},                  // тестовый shortcut: выставить счётчик вопросов за сегодня
    {"note": "комментарий"}
  ]}
]}
```

- `fresh: true` — перед сценарием стереть пользователя из БД и сбросить его состояние диалога.
- Э6, подписка на канал: в симуляторе стена включена (`SUB_GATE_ENABLED`) и тестовые пользователи НЕ исключены из проверки;
  getChatMember по умолчанию отвечает `member`. Шаги:
  `{"sub": "left"}` — статус в канале (`member` / `left` / `kicked` / `administrator` / `creator` / `restricted_member` /
  `restricted_left` / `error` — сбой Telegram), кэш бота для пользователя сбрасывается;
  `{"set_role": "client"}`; `{"as_admin": true}` — этот пользователь — админ до конца сценария;
  `{"tests_exempt": true}` — тестовые без проверки, как в рабочем боте; `{"gate": "off"}` — как при `SUB_GATE_ENABLED=0`;
  `{"report": "daily"}` — отчёт 09:00 за сегодня по тестовым (scope=test) в лог прогона.
  В верхнем уровне файла `"sub_gate": false` выключает стену на весь прогон. Сценарии Э6 — `scenarios/scenarios_e6.json`.
- Э7, комиссии: в верхнем уровне `"admin_user": -2071` (этот пользователь — админ: роутер админа строится с ним) и
  `"wb_fixtures": {"RU": "wb_ru.json", "CN": "wb_cn.json"}` — ответы WB API из папки сценариев (к WB сеть не ходит).
  Шаги: `{"wb_mock": "ok"}` (`ok` / `429` / `empty` / `broken` / `jump` / `missing` / `low_coverage`);
  `{"document": "файл.xlsx", "file_name": "имя для бота.xlsx"}` — прислать файл из папки сценариев;
  `{"set_settings": {"commissions:wb:last_success": "today-11", "…": null}}` (null — удалить ключ);
  `{"tick": "08:30"}` — один проход планировщика в это время; `{"db_commissions": ["Одежда"]}` — строки mp_commissions + settings.
  До прогона симулятор сохраняет mp_commissions и settings `commissions:%` / `job:commissions:last_run`, после — возвращает
  (`mp_commissions_restored.identical` в summary). Справочник не грузится в базу знаний, а пишется в `kb_commissions_N.md` в папке вывода.
  Фикстуры Э7 (wb_ru.json, wb_cn.json, xlsx Ozon, broken.xlsx) — в `/root/e2e_runs/e7_in/`, в репозиторий не входят.
- У любого шага можно указать `"expect": "…"` — попадёт в отчёт рядом с фактом.
- `click` ищет кнопку: точное совпадение → без учёта регистра → по подстроке.
- Пауза между запросами к Voyage — 21 с (бесплатный тариф, 3 запроса в минуту), поэтому вопросы к ИИ идут медленно.

В логе у каждого исходящего: `target` = `user` / `LEADS_GROUP` / `ADMIN` / `FOREIGN_CHAT`.
HTML проверяется как в Telegram (разрешённые теги, баланс, `&`, длина 4096): при ошибке симулятор отвечает
боту `TelegramBadRequest`, как настоящий Telegram, и пишет `telegram_error`. Предупреждения и ошибки из логов бота —
строки `log`.
