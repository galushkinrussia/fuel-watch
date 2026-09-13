# FuelWatch — статус проекта (handoff)

Обновлено: 13 сентября 2026
Проект: **многопользовательский** монитор появления топлива и городского чата
водителей (данные `gdebenz.ru`) с Telegram-ботом для настройки.

---

## 1. Что это теперь

Сервис на несколько пользователей, работающий 24/7 в облаке (GitHub Actions).
Пользователи получают уведомления **в Telegram**; служба **ntfy** задействована
только **админом**. Доступ к боту — **по приглашению**. Публичный репозиторий
содержит **только код**; все данные — в отдельном **приватном** репозитории.

| Компонент | Что делает |
|---|---|
| `monitor/multi_watch.py` | обходит всех активных пользователей, опрашивает их координаты, шлёт уведомление в Telegram (админу — дополнительно в ntfy); копит историю |
| `monitor/tg_bot.py` | Telegram-бот: регистрация по приглашению, настройка места/радиуса/топлива, персональный анализ |
| `monitor/data_store.py` | чтение/запись данных в приватный репозиторий через GitHub Contents API |
| `monitor/chat_watch.py` | раз в час — тезисная LLM-сводка городского чата водителей |
| `monitor/analyze.py` | анализ времени появления топлива по накопленной истории (в т.ч. персональный) |
| `monitor/fuel_watch.py` | однопользовательский монитор (локально / для одного человека) |
| `monitor/migrate_data.py` | разовая миграция старых данных в приватный репо |
| `android/` (Kotlin) | автономное приложение на телефоне (в проде не используется, см. §9) |

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

| Workflow | Что делает | Интервал |
|---|---|---|
| `fuel-monitor` | `multi_watch.py once` — опрос всех пользователей | 15 мин |
| `chat-monitor` | `chat_watch.py once` — сводка чата | час |
| `tg-bot-config` | `tg_bot.py once` — обработка команд бота | ~1 мин |
| `migrate-data` | разовый перенос старых данных | однократно |

В cron-job.org для каждого workflow создаётся задание **POST** на:
```
https://api.github.com/repos/<ЛОГИН>/<РЕПО>/actions/workflows/<workflow>.yml/dispatches
```
с заголовком `Authorization: Bearer <PAT>` и телом `{"ref":"main"}`.

---

## 4. Данные в приватном репозитории

Публичный репо — только код. Все данные лежат в отдельном **приватном**
репозитории (`DATA_REPO`) и читаются/пишутся через GitHub Contents API
(`monitor/data_store.py`) по `DATA_PAT`:

- `users.json` — пользователи и приглашения;
- `users_state.json` — состояние детекции топлива по пользователям;
- `sends.jsonl` — журнал отправок;
- `tg_bot_state.json` — offset long polling бота;
- `chat_state.json` — `last_id` последнего сообщения чата;
- `history.jsonl` — накопленная история снимков (для анализа появления).

Локально (без `DATA_REPO`/`DATA_PAT`) скрипты работают с обычными файлами —
удобно для разработки. Файлы данных закрыты в `.gitignore`.

---

## 5. Переменные и секреты (Settings → Secrets and variables → Actions)

**Variables:**

| Переменная | Назначение |
|---|---|
| `CHAT_CITY` | slug города для сводки чата (напр. `volgograd`) |
| `CHAT_TOPIC` | общая ntfy-тема для сводки чата |
| `TELEGRAM_ADMIN` | `user_id` админа бота (выдаёт приглашения) |
| `DATA_REPO` | `owner/repo` приватного репозитория с данными |

**Secrets:**

| Секрет | Назначение |
|---|---|
| `LLM_API_KEY` | ключ DeepSeek (или др. OpenAI-совместимого) для сводки чата |
| `TELEGRAM_BOT_TOKEN` | токен Telegram-бота |
| `DATA_PAT` | PAT с правами **contents** на приватный репо (`DATA_REPO`) |

> Координаты, радиус и топливо **пользователей** задаются через бота
> и хранятся в `users.json` в приватном репо — не в GitHub Variables.

---

## 6. Бот: регистрация и команды

Бот: **@give_me_fuel_give_me_fire_bot**. Доступ по приглашению.

