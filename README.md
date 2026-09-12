# FuelWatch — монитор топлива на АЗС

Следит за живыми отметками водителей (данные `gdebenz.ru`, открытый JSON-эндпоинт)
и шлёт push на телефон через **ntfy.sh**. Три режима:

| Компонент | Что делает |
|---|---|
| `monitor/multi_watch.py` | многопользовательский монитор АЗС: для каждого пользователя опрашивает его координаты и шлёт push в его ntfy-тему |
| `monitor/tg_bot.py` | Telegram-бот (по приглашению): регистрация и настройка пользователей |
| `monitor/fuel_watch.py` | однопользовательский монитор (локально/для одного человека) |
| `monitor/chat_watch.py` | релей городского чата водителей — разовая **LLM-сводка** вместо спама |
| `monitor/analyze.py` | анализ накопленной истории — время подвоза топлива по АЗС |
| `android/` (Kotlin) | автономное приложение, опрашивает API прямо на телефоне (без бэкенда) |

Работает 24/7 в облаке (GitHub Actions) — push в ntfy на телефон. Каждый
пользователь получает уведомления на **свою** ntfy-тему.

---

## Настройка ntfy на телефоне

Push приходит через **ntfy.sh** — бесплатную службу push-уведомлений. Каждый
пользователь получает уведомления на **свою** тему (задаётся в боте командой
`/set topic <тема>`), а сводка чата — на общую тему `CHAT_TOPIC`.

1. Установите приложение **ntfy** (Google Play или F-Droid).
2. В приложении нажмите «+» → введите имя своей темы → «Подписаться».
3. Готово — уведомления будут приходить на телефон.

Ту же тему можно открыть в браузере: `https://ntfy.sh/<тема>`.

> Имя темы — фактически пароль (оно случайное и неугадываемое). Не публикуйте
> его в открытом виде и не коммитьте в репозиторий.

---

## 1. Монитор топлива

### Многопользовательский (`multi_watch.py`)

Читает `users.json` и для каждого активного пользователя опрашивает его
координаты, шлёт push в его ntfy-тему. Состояние — в `users_state.json`.

```bash
python3 monitor/multi_watch.py once --users users.json --state users_state.json
python3 monitor/multi_watch.py loop --users users.json --state users_state.json --interval 180
```

Переменные окружения: `MULTI_USERS`, `MULTI_STATE`, `MULTI_INTERVAL`.

### Однопользовательский (`fuel_watch.py`)

Для локального запуска одного человека (без `users.json`).

```bash
# посмотреть текущее наличие
python3 monitor/fuel_watch.py list --lat <LAT> --lon <LON> --radius 8 --fuel 92 95

# следить в цикле локально (ПК/сервер)
python3 monitor/fuel_watch.py watch \
  --lat <LAT> --lon <LON> --radius 8 \
  --fuel 92 95 --topic <тема> --interval 180

# один опрос с детекцией перехода (для cron/облака)
python3 monitor/fuel_watch.py once --topic <тема> --state state.json \
  --history history.jsonl
```

Координаты обязательны — задавайте их флагами, через `config.json` или
переменными окружения (`FUELWATCH_LAT`, `FUELWATCH_LON`, `FUELWATCH_RADIUS`,
`FUELWATCH_FUEL`, `FUELWATCH_TOPIC`, `FUELWATCH_STATE`, `FUELWATCH_HISTORY`).
Жёстких дефолтов в коде нет.

Первый опрос фиксирует базу молча (без спама), дальше уведомления только при
переходе «нет → есть/очередь». В push добавляется **ссылка на Яндекс.Карты**
(по `lat`/`lon` АЗС), тап по уведомлению открывает карту.

---

## 2. Чат-релей с LLM-сводкой (`chat_watch.py`)

Собирает новые сообщения городского чата водителей и раз в час шлёт **одну
смысловую сводку** (через LLM), а не отдельный push на каждое сообщение.

```bash
python3 monitor/chat_watch.py once --city volgograd --topic <тема> --state chat_state.json
```

- LLM по умолчанию — **DeepSeek** (`deepseek-chat`, OpenAI-совместимый API).
  Ключ передаётся через `LLM_API_KEY`, модель/адрес — через `LLM_MODEL` и
  `LLM_BASE_URL` (необязательно).
- Если LLM недоступен — автоматический откат на сырой список сообщений.
- Промпт требует не превращать вопросы в утверждения и помечать
  `[факт]` / `[вопрос]` / `[слух]`.

---

## 3. Анализ времени подвоза (`analyze.py`)

Читает накопленный `history.jsonl` и находит переходы «нет → есть» (= подвоз)
по каждой АЗС.

```bash
python3 monitor/analyze.py --history history.jsonl            # все АЗС
python3 monitor/analyze.py --history history.jsonl --min 2    # с 2+ подвозами
python3 monitor/analyze.py --history history.jsonl --station Лукойл
python3 monitor/analyze.py --history history.jsonl --tz 3 --hourly   # время по Москве
```

> Для надёжного «графика подвоза» нужно несколько дней данных (коллектор
> копит их автоматически при каждом опросе).

---

## 4. Облако (GitHub Actions)

Монитор крутится на GitHub бесплатно, push — через ntfy.sh.

### Как устроен запуск

