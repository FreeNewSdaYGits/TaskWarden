"""Клиент Bazon External API.

Поддерживает обе формы вызова: REST (GET /v1/<method>) и JSON-RPC (POST / с
пакетом методов). Авторизация: POST /login/user -> AT + RT, обновление через
POST /refresh/user.

Важно: методы версии 1.0 вызываются по пути /external-api/v1/<method>,
хотя в спецификации baseUrl указан без версии. Без /v1 сервер отвечает
{"error": "unknown method"}.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://remixparts.baz-on.ru/external-api"
AUTH = "https://a.baz-on.ru"


class BazonError(Exception):
    def __init__(self, status: int, body: str, url: str = ""):
        self.status = status
        self.body = body
        self.url = url
        super().__init__(f"HTTP {status}: {body[:300]}")


class Bazon:
    def __init__(self, login: str, password: str, token_path: str | None = None):
        self._login = login
        self._password = password
        self.token_path = token_path or "/tmp/bz_token.json"
        self._at: str | None = None
        self._rt: str | None = None
        self._at_deadline = 0.0
        self._load_tokens()

    # ---------- токены ----------

    def _load_tokens(self) -> None:
        try:
            with open(self.token_path, encoding="utf-8") as f:
                d = json.load(f)
            if d.get("login") == self._login:
                self._at = d["at"]
                self._rt = d["rt"]
                self._at_deadline = float(d.get("deadline", 0))
        except (OSError, ValueError, KeyError):
            pass

    def _save_tokens(self) -> None:
        tmp = self.token_path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "login": self._login,
                    "at": self._at,
                    "rt": self._rt,
                    "deadline": self._at_deadline,
                },
                f,
            )
        os.replace(tmp, self.token_path)

    def _post_auth(self, path: str, payload: dict, bearer: str | None = None) -> dict:
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            AUTH + path, data=data, method="POST",
            headers={"Content-Type": "application/json"},
        )
        if bearer:
            req.add_header("Authorization", f"Bearer {bearer}")
        return self._exec(req)

    def login(self) -> None:
        d = self._post_auth("/login/user", {"login": self._login, "password": self._password})
        self._at, self._rt = d["AT"], d["RT"]
        ttl = int(d.get("access_token_ttl") or 0)
        # обновляем заранее, за 5 минут до истечения
        self._at_deadline = time.time() + max(ttl - 300, 60)
        self._save_tokens()

    def refresh(self) -> None:
        if not self._rt:
            self.login()
            return
        try:
            d = self._post_auth("/refresh/user", {"rt": self._rt}, bearer=self._rt)
            self._at, self._rt = d["AT"], d["RT"]
            ttl = int(d.get("access_token_ttl") or 0)
            self._at_deadline = time.time() + max(ttl - 300, 60)
            self._save_tokens()
        except BazonError:
            self.login()

    def _ensure_at(self) -> str:
        if not self._at or time.time() >= self._at_deadline:
            if self._rt:
                self.refresh()
            else:
                self.login()
        return self._at  # type: ignore[return-value]

    # ---------- запросы ----------

    def _exec(self, req: urllib.request.Request, attempt: int = 0) -> dict:
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            if e.code == 429 and attempt < 5:
                wait = float(e.headers.get("Retry-After") or 2 ** attempt)
                time.sleep(min(wait, 60))
                return self._exec(req, attempt + 1)
            if e.code in (401, 403) and attempt == 0:
                self.refresh()
                req.add_header("Authorization", f"Bearer {self._at}")
                return self._exec(req, attempt + 1)
            raise BazonError(e.code, body, req.full_url) from None

    def call(self, method: str, params: dict | None = None, version: str = "v1") -> dict:
        """Одиночный вызов. params -> query string для GET-методов."""
        params = params or {}
        qs = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        url = f"{BASE}/{version}/{method}" + (f"?{qs}" if qs else "")
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {self._ensure_at()}"})
        d = self._exec(req)
        res = d["response"][0]
        if isinstance(res.get("result"), dict) and "error" in res["result"]:
            raise BazonError(200, json.dumps(res["result"], ensure_ascii=False), url)
        return res["result"]

    def call_batch(self, calls: list[dict], version: str = "v1") -> list[dict]:
        """JSON-RPC пакет: [{"method": ..., "params": {...}}, ...]."""
        url = f"{BASE}/{version}/"
        body = json.dumps({"request": calls}).encode()
        req = urllib.request.Request(
            url, data=body, method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._ensure_at()}",
            },
        )
        d = self._exec(req)
        return d["response"]

    def iter_all(self, method: str, page: int = 500, version: str = "v1", **filters):
        """Постраничное чтение: отдаёт элементы и общее число найденных."""
        offset = 0
        total = None
        while True:
            q = {"order": "asc", "limit": page, **filters}
            if offset:
                q["offset"] = offset  # offset=0 сервер считает ошибкой
            res = self.call(method, q, version)
            total = res.get("found", total)
            items = next((v for k, v in res.items() if isinstance(v, list)), [])
            if not items:
                return
            yield from items
            offset += len(items)
            if total is not None and offset >= total:
                return
            time.sleep(0.2)
