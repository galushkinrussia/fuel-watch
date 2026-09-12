# FuelWatch — статус проекта (handoff)

Обновлено: 12 сентября 2026
Проект: монитор топлива и чата водителей на АЗС Волгограда (gdebenz.ru).

---

## 1. Что это теперь

Система из трёх компонентов, работающих 24/7 в облаке (GitHub Actions),
push — через **ntfy.sh** (телефон + браузер). Telegram больше не используется.

| Компонент | Что делает |
|---|---|
| `monitor/fuel_watch.py` | опрашивает АЗС, push при появлении топлива (переход «нет → есть»), пишет историю в `history.jsonl` |
| `monitor/chat_watch.py` | раз в час — LLM-сводка городского чата водителей (вместо спама отдельными сообщениями) |
| `monitor/analyze.py` | анализ времени подвоза топлива по накопленной истории |
| `ANALYSIS.md` | готовый разбор «где и когда стабильно дают бензин» (для публикации) |
| `android/` (Kotlin) | автономное приложение на телефоне (не в проде, см. §7) |

---

## 2. Источники данных (оба открытые, без ключей)

- **АЗС:** `https://gdebenz.ru/api/nearby?lat=…&lon=…&radius_km=…&full=1`
  → `status` (`no`/`queue`/`yes`), `fuels_now` (`"92,95,ДТ"`), `detail`,
  `last_at`, `distance_km`, `lat`/`lon`, `osm_id`.
- **Чат:** `https://api.gdebenz.ru/api/chats/city/<slug>/messages?limit=30&cursor=…`
  → `messages[]` с `id`, `author_name`, `body`, `created_ms`, `reply_to_*`.

Данные — отметки самих водителей, не оферта; проверять на месте.
Запасной адрес сервиса — `gdebenz.org`.

---

## 3. Как устроен запуск в облаке

Встроенное расписание GitHub (`schedule`) троттлится, поэтому **триггер —
внешний бесплатный cron**: [cron-job.org](https://cron-job.org) дёргает API
GitHub по расписанию (`workflow_dispatch`).

- `fuel-monitor` — опрос АЗС каждые 15 мин.
- `chat-monitor` — сводка чата раз в час.

В cron-job.org для каждого workflow создаётся задание **POST** на:
```
https://api.github.com/repos/<ЛОГИН>/<РЕПО>/actions/workflows/<workflow>.yml/dispatches
```
с заголовком `Authorization: Bearer <PAT>` и телом `{"ref":"main"}`.

Раннеры GitHub без памяти → состояние коммитится обратно в репозиторий:
- `state.json` — последний статус АЗС (для детекции «нет → есть»);
- `chat_state.json` — `last_id` последнего сообщения чата;
- `history.jsonl` — накопленная история снимков (для анализа подвоза).

---

## 4. Текущее состояние

- Репозиторий: **https://github.com/galushkinrussia/fuel-watch** (публичный).
- Remote — SSH (`git@github.com:galushkinrussia/fuel-watch.git`), логин `galushkinrussia`.
- **Облако работает**: `history.jsonl` накопил ~18 тыс. записей, последний
  снимок — 2026-09-12T08:15Z; `chat_state.json` обновлён 12.09 08:00.
- Локальный монитор на Linux-машине остановлен.

### Переменные репозитория (Settings → Secrets and variables → Actions)

Variables:
| Переменная | Пример | Назначение |
|---|---|---|
| `LAT` | `48.680` | центр поиска |
| `LON` | `44.470` | центр поиска |
| `RADIUS` | `8` | радиус, км |
| `FUEL` | `92 95` | марки для монитора |
| `TOPIC` | `fuelwatch-...` | ntfy-тема топлива |
| `CHAT_CITY` | `volgograd` | slug города для чата |
| `CHAT_TOPIC` | `fuelwatch-chat-...` | ntfy-тема чата |

Secrets:
| Секрет | Назначение |
|---|---|
| `LLM_API_KEY` | ключ DeepSeek (или др. OpenAI-совместимого) для сводки чата |

> Точные имена тем ntfy в репо НЕ хранятся (они в Variables) — это хорошо,
> т.к. имя темы фактически пароль.

---

## 5. Команды

```bash
# топливо — посмотреть сейчас
python3 monitor/fuel_watch.py list --lat 48.680 --lon 44.470 --radius 8 --fuel 92 95

# топливо — локальный цикл (ПК/сервер)
python3 monitor/fuel_watch.py watch --lat 48.680 --lon 44.470 --radius 8 --fuel 92 95 --topic <тема> --interval 180

# топливо — один опрос (для cron/облака)
python3 monitor/fuel_watch.py once --topic <тема> --state state.json --history history.jsonl

# чат — одна сводка
python3 monitor/chat_watch.py once --city volgograd --topic <тема> --state chat_state.json

# анализ подвоза
python3 monitor/analyze.py --history history.jsonl --min 2 --tz 3 --hourly
python3 monitor/analyze.py --history history.jsonl --station Лукойл
```

Переменные окружения: `FUELWATCH_LAT/LON/RADIUS/FUEL/TOPIC/STATE/HISTORY/INTERVAL`,
`CHATWATCH_CITY/TOPIC/STATE`, `LLM_API_KEY/LLM_BASE_URL/LLM_MODEL`.

Уведомление о топливе содержит название/адрес АЗС, марки, деталь (очередь/
лимит) и **ссылку на Яндекс.Карты** (`Click`-заголовок ntfy — тап открывает карту).

---

## 6. Файлы (что где)

- `.github/workflows/fuel-monitor.yml`, `chat-monitor.yml` — CI.
- `monitor/fuel_watch.py`, `chat_watch.py`, `analyze.py` — скрипты.
- `monitor/config.example.json` — пример локального конфига.
- `state.json`, `chat_state.json`, `history.jsonl` — состояние/история (трекаются в git).
- `ANALYSIS.md` — контентный разбор.
- `android/` — мобильное приложение.
- `README.md` — основная документация (актуальна).

---

## 7. Известные проблемы / заметки

- **Android-приложение** собрано только как debug (`FuelWatch-debug.apk`);
  debug-подпись вызывала ложное срабатывание антивируса Сбербанка. В проде не
  используется — основное развёртывание облачное.
- **`FuelWatch-debug.zip`** (~5 МБ бинарник) лежит в репозитории — по просьбе
  пользователя оставлен, не удалён.
- **Токен Telegram-бота** был ранее засвечен в `ps aux` при локальном запуске.
  Telegram не используется, но токен стоит отозвать в `@BotFather` (/revoke).
- Функция Telegram всё ещё есть в `fuel_watch.py` (`--tg-token`/`--tg-chat`),
  но не задействована.
- Координаты по умолчанию (48.680, 44.470) — приблизительный центр
  Ворошиловского района, не точный адрес Кузнецкая 73.

---

## 8. Бэклог / идеи

- Вшить точные координаты Кузнецкой 73.
- Собрать подписанный release-APK (убрать ложное срабатывание Сбера).
- Добавить цену в текст уведомления.
- Пароль (access token) на темы ntfy, если репо публичный.
- Автопост `ANALYSIS.md` в соцсети/каналы.