Workflow запускаются **только вручную** (`workflow_dispatch`): встроенное
расписание GitHub (`schedule`) троттлится и работает нестабильно, поэтому
триггером служит **внешний cron** — бесплатный [cron-job.org](https://cron-job.org),
который дёргает API GitHub по расписанию.

- `fuel-monitor` — многопользовательский опрос АЗС, раз в 15 мин.
- `chat-monitor` — сводка чата, раз в час.
- `tg-bot-config` — обработка команд Telegram-бота, раз в ~1 мин.

Для каждого в cron-job.org создаётся задание POST на:
`https://api.github.com/repos/<ЛОГИН>/<РЕПО>/actions/workflows/<workflow>.yml/dispatches`
с заголовком `Authorization: Bearer <PAT>` и телом `{"ref":"main"}`.

### Переменные и секреты

**Settings → Secrets and variables → Actions:**

Переменные (`Variables`):

| Переменная | Пример | Назначение |
|---|---|---|
| `CHAT_CITY` | `volgograd` | slug города для сводки чата |
| `CHAT_TOPIC` | `fuelwatch-chat-...` | ntfy-тема чата |
| `TELEGRAM_ADMIN` | `123456` | ваш `user_id` (админ бота, выдаёт приглашения) |

Секреты (`Secrets`):

| Секрет | Назначение |
|---|---|
| `LLM_API_KEY` | ключ DeepSeek (или др. OpenAI-совместимого) для сводки чата |
| `TELEGRAM_BOT_TOKEN` | токен Telegram-бота |

> Координаты, радиус, топливо и ntfy-темы **пользователей** хранятся не в
> GitHub Variables, а в `users.json` — каждый пользователь задаёт их через бота.

### Состояние в репозитории

Раннеры GitHub Actions не имеют памяти, поэтому состояние между запусками
коммитится обратно в репозиторий:

- `users.json` — настройки пользователей и приглашения (меняет бот);
- `users_state.json` — состояние детекции по каждому пользователю;
- `tg_bot_state.json` — offset long polling бота;
- `chat_state.json` — `last_id` последнего сообщения чата;
- `history.jsonl` — накопленная история снимков (для анализа подвоза).

---

## 5. Бот Telegram для настройки (`tg_bot.py`)

Многопользовательский бот с доступом **по приглашению**. Каждый пользователь
задаёт свои координаты, радиус, топливо и **свою** ntfy-тему через бота —
без захода в GitHub. Боты Telegram доступны частным лицам без статуса.

Команды пользователя:
```
/start              — приветствие / регистрация
/invite <код>       — активировать приглашение
/status             — мои настройки
/set lat 48.700     — задать настройку
/set lon 44.500
/set radius 8
/set fuel 92 95
/set topic <тема>   — моя ntfy-тема
/help               — помощь
```

Команды админа (указан в `TELEGRAM_ADMIN`):
```
/newinvite          — создать код приглашения
/listusers          — список пользователей
```

Пользователь становится «активным» (получает уведомления), когда задал
`lat`, `lon` и `topic`. Монитор `fuel-monitor` на каждой итерации обходит
всех активных пользователей и шлёт каждому push в его тему.

### Подготовка (разово)

1. Создайте бота у `@BotFather` (`/newbot`) и возьмите токен.
2. В GitHub добавьте секрет `TELEGRAM_BOT_TOKEN` (токен) и переменную
   `TELEGRAM_ADMIN` (ваш `user_id` — узнайте у `@userinfobot`).
3. Отправьте боту `/setup` — зарегистрировать команды в меню.
4. Сгенерируйте приглашение командой `/newinvite` и передайте код человеку —
   он активирует его командой `/invite <код>`.

### Запуск

- **Облако** (как остальное): создайте в cron-job.org задание POST на
  `.../actions/workflows/tg-bot-config.yml/dispatches` (интервал ~1 мин).
- **Локально/VPS** (постоянный процесс):
  ```bash
  python3 monitor/tg_bot.py loop --token <токен> --admin <user_id>
  ```

---

## 6. Android-приложение (Kotlin)

Автономное: фоновый сервис (`MonitorService`) опрашивает API прямо с телефона,
показывает локальные уведомления. Бэкенд и Firebase не нужны.

Сборка: открыть `android/` в Android Studio (AGP 8.3.2 / Kotlin 1.9.23 /
compileSdk 34) → Build → Build APK(s).

---

## Источник данных

- АЗС: `https://gdebenz.ru/api/nearby?lat=…&lon=…&radius_km=…&full=1`
  → `status` (`no`/`queue`/`yes`), `fuels_now` (`"92,95,ДТ"`), `detail`,
  `last_at`, `distance_km`, `lat`/`lon`.
- Чат: `https://api.gdebenz.ru/api/chats/city/<slug>/messages?limit=30`
  → `messages[]` с `id`, `author_name`, `body`, `created_ms`, `reply_to_*`.

Данные — отметки самих водителей, актуальность не гарантируется; проверяйте на
месте. Запасной адрес — `gdebenz.org`.

---

## Важно

- Краудсорсинговые данные, а не оферта заправок.
- Не злоупотребляйте частотой опроса (15 мин для АЗС, час для чата — разумно).
- API-ключи и токены — только в Secrets, не коммитить.
