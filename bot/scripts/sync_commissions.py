"""Э7: комиссии вручную из консоли — проверка без записи (по умолчанию) или запись.

Запуск (из /opt/consultinchina-bot):
  docker compose run --rm --no-deps -v /root/ozon_files:/ozon:ro bot python scripts/sync_commissions.py \
      [--wb] [--ozon-rf /ozon/файл.xlsx] [--ozon-cn /ozon/файл.xlsx] [--write]
Без --write ничего не пишет (ни mp_commissions, ни settings, ни базу знаний) и никуда не отправляет.
С --write — запись площадок, прошедших защиту, и пересборка справочника (отчёт в группу не шлётся).
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, "/app")

import database
from services import commission_sync as cs


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wb", action="store_true")
    ap.add_argument("--ozon-rf")
    ap.add_argument("--ozon-cn")
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    await database.init_pool()
    try:
        current = await cs.current_rows()
        plans = []
        if a.wb:
            try:
                plans.append(await cs.build_wb_plan())
            except cs.SyncError as e:
                print(f"WB: {e}")
        for path in filter(None, (a.ozon_rf, a.ozon_cn)):
            try:
                plans.append(cs.build_ozon_plan(Path(path).read_bytes(), Path(path).name, current))
            except cs.SyncError as e:
                print(f"{path}: {e}")
        for p in plans:
            print("=" * 60)
            print(cs.report_text(p, applied=False))
            print(cs.table_text(p, current))
        if a.write:
            written = [p for p in plans if p.ok]
            for p in written:
                await cs.apply(p)
            if written:
                print("Справочник:", await cs.rebuild_kb())
            print("Записано:", [p.platform for p in written] or "ничего")
    finally:
        await database.close_pool()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
