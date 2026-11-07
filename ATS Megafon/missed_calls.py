#!/usr/bin/env python3
"""Пропущенные входящие без успешного перезвона с АТС vats733614.megapbx.ru.

Печатает текст готового отчёта. Параметры: --minutes N (окно до текущего момента)
или --start/--end в ISO 8601 (UTC).
"""

import argparse
import datetime as dt
import json
import os
import sys
import urllib.request
import urllib.error

BASE = "https://vats733614.megapbx.ru/api/v2"
LOGIN = "admin@vats733614.megapbx.ru"
PASSWORD = "Region@65"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
TZ = dt.timezone(dt.timedelta(hours=11))  # сахалинское время, UTC+11
NTFY_TOPIC = "vats733614-01864742088727"  # тема push-уведомлений в ntfy.sh
MENTIONS = os.environ.get(
    "NTFY_MENTIONS", "@Alexey_Buchnev @MrFreeday @Shanni0125"
).strip()

DEST = {
    "hello": "приветствие (IVR)",
    "offtime": "нерабочее время",
    "group": "группа",
    "user": "сотрудник",
    "ivr": "IVR",
}


def post(path, payload, cookie=None):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(BASE + "/" + path, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", UA)
    if cookie:
        req.add_header("Cookie", cookie)
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = r.read()
        sc = r.headers.get("Set-Cookie", "")
    body = json.loads(raw.decode() or "{}")
    new_cookie = cookie
    if sc and "s=" in sc:
        new_cookie = "s=" + sc.split("s=", 1)[1].split(";")[0]
    return body, new_cookie


def http_post(url, payload, headers=None):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", UA)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def notify(topic, title, message):
    """Отправляет push в ntfy.sh. Ошибка доставки не должна ломать отчёт."""
    try:
        http_post(
            "https://ntfy.sh/",
            {"topic": topic, "title": title, "message": message,
             "tags": ["telephone"], "priority": 3},
        )
        print("push: отправлен в тему %s" % topic, file=sys.stderr)
        return True
    except Exception as e:  # noqa: BLE001
        print("push: не удалось отправить (%s)" % e, file=sys.stderr)
        return False


def human_dest(call, groups):
    dest = call.get("destination")
    emp = call.get("employeeId") or ""
    name = call.get("employeeRealName") or ""
    if dest == "group":
        g = groups.get(emp, name)
        return "группа «%s»" % (g or emp)
    if dest in ("hello", "offtime", "ivr"):
        return DEST[dest]
    if name and not name.isdigit():
        return name
    return "линия %s" % (emp or "—")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=int, default=None)
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--ntfy-topic", default=NTFY_TOPIC)
    ap.add_argument("--no-ntfy", action="store_true")
    a = ap.parse_args()

    now = dt.datetime.now(dt.timezone.utc)
    if a.start:
        start = dt.datetime.fromisoformat(a.start.replace("Z", "+00:00"))
        end = dt.datetime.fromisoformat(a.end.replace("Z", "+00:00"))
    elif a.minutes:
        end = now
        start = end - dt.timedelta(minutes=a.minutes)
    else:  # накопительно: с начала текущих суток по сахалинскому времени
        end = now
        start = now.astimezone(TZ).replace(hour=0, minute=0, second=0, microsecond=0)

    def fmt(x):
        return x.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    _, cookie = post("login", {"u": LOGIN, "p": PASSWORD, "r": True, "Language": "ru-RU"})
    body, _ = post(
        "calls-history",
        {
            "owner": "company",
            "start": fmt(start),
            "end": fmt(end),
            "CallType": "all",
            "missedStatus": [3],
            "counted": 1,
            "thesaurus_id": [],
        },
        cookie,
    )

    groups = body.get("groupsRealNames") or {}
    calls = [c for c in (body.get("calls") or []) if c.get("type") == "missed"]
    calls.sort(key=lambda c: c.get("start") or "")

    rows = []
    for c in calls:
        t = dt.datetime.fromisoformat(c["start"].replace("Z", "+00:00")).astimezone(TZ)
        rows.append(
            {
                "time": t.strftime("%H:%M"),
                "date": t.strftime("%d.%m"),
                "phone": c.get("clientTelnum") or "номер скрыт",
                "wait": int(c.get("wait") or 0),
                "dest": human_dest(c, groups),
                "line": c.get("via") or "—",
                "attempts": int(c.get("missedCount") or 1),
                "status": int(c.get("missedStatus") or 3),
            }
        )

    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return

    period = "%s–%s (сах. время)" % (
        start.astimezone(TZ).strftime("%d.%m %H:%M"),
        end.astimezone(TZ).strftime("%d.%m %H:%M"),
    )
    if not rows:
        print("Пропущенных звонков без перезвона за период %s не было." % period)
        return

    word = "звонок" if len(rows) == 1 else ("звонка" if 2 <= len(rows) <= 4 else "звонков")
    out = [
        "**Пропущенные без перезвона** — %s, %d %s:"
        % (period, len(rows), word),
        "",
    ]
    notes_used = any(r["attempts"] > 1 or r["status"] == 4 for r in rows)
    head = "| Время | Номер клиента | Ждал | Маршрут | Линия |"
    sep = "|---|---|---|---|---|"
    if notes_used:
        head += " Примечание |"
        sep += "---|"
    out += [head, sep]
    for r in rows:
        note = []
        if r["attempts"] > 1:
            note.append("попыток: %d" % r["attempts"])
        if r["status"] == 4:
            note.append("перезванивали, но не дозвонились")
        line = "| %s | %s | %d с | %s | %s |" % (
            r["time"], r["phone"], r["wait"], r["dest"], r["line"],
        )
        if notes_used:
            line += " %s |" % ("; ".join(note) if note else "—")
        out.append(line)
    print("\n".join(out))

    if not a.no_ntfy and a.ntfy_topic:
        word = "звонок" if len(rows) == 1 else ("звонка" if 2 <= len(rows) <= 4 else "звонков")
        lines = []
        for r in rows:
            tag = "" if r["attempts"] == 1 else " · попыток: %d" % r["attempts"]
            lines.append("%s  %s  %d с  %s%s" % (r["time"], r["phone"], r["wait"], r["dest"], tag))
        if len(lines) > 40:
            lines = lines[:40] + ["…и ещё %d" % (len(rows) - 40)]
        if MENTIONS:
            lines = [MENTIONS, ""] + lines
        notify(
            a.ntfy_topic,
            "Не перезвонили: %d %s" % (len(rows), word),
            "\n".join(lines),
        )


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as e:
        print("Ошибка API: HTTP %s %s" % (e.code, e.read()[:300].decode(errors="replace")))
        sys.exit(1)
