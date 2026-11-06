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
import logging
import os
import re
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


def telegram_targets():
    """Достаёт (токен, chat_id) из адресов tgram:// — для диагностики и запасной отправки."""
    out = []
    for url in APPRISE_URLS:
        m = re.match(r"^tgram://([^/]+)/([^/?]+)", url)
        if m:
            out.append((m.group(1), m.group(2).split("/")[0]))
    return out


def telegram_send_raw(token, chat_id, title, body):
    """Прямая отправка через Bot API. Возвращает (успех, пояснение от Telegram)."""
    text = ("%s\n\n%s" % (title, body)) if title else body
    try:
        r = requests.post(
            "https://api.telegram.org/bot%s/sendMessage" % token,
            json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
            timeout=25,
        )
        data = {}
        try:
            data = r.json()
        except ValueError:
            pass
        if data.get("ok"):
            return True, "ok"
        return False, data.get("description") or ("HTTP %s" % r.status_code)
    except Exception as e:  # noqa: BLE001
        return False, "%s: %s" % (type(e).__name__, e)


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

    since = int(time.time())  # фиксируем до проверок, чтобы не потерять сообщения при старте
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("apprise").setLevel(logging.INFO)
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    ap, loaded = build_apprise()
    if not loaded:
        log("нет ни одного рабочего канала в APPRISE_URLS")
        return 2

    check_telegram()

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
                    log("Apprise не смог доставить: %s — пробую напрямую через Bot API" % title)
                    delivered = False
                    for token, chat_id in telegram_targets():
                        ok, why = telegram_send_raw(token, chat_id, title, body)
                        if ok:
                            delivered = True
                            log("переслано напрямую: %s (chat_id=%s)" % (title, chat_id))
                        else:
                            log("ОШИБКА доставки (chat_id=%s): %s" % (chat_id, why))
                    if not delivered:
                        log("сообщение не доставлено: %s" % title)
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
