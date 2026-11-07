"""Ежедневный отчёт по сделкам. Сахалин, UTC+11, срез на 19:30.

Пример: python3 report.py --date 2026-10-06
"""
from __future__ import annotations
import argparse, json, os, time, urllib.parse, urllib.request, datetime as dt
from collections import Counter, defaultdict
from bazon import Bazon

LOGIN = os.environ.get("BAZON_LOGIN", "c1575_external_app")
PASSWORD = os.environ.get("BAZON_PASSWORD", "")
TOKEN = os.environ.get("BAZON_TOKEN_PATH", "/tmp/bz_token.json")
TZ = dt.timezone(dt.timedelta(hours=11))
CUTOFF = dt.time(19, 30)
UTC = dt.timezone.utc


def utc_str(d: dt.datetime) -> str:
    return d.astimezone(UTC).strftime("%Y-%m-%dT%H-%M-%S")


def window(day: dt.date, start: dt.time, end: dt.time) -> tuple[str, str]:
    lo = dt.datetime.combine(day, start, TZ)
    hi = dt.datetime.combine(day, end, TZ)
    return utc_str(lo), utc_str(hi)


def fetch_range(b, method, key, lo: str, hi: str):
    out, total, off = [], None, 0
    while True:
        q = {"order": "asc", "limit": 500, "min_created_at": lo, "max_created_at": hi}
        if off:
            q["offset"] = off
        r = b.call(method, q)
        total = r.get("found", total)
        chunk = r.get(key, [])
        out += chunk
        if not chunk or (total is not None and len(out) >= total):
            return out
        off += len(chunk)
        time.sleep(0.1)


def local_date(s: str) -> dt.date:
    return dt.datetime.fromisoformat(s).astimezone(TZ).date()


def fmt(x: float) -> str:
    return f"{x:,.0f}".replace(",", " ")


def build(day: dt.date) -> dict:
    b = Bazon(LOGIN, PASSWORD, TOKEN)
    lo, hi = window(day, dt.time(0, 0), CUTOFF)
    sales = fetch_range(b, "getSaleDocuments", "sale_documents", lo, hi)
    returns = fetch_range(b, "getReturnDocuments", "return_documents", lo, hi)
    orders = fetch_range(b, "getOrders", "orders", lo, hi)

    # история платежей для сверки: 60 дней, чтобы видеть предоплаты
    hist_lo = utc_str(dt.datetime.combine(day - dt.timedelta(days=60), dt.time(0, 0), TZ))
    hist = fetch_range(b, "getOrders", "orders", hist_lo, hi)

    issued = [x for x in sales if x["status"] == "issued"]
    ret = [x for x in returns if x["status"] == "complete"]
    inc = [x for x in orders if x["type"] == "income"]
    out = [x for x in orders if x["type"] == "outcome"]
    cash_in = [x for x in inc if x["paysource_type"] == "cash"]
    cash_out = [x for x in out if x["paysource_type"] == "cash"]

    net = defaultdict(float)
    for o in hist:
        if o["document_type"] == "sale":
            net[o["document_id"]] += o["sum"]
    unpaid = [x for x in issued if x["sum"] > 0 and abs(net.get(x["id"], 0)) < 1]

    by_mgr = Counter()
    for x in issued:
        by_mgr[x["manager_name"] or "без менеджера"] += x["sum"]

    # хвост прошлого дня после 19:30
    prev = day - dt.timedelta(days=1)
    tlo, thi = window(prev, CUTOFF, dt.time(23, 59, 59))
    tail_sales = [x for x in fetch_range(b, "getSaleDocuments", "sale_documents", tlo, thi)
                  if x["status"] == "issued"]
    tail_orders = fetch_range(b, "getOrders", "orders", tlo, thi)

    # накопительно с начала месяца
    mlo = utc_str(dt.datetime.combine(day.replace(day=1), dt.time(0, 0), TZ))
    mtd = [x for x in fetch_range(b, "getSaleDocuments", "sale_documents", mlo, hi)
           if x["status"] == "issued"]

    return {
        "date": str(day),
        "issued_cnt": len(issued), "issued_sum": sum(x["sum"] for x in issued),
        "avg_check": (sum(x["sum"] for x in issued) / len(issued)) if issued else 0,
        "return_cnt": len(ret), "return_sum": sum(x["sum"] for x in ret),
        "income": sum(x["sum"] for x in inc), "outcome": abs(sum(x["sum"] for x in out)),
        "cash_in": sum(x["sum"] for x in cash_in), "cash_in_cnt": len(cash_in),
        "cash_out": abs(sum(x["sum"] for x in cash_out)), "cash_out_cnt": len(cash_out),
        "unpaid_cnt": len(unpaid), "unpaid_sum": sum(x["sum"] for x in unpaid),
        "top_manager": by_mgr.most_common(1)[0] if by_mgr else ("—", 0),
        "tail_sales_cnt": len(tail_sales),
        "tail_sales_sum": sum(x["sum"] for x in tail_sales),
        "tail_income": sum(x["sum"] for x in tail_orders if x["type"] == "income"),
        "mtd_cnt": len(mtd), "mtd_sum": sum(x["sum"] for x in mtd),
    }


