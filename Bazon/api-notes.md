# Bazon External API: рабочие заметки

Документ переносится в любой чат по Bazon. Содержит только проверенное на стенде
`remixparts.baz-on.ru` в ноябре 2026 года.

## Авторизация

```
POST https://a.baz-on.ru/login/user
{"login": "...", "password": "..."}
→ {"success": true, "AT": "...", "RT": "...", "access_token_ttl": 355833}
```

- `AT` — access token, живёт около 4 суток (355 833 с в момент проверки).
- `RT` — refresh token, одноразовый: при обновлении выдаётся новая пара, старая
  становится недействительной.
- Обновление: `POST https://a.baz-on.ru/refresh/user`, заголовок `Authorization: Bearer <RT>`,
  тело `{"rt": "..."}`.
- Отзыв: `POST https://a.baz-on.ru/revoke/user` с тем же заголовком и телом.
- Ошибки: 400 — неверный формат и неверный логин/пароль, 403 — неактуальный RT,
  429 — слишком много запросов (`too many requests`).
- При частых логинах срабатывает защита от брутфорса: логиниться раз в сутки, дальше
  жить на refresh.

## Версии и пути

- v0.1: `https://remixparts.baz-on.ru/external-api/<method>`
- v1.0: `https://remixparts.baz-on.ru/external-api/v1/<method>`
- Спецификации: `/external-api/doc.json` (v0.1), `/external-api/v1/doc.json` (v1.0),
  `/external-api/doc-auth.json` (авторизация). Открываются с `Authorization: Bearer <AT>`.

Ловушка: в спецификации v1.0 `baseUrl` указан без версии. Запрос без `/v1` отвечает
`{"error":"unknown method"}`.

## Форматы вызова

REST: `GET /v1/getProducts?order=asc&limit=500`.

JSON-RPC пакетом: `POST /v1/` с телом
`{"request":[{"method":"getProducts","params":{...}}, ...]}`,
ответ `{"response":[{"method":..., "result":...}]}` в том же порядке.

## Особенности, которые ломают наивный клиент

1. `offset=0` — ошибка `Invalid params: offset`. Первая страница запрашивается без `offset`.
2. В пакетном вызове `order` обязателен для методов-списков.
3. Максимум 500 записей на страницу, `offset` до 500 000.
4. При превышении лимита — HTTP 429 и заголовок `Retry-After`.
5. В пакете при достижении мягкого лимита времени завершённые операции сохраняются,
   остальные получают `EXTERNAL_API_TIME_LIMIT_EXCEEDED`.
6. Ошибка метода приходит с HTTP 200 и телом `{"error": "..."}` внутри `result`.

## Методы v1.0

Чтение: `getProducts`, `getProductsWithChars`, `getPartnames`, `getPartsWithChars`,
`getWheelsWithChars`, `getTiresWithChars`, `getDisksWithChars`, `getServicesWithChars`,
`getStorages`, `getPaysources`, `getCarsrcs`, `getManagers`, `getContractors`,
`getIncomeDocuments`, `getSaleDocuments`, `getReturnDocuments`, `getWriteoffDocuments`,
`getMovingDocuments`, `getShipments`, `getOrders`, `getReceipts`.

Запись: `setProducts` (до 100 товаров за вызов), `setPartnames`.

## Незафиксированное поведение

- frontend-api методы работают через тот же вызов, но без документации и без гарантий;
  их нужно держать в одном адаптере и помечать, чтобы потом заменить.
- Схемы ответов могут меняться: сохраняйте сырые ответы, чтобы пересчитать результаты.

## Проверено на стенде, ноябрь 2026

- Фильтры `min_created_at`/`max_created_at` принимают время: `2026-09-30T13-00-00`. Двоеточия в примере спецификации не работают (HTTP 500), нужен формат с дефисами.
- Границы фильтра трактуются как UTC и совпадают с датой в `created_at`. Для Сахалина (UTC+11) сутки — это предыдущий день 13:00 UTC — текущий 13:00 UTC.
- В ответе `getSaleDocuments` и `getReturnDocuments` нет `created_at`. Дату документа можно определить только фильтром, поэтому дневная разбивка — отдельный запрос на каждое окно.
- `getOrders` фильтр по `paysource_type` не поддерживает: параметр игнорируется. Отбор наличных — на стороне клиента.
- `getContractors` с `id` всё равно требует `order`, иначе `Invalid params: order`. Удобнее `{"order":"asc","id":...}`.
- У `getOrders` `manager_id` всегда 0; фактический автор операции — `created_by` (id менеджера).
