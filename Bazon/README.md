# Bazon · отчёты и наличные в Telegram

Два процесса на одном контейнере:

- ежедневный отчёт по сделкам в 19:30 по Сахалину (UTC+11) — топик «Отчёты»;
- зеркало операций с наличными: опрос каждую минуту, сообщение в топик «Касса».

Bazon используется только на чтение: `getOrders`, `getSaleDocuments`, `getReturnDocuments`,
`getContractors`, `getManagers`. Ничего не меняем и не создаём.

## Файлы

```
main.py            планировщик: отчёт по времени и опрос наличных
report.py          дневной отчёт: python3 report.py --date 2026-11-07 [--send]
cash_watch.py      зеркало наличных: python3 cash_watch.py --init | --from-id N --limit 3 [--send]
bazon.py           клиент Bazon External API v1.0
amvera.yaml        конфигурация сборки и запуска
requirements.txt   пустой: только стандартная библиотека
config.example.env пример переменных окружения
api-notes.md       проверенные особенности API
```

## Запуск локально

```bash
export BAZON_LOGIN=c1575_external_app BAZON_PASSWORD=... BAZON_TOKEN_PATH=/tmp/bz_token.json
python3 report.py --date 2026-11-07          # печатает отчёт
python3 cash_watch.py --init                 # запомнить текущую позицию
python3 cash_watch.py --limit 5              # показать 5 операций без отправки
SEND=1 python3 main.py                       # рабочий режим, отправка в Telegram
```

Без `SEND=1` и без `--send` ничего не отправляется: сообщения идут в вывод и в журнал.

## Переменные

| Имя | Назначение |
|---|---|
| `BAZON_LOGIN`, `BAZON_PASSWORD` | доступ к API; пароль задаётся секретом в интерфейсе хостинга |
| `BAZON_TOKEN_PATH` | файл токенов, на постоянном диске: `/data/bz_token.json` |
| `TELEGRAM_BOT_TOKEN` | токен бота, администратора группы |
| `TELEGRAM_CHAT_ID` | группа |
| `TELEGRAM_TOPIC_REPORT`, `TELEGRAM_TOPIC_CASH` | топики отчётов и наличных |
| `SEND` | `0` — только журнал, `1` — отправка |
| `REPORT_AT`, `POLL_SECONDS` | время отчёта и период опроса |
| `STATE_PATH`, `REPORT_STATE`, `LOG_PATH` | файлы состояния и журнал, на постоянном диске |

Пример — в `config.example.env`. Секретов в репозитории нет и быть не должно.

## Хостинг

Сборка и запуск описаны в `amvera.yaml`: Python 3.11, `python3 main.py`, постоянный диск в `/data`.
Серверы хостинга работают в UTC, в коде время считается по фиксированному смещению +11,
поэтому 19:30 по Сахалину наступает независимо от часового пояса контейнера.

Если запускать отчёт отдельной задачей Cron Jobs, расписание задаётся в формате Quartz по UTC:
`0 30 8 * * ?` — это 19:30 по Сахалину.

## Ограничения

- Маржа и расхождение цен недоступны: API не отдаёт строки сделок и себестоимость.
- В `getSaleDocuments` и `getReturnDocuments` нет `created_at`, дата определяется фильтром.
- `getOrders` не фильтрует наличные: отбор по `paysource_type` идёт на стороне клиента.
- Телефон контрагента в сообщениях маскируется до последних четырёх цифр.