def as_telegram(r: dict) -> str:
    d = dt.date.fromisoformat(r["date"]).strftime("%d.%m.%Y")
    ret_share = (r["return_sum"] / r["issued_sum"] * 100) if r["issued_sum"] else 0
    if not r["issued_cnt"] and not r["income"] and not r["outcome"] and not r["return_cnt"]:
        return f"<b>Итоги за {d}</b>\nОпераций нет."
    lines = [
        f"<b>Итоги за {d} на 19:30 (Сах.)</b>",
        f"Продажи: <b>{r['issued_cnt']}</b> шт. на <b>{fmt(r['issued_sum'])} ₽</b>",
        f"Средний чек: {fmt(r['avg_check'])} ₽",
        f"Возвраты: {r['return_cnt']} на {fmt(r['return_sum'])} ₽ ({ret_share:.1f}%)",
        "",
        f"Поступления: {fmt(r['income'])} ₽",
        f"Расходы: {fmt(r['outcome'])} ₽",
        f"Наличные (Касса, офис): приход {fmt(r['cash_in'])} ₽ ({r['cash_in_cnt']} оп.), "
        f"расход {fmt(r['cash_out'])} ₽ ({r['cash_out_cnt']} оп.)",
    ]
    if r["unpaid_cnt"]:
        lines.append(f"⚠️ Выдано без оплаты: {r['unpaid_cnt']} на {fmt(r['unpaid_sum'])} ₽")
    name, s = r["top_manager"]
    if s:
        lines.append(f"Лучший менеджер: {name} — {fmt(s)} ₽")
    lines.append(f"С начала месяца: {r['mtd_cnt']} шт. на {fmt(r['mtd_sum'])} ₽")
    if r["tail_sales_cnt"] or r["tail_income"]:
        lines.append(
            f"После 19:30 вчера: {r['tail_sales_cnt']} сделок на {fmt(r['tail_sales_sum'])} ₽, "
            f"поступления {fmt(r['tail_income'])} ₽"
        )
    return "\n".join(lines)


def send(text: str) -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat = os.environ.get("TELEGRAM_CHAT_ID") or os.environ["TELEGRAM_CHAT_REPORT"]
    payload = {
        "chat_id": chat, "text": text, "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }
    thread = os.environ.get("TELEGRAM_TOPIC_REPORT")
    if thread:
        payload["message_thread_id"] = thread
    data = urllib.parse.urlencode(payload).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=data)
    with urllib.request.urlopen(req, timeout=30) as r:
        r.read()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=dt.datetime.now(TZ).date().isoformat())
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--send", action="store_true", help="отправить в TELEGRAM_CHAT_REPORT")
    a = ap.parse_args()
    res = build(dt.date.fromisoformat(a.date))
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        text = as_telegram(res)
        if a.send:
            send(text)
            print("sent")
        else:
            print(text)
