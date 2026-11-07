"""Зеркалирование операций с наличными в Telegram. Только чтение Bazon.

По умолчанию ничего не отправляет, а печатает сообщения (dry-run).
Отправка: --send при заданных TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_CASH.
Состояние: work/state/cash_state.json (last_order_id).
"""
from __future__ import annotations
import argparse, html, json, os, time, urllib.parse, urllib.request, datetime as dt
from bazon import Bazon

LOGIN = os.environ.get("BAZON_LOGIN", "c1575_external_app")
PASSWORD = os.environ.get("BAZON_PASSWORD", "")
TOKEN = os.environ.get("BAZON_TOKEN_PATH", "/tmp/bz_token.json")
TZ = dt.timezone(dt.timedelta(hours=11))
STATE = os.environ.get("STATE_PATH") or os.path.join(os.path.dirname(__file__), "state", "cash_state.json")


def load_state() -> dict:
    try:
        return json.load(open(STATE, encoding="utf-8"))
    except (OSError, ValueError):
        return {"last_order_id": 0, "recent": []}


def save_state(st: dict) -> None:
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    tmp = STATE + ".tmp"
    json.dump(st, open(tmp, "w", encoding="utf-8"), ensure_ascii=False)
    os.replace(tmp, STATE)


def money(x: float) -> str:
    return f"{abs(x):,.0f}".replace(",", " ")


def mask_phone(raw: str) -> str:
    """Оставляет последние 4 цифры телефона, остальные цифры заменяет на *."""
    raw = (raw or "").strip()
    digits = [i for i, ch in enumerate(raw) if ch.isdigit()]
    if len(digits) <= 4:
        return raw
    hide = set(digits[:-4])
    return "".join("*" if i in hide else ch for i, ch in enumerate(raw))


class Names:
    def __init__(self, b: Bazon):
        self.b = b
        self.managers = {m["id"]: m["name"] for m in self._all("getManagers", "managers")}
        self.contractors: dict[int, str] = {}

    def _all(self, method, key):
        out, off = [], 0
        while True:
            q = {"order": "asc", "limit": 500}
            if off:
                q["offset"] = off
            r = self.b.call(method, q)
            chunk = r.get(key, [])
            out += chunk
            if not chunk or len(out) >= r.get("found", 0):
                return out
            off += len(chunk)
            time.sleep(0.1)

    def contractor(self, cid: int) -> str:
        if cid in self.contractors:
            return self.contractors[cid]
        try:
            r = self.b.call("getContractors", {"order": "asc", "id": cid})
            items = r.get("contractors", [])
            c = items[0] if items else {}
            name = (c.get("name") or "").strip()
            phone = mask_phone(c.get("phone") or "")
            val = name or phone or "Частное лицо"
        except Exception:
            val = f"id {cid}"
        self.contractors[cid] = val
        return val


def message(o: dict, names: Names) -> str:
    sign = "приход" if o["type"] == "income" else "расход"
    icon = "💵" if o["type"] == "income" else "💸"
    kind = {"sale": "Сделка", "return": "Возврат"}.get(o["document_type"], o["document_type"])
    ts = dt.datetime.fromisoformat(o["created_at"]).astimezone(TZ).strftime("%d.%m.%Y %H:%M")
    who = names.contractor(o["contractor_id"]) if o["contractor_id"] else "—"
    creator = names.managers.get(o["created_by"], f"id {o['created_by']}")
    lines = [
        f"{icon} <b>Наличные · Касса, офис · {sign}</b>",
        f"<b>{money(o['sum'])} ₽</b>",
        f"{kind} №{html.escape(o['document_number'] or '—')} · {ts} (Сах.)",
        f"Контрагент: {html.escape(who)}",
        f"Создал: {html.escape(creator)}",
    ]
    if o.get("comment"):
        lines.append(f"Комментарий: {html.escape(o['comment'])}")
    return "\n".join(lines)


def send(text: str) -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat = os.environ.get("TELEGRAM_CHAT_ID") or os.environ.get("TELEGRAM_CHAT_CASH")
    payload = {
        "chat_id": chat, "text": text, "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }
    thread = os.environ.get("TELEGRAM_TOPIC_CASH")
    if thread:
        payload["message_thread_id"] = thread
    data = urllib.parse.urlencode(payload).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=data)
    with urllib.request.urlopen(req, timeout=30) as r:
        r.read()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--init", action="store_true", help="запомнить текущий последний id без отправки")
    ap.add_argument("--from-id", type=int, help="начать с этого id (не сохраняя состояние)")
    ap.add_argument("--limit", type=int, default=50, help="сколько сообщений напечатать")
    ap.add_argument("--send", action="store_true")
    a = ap.parse_args()

    b = Bazon(LOGIN, PASSWORD, TOKEN)
    st = load_state()

    if a.init:
        r = b.call("getOrders", {"order": "desc", "limit": 1})
        last = r["orders"][0]["id"]
        st["last_order_id"] = last
        save_state(st)
        print(f"init: last_order_id={last}")
        return

    start = a.from_id if a.from_id is not None else st["last_order_id"] + 1
    names = Names(b)
    printed = 0
    last_seen = start - 1

    while printed < a.limit:
        r = b.call("getOrders", {"order": "asc", "limit": 500, "min_id": start})
        rows = r.get("orders", [])
        if not rows:
            break
        for o in rows:
            last_seen = max(last_seen, o["id"])
            if o["paysource_type"] != "cash":
                continue
            text = message(o, names)
            if a.send:
                send(text)
                st["last_order_id"] = max(st["last_order_id"], o["id"])
                st["recent"] = (st.get("recent", []) + [o["id"]])[-50:]
                save_state(st)
            else:
                print(text.replace("<b>", "").replace("</b>", ""))
                print("-" * 40)
            printed += 1
            if printed >= a.limit:
                break
        if len(rows) < 500:
            break
        start = rows[-1]["id"] + 1

    if a.from_id is None:
        st["last_order_id"] = max(st["last_order_id"], last_seen)
        st["recent"] = (st.get("recent", []) + [last_seen])[-50:]
        save_state(st)
    print(f"processed up to id {last_seen}, printed {printed}")


if __name__ == "__main__":
    main()
