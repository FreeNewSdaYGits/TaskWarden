"""Проверка логики моста: подписка на ntfy и передача сообщения в Apprise."""

import os
import sys
import threading
import time

os.environ.setdefault("NTFY_TOPIC", "vats733614-01864742088727")
os.environ.setdefault("APPRISE_URLS", "json://localhost/placeholder.json")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "outputs"))
import ntfy_to_apprise as bridge  # noqa: E402

received = []


def fake_notify(self, body=None, title=None, **kwargs):
    received.append((title, body))
    return True


import apprise  # noqa: E402

apprise.Apprise.notify = fake_notify
apprise.Apprise.add = lambda self, url: True


def publisher():
    import requests

    time.sleep(2)
    requests.post(
        "https://ntfy.sh/",
        json={
            "topic": os.environ["NTFY_TOPIC"],
            "title": "Не перезвонили: 2 звонка",
            "message": "14:16  +79625805610  51 с\n14:44  +79625805610  76 с",
        },
        timeout=15,
    )


threading.Thread(target=publisher, daemon=True).start()

deadline = time.time() + 12
for msg in bridge.stream_messages(int(time.time())):
    received.append((msg.get("title"), msg.get("message")))
    if received:
        break
    if time.time() > deadline:
        break

print("получено сообщений:", len(received))
for title, body in received:
    print("заголовок:", title)
    print("текст:", (body or "").replace("\n", " | "))
sys.exit(0 if received else 1)