Пользователь:
```
/start              — приветствие / регистрация
/invite <код>       — активировать приглашение
/loc                — задать место (автоматически / карта / адрес)
/stats              — персональный анализ появления топлива
/notify             — вкл/выкл уведомления
/status             — мои настройки
/test               — проверить доставку уведомлений
/set radius 8 fuel 92 95 — задать вручную
/help               — помощь
```
Админ (`TELEGRAM_ADMIN`):
```
/newinvite          — создать код приглашения
/listusers          — список пользователей
/topic              — своя ntfy-тема (только для админа)
```

Пользователь становится «активным» (получает уведомления), когда задал
геолокацию. Кнопки: 🧭 Местоположение, 📊 Анализ, ⚙️ Настройки, ❓ Помощь.

> **ntfy — только для админа.** Обычные пользователи получают уведомления в
> Telegram. Админ смотрит свою тему командой `/topic` и подписывается в ntfy.

---

## 7. Команды (CLI)

```bash
# многопользовательский опрос
python3 monitor/multi_watch.py once --users users.json --state users_state.json
python3 monitor/multi_watch.py loop --users users.json --interval 180

# бот
python3 monitor/tg_bot.py once --token <токен> --admin <user_id>
python3 monitor/tg_bot.py loop --token <токен> --admin <user_id>

# чат — одна сводка
python3 monitor/chat_watch.py once --city volgograd --topic <тема> --state chat_state.json

# анализ появления топлива
python3 monitor/analyze.py --min 2 --tz 3 --hourly
python3 monitor/analyze.py --data-repo owner/repo --data-token <PAT> --md analysis/ANALYSIS-FULL.md
```

Переменные окружения: `MULTI_USERS/STATE/INTERVAL`, `TELEGRAM_BOT_TOKEN`,
`TELEGRAM_ADMIN`, `USERS_FILE`, `TGBOT_STATE`, `DATA_REPO`, `DATA_PAT`,
`CHATWATCH_CITY/TOPIC/STATE`, `LLM_API_KEY/LLM_BASE_URL/LLM_MODEL`,
`FUELWATCH_*` (для однопользовательского).

Уведомление о топливе содержит АЗС, марки, деталь (очередь/лимит) и **ссылку на
Яндекс.Карты** (у админа в ntfy ещё и `Click`-заголовок — тап открывает карту).

---

## 8. Файлы (что где)

- `.github/workflows/` — `fuel-monitor.yml`, `chat-monitor.yml`,
  `tg-bot-config.yml`, `migrate-data.yml`.
- `monitor/` — `multi_watch.py`, `tg_bot.py`, `data_store.py`, `chat_watch.py`,
  `analyze.py`, `fuel_watch.py`, `migrate_data.py`, `config.example.json`.
- `analysis/ANALYSIS.md` — краткий разбор для публикации;
  `analysis/ANALYSIS-FULL.md` — полный отчёт по АЗС.
- `android/` — мобильное приложение.
- `README.md` — основная документация (актуальна).

---

## 9. Известные проблемы / заметки

- **Android-приложение** собрано только как debug (`FuelWatch-debug.apk`);
  debug-подпись вызывала ложное срабатывание антивируса Сбербанка. В проде не
  используется.
- **`FuelWatch-debug.zip`** (~5 МБ) лежит в репозитории — по просьбе оставлен.
- **Токен старого Telegram-бота** был засвечен в `ps aux`; если он совпадает с
  текущим `TELEGRAM_BOT_TOKEN` — отозвать в `@BotFather` (`/revoke`).
- Раньше данные (`users_state.json`, `sends.jsonl`, `chat_state.json`,
  `history.jsonl`) коммитились в публичный репо — перенесены в приватный
  (`migrate-data`), в публичном удалены.
- Уведомления чата могут «молчать», если в чате нет новых сообщений (это норма,
  а не сбой). Сообщения, пришедшие сразу после прогона, ждут следующего часа.

---

## 10. Бэклог / идеи

- Собрать подписанный release-APK (убрать ложное срабатывание Сбера).
- Добавить цену в текст уведомления.
- Пароль (access token) на темы ntfy.
- Автопост `analysis/ANALYSIS.md` в соцсети/каналы.
