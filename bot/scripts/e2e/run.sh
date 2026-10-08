#!/bin/bash
# E2E-симулятор бота (см. README.md рядом).
#   bot/scripts/e2e/run.sh <сценарии.json> [папка_вывода]   — прогон (по умолчанию вывод в /root/e2e_runs/<дата_время>)
#   bot/scripts/e2e/run.sh --cleanup                         — только удалить тестовых пользователей −2001…−2099
# Код бота берётся из рабочей копии (bot/ монтируется в /app), образ не пересобирается, cinc_bot не трогается.
# Скрипт блокирует до конца прогона. Как ждать — см. README.md рядом, раздел «Как дождаться конца прогона»
# (НЕ через `pgrep -f`: шаблон совпадает с командной строкой самой оболочки ожидания, и цикл не кончается).
set -euo pipefail
if [ "${1:-}" != "--cleanup" ]; then
  SC=$(realpath "$1")
  OUT=$(realpath -m "${2:-/root/e2e_runs/$(date +%Y%m%d_%H%M%S)}")
fi
cd "$(dirname "$(realpath "$0")")/../../.."
if [ "${1:-}" = "--cleanup" ]; then
  exec docker compose run --rm --no-deps -T -v "$PWD/bot:/app" bot python scripts/e2e/simulate.py --cleanup
fi
mkdir -p "$OUT"
rm -f "$OUT/DONE"
set +e
docker compose run --rm --no-deps -T \
  -v "$PWD/bot:/app" -v "$(dirname "$SC"):/e2e_in:ro" -v "$OUT:/e2e_out" \
  bot python scripts/e2e/simulate.py "/e2e_in/$(basename "$SC")" /e2e_out
rc=$?
set -e
# Маркер конца прогона пишется всегда (и при ошибке): ждать его, а не процесс
echo "exit=$rc $(date '+%F %T')" > "$OUT/DONE"
echo "Вывод: $OUT/transcript.md, $OUT/summary.json; маркер: $OUT/DONE (exit=$rc)"
exit $rc
