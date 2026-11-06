#!/usr/bin/env python3
"""Мост ntfy → Apprise.

Подписывается на тему ntfy и пересылает каждое сообщение во все каналы,
описанные в APPRISE_URLS (Telegram, почта, Slack и т.д.).

Переменные окружения:
  NTFY_TOPIC   — тема ntfy (обязательно)
  APPRISE_URLS — список URL Apprise через пробел (обязательно), например
                 tgram://123456:AAE.../987654321
  NTFY_SERVER  — сервер ntfy, по умолчанию https://ntfy.sh
"""

import json
import os
import sys
import time
import traceback

import requests
import apprise

NTFY_SERVER = (os.environ.get("NTFY_SERVER") or "https://ntfy.sh").rstrip("/")
NTFY_TOPIC = (os.environ.get("NTFY_TOPIC") or "").strip()
APPRISE_URLS = (os.environ.get("APPRISE_URLS") or "").split()

LOG_PREFIX = "[ntfy→apprise]"


def log(msg):
    print("%s %s %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), LOG_PREFIX, msg), flush=True)


def build_apprise():
    ap = apprise.Apprise()
    loaded = 0
    for url in APPRISE_URLS:
        if ap.add(url):
            loaded += 1
            log("канал подключён: %s" % url.split("://")[0])
        else:
            log("ОШИБКА: не удалось разобрать URL %s" % url.split("://")[0])
    return ap, loaded


def check_telegram():
    """Проверяет, доступен ли Telegram API с этого хоста. Не блокирует запуск."""
    try:
        r = requests.get("https://api.telegram.org/", timeout=10)
        log("Telegram API доступен (HTTP %s)" % r.status_code)
        return True
    except Exception as e:  # noqa: BLE001
        log("ВНИМАНИЕ: Telegram API недоступен (%s). Доставка в Telegram работать не будет." % type(e).__name__)
        return False


def stream_messages(since):
    """Отдаёт сообщения из темы ntfy начиная с since (unix time)."""
    url = "%s/%s/json?since=%d" % (NTFY_SERVER, NTFY_TOPIC, since)
    with requests.get(url, stream=True, timeout=(15, 600)) as r:
        r.raise_for_status()
        for raw in r.iter_lines(decode_unicode=True):
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if data.get("event") != "message":
                continue
            yield data


def main():
    if not NTFY_TOPIC:
        log("не задана NTFY_TOPIC")
        return 2

    ap, loaded = build_apprise()
    if not loaded:
        log("нет ни одного рабочего канала в APPRISE_URLS")
        return 2

    check_telegram()

    since = int(time.time())
    seen = []
    log("слушаю тему %s на %s" % (NTFY_TOPIC, NTFY_SERVER))

    while True:
        try:
            for msg in stream_messages(since):
                mid = msg.get("id")
                ts = int(msg.get("time") or since)
                since = max(since, ts)
                if mid and mid in seen:
                    continue
                seen = (seen + [mid])[-500:] if mid else seen

                title = msg.get("title") or "Пропущенные звонки"
                body = msg.get("message") or ""
                if ap.notify(body=body, title=title):
                    log("переслано: %s" % title)
                else:
                    log("ОШИБКА доставки: %s" % title)
        except Exception:  # noqa: BLE001 — переподключение важнее падения
            log("обрыв соединения, переподключаюсь через 15 с")
            traceback.print_exc(file=sys.stderr)
            time.sleep(15)
            since = int(time.time())


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        pass
