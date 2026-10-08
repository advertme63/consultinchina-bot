#!/bin/bash
# E2E-симулятор бота (см. README.md рядом).
#   bot/scripts/e2e/run.sh <сценарии.json> [папка_вывода]   — прогон (по умолчанию вывод в /root/e2e_runs/<дата_время>)
#   bot/scripts/e2e/run.sh --cleanup                         — только удалить тестовых пользователей −2001…−2099
# Код бота берётся из рабочей копии (bot/ монтируется в /app), образ не пересобирается, cinc_bot не трогается.
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
docker compose run --rm --no-deps -T \
  -v "$PWD/bot:/app" -v "$(dirname "$SC"):/e2e_in:ro" -v "$OUT:/e2e_out" \
  bot python scripts/e2e/simulate.py "/e2e_in/$(basename "$SC")" /e2e_out
echo "Вывод: $OUT/transcript.md, $OUT/summary.json"
