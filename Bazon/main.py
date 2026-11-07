"""Планировщик для постоянного контейнера (Amvera): отчёт 19:30 и зеркало наличных.

Скрипты запускаются отдельными процессами, поэтому их можно вызывать и вручную,
и через cron без изменений.

Переменные окружения:
  SEND=1           — реально отправлять в Telegram (иначе dry-run в журнал)
  REPORT_AT=19:30  — местное время ежедневного отчёта
  POLL_SECONDS=60  — период опроса наличных
  STATE_PATH       — состояние наличных, на Amvera: /data/cash_state.json
  REPORT_STATE     — отметка об отправленном отчёте: /data/report_state.json
  LOG_PATH         — файл журнала, на Amvera: /data/app.log
"""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
TZ = dt.timezone(dt.timedelta(hours=11))
SEND = os.environ.get("SEND", "0") == "1"
REPORT_AT = os.environ.get("REPORT_AT", "19:30")
POLL_SECONDS = int(os.environ.get("POLL_SECONDS", "60"))
REPORT_STATE = os.environ.get("REPORT_STATE", "/data/report_state.json")
LOG_PATH = os.environ.get("LOG_PATH", "/data/app.log")


def log(msg: str) -> None:
    line = f"{dt.datetime.now(TZ):%Y-%m-%d %H:%M:%S} {msg}"
    print(line, flush=True)
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > 5_000_000:
            os.replace(LOG_PATH, LOG_PATH + ".1")
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def load_json(path: str, default: dict) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(path: str, data: dict) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, path)
    except OSError as e:
        log(f"не удалось сохранить {path}: {e}")


def run_script(args: list[str], timeout: int = 600) -> int:
    cmd = [sys.executable, os.path.join(HERE, args[0])] + args[1:]
    try:
        p = subprocess.run(cmd, cwd=HERE, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        log(f"{args[0]}: превышено время ожидания {timeout} с")
        return 124
    for stream, name in ((p.stdout, "out"), (p.stderr, "err")):
        text = (stream or "").strip()
        if text:
            log(f"{args[0]} [{name}] {text[-2000:]}")
    return p.returncode


def report_due(state: dict, now: dt.datetime) -> bool:
    hh, mm = (int(x) for x in REPORT_AT.split(":"))
    plan = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    return now >= plan and state.get("last_report_date") != now.date().isoformat()


def send_report(state: dict, now: dt.datetime) -> None:
    args = ["report.py", "--date", now.date().isoformat()]
    if SEND:
        args.append("--send")
    code = run_script(args)
    if code == 0:
        state["last_report_date"] = now.date().isoformat()
        save_json(REPORT_STATE, state)
        log(f"отчёт за {now.date()} сформирован" + ("" if SEND else " (dry-run)"))
    else:
        log(f"отчёт за {now.date()} не сформирован, код {code}; повтор через минуту")


def poll_cash() -> None:
    args = ["cash_watch.py", "--limit", "50"]
    if SEND:
        args.append("--send")
    code = run_script(args, timeout=300)
    if code != 0:
        log(f"опрос наличных завершился с кодом {code}")


def main() -> None:
    log(f"старт планировщика: SEND={SEND}, отчёт {REPORT_AT}, опрос {POLL_SECONDS} с")
    state = load_json(REPORT_STATE, {})
    next_poll = 0.0
    while True:
        try:
            now = dt.datetime.now(TZ)
            if report_due(state, now):
                send_report(state, now)
            if time.time() >= next_poll:
                poll_cash()
                next_poll = time.time() + POLL_SECONDS
            time.sleep(20)
        except KeyboardInterrupt:
            log("остановка по сигналу")
            return
        except Exception:
            log("сбой в цикле: " + traceback.format_exc(limit=3))
            time.sleep(60)


if __name__ == "__main__":
    main()
