# FuelWatch — монитор топлива на АЗС

Следит за живыми отметками водителей (данные `gdebenz.ru`, открытый JSON-эндпоинт)
и шлёт push на телефон через **ntfy.sh**. Три режима:

| Компонент | Что делает |
|---|---|
| `monitor/fuel_watch.py` | опрашивает АЗС, шлёт push при появлении топлива (переход «нет → есть») |
| `monitor/chat_watch.py` | релей городского чата водителей — разовая **LLM-сводка** вместо спама |
| `monitor/analyze.py` | анализ накопленной истории — время подвоза топлива по АЗС |
| `android/` (Kotlin) | автономное приложение, опрашивает API прямо на телефоне (без бэкенда) |

Работает 24/7 в облаке (GitHub Actions) — push в ntfy на телефон.

---

## Настройка ntfy на телефоне

Push приходит через **ntfy.sh** — бесплатную службу push-уведомлений. Имя темы
(случайное) задаётся в настройках репозитория: переменные `TOPIC` (топливо) и
`CHAT_TOPIC` (чат).

1. Установите приложение **ntfy** (Google Play или F-Droid).
2. В приложении нажмите «+» → введите имя темы из `TOPIC` (или `CHAT_TOPIC`) →
   «Подписаться».
3. Готово — уведомления будут приходить на телефон.

Ту же тему можно открыть в браузере: `https://ntfy.sh/<тема>`.

> Имя темы — фактически пароль (оно случайное и неугадываемое). Не публикуйте
> его в открытом виде и не коммитьте в репозиторий.

---

## 1. Python-монитор топлива (`fuel_watch.py`)

Только стандартная библиотека Python 3, без сторонних зависимостей.

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
переменными окружения (см. ниже). Жёстких дефолтов в коде нет.

Первый опрос фиксирует базу молча (без спама), дальше уведомления только при
переходе «нет → есть/очередь». В push добавляется **ссылка на Яндекс.Карты**
(по `lat`/`lon` АЗС), тап по уведомлению открывает карту.

Параметры можно задавать переменными окружения:
`FUELWATCH_LAT`, `FUELWATCH_LON`, `FUELWATCH_RADIUS`, `FUELWATCH_FUEL`,
`FUELWATCH_TOPIC`, `FUELWATCH_STATE`, `FUELWATCH_HISTORY`.

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

- `fuel-monitor` — опрос АЗС, раз в 15 мин.
- `chat-monitor` — сводка чата, раз в час.

Для каждого в cron-job.org создаётся задание POST на:
`https://api.github.com/repos/<ЛОГИН>/<РЕПО>/actions/workflows/<workflow>.yml/dispatches`
с заголовком `Authorization: Bearer <PAT>` и телом `{"ref":"main"}`.

### Переменные и секреты

**Settings → Secrets and variables → Actions:**

Переменные (`Variables`):

| Переменная | Пример | Назначение |
|---|---|---|
| `LAT` | `<широта>` | центр поиска |
| `LON` | `<долгота>` | центр поиска |
| `RADIUS` | `8` | радиус, км |
| `FUEL` | `92 95` | марки для монитора |
| `TOPIC` | `fuelwatch-...` | ntfy-тема топлива |
| `CHAT_CITY` | `volgograd` | slug города для чата |
| `CHAT_TOPIC` | `fuelwatch-chat-...` | ntfy-тема чата |
| `GH_REPO` | `galushkinrussia/fuel-watch` | репозиторий для бота настройки |
| `TELEGRAM_ADMIN` | `123456` | ваш `user_id` (кто может менять настройки) |

Секреты (`Secrets`):

| Секрет | Назначение |
|---|---|
| `LLM_API_KEY` | ключ DeepSeek (или др. OpenAI-совместимого) для сводки чата |
| `TELEGRAM_BOT_TOKEN` | токен Telegram-бота |
| `GH_PAT` | GitHub PAT с правами на Variables репозитория |

### Состояние в репозитории

Раннеры GitHub Actions не имеют памяти, поэтому состояние между запусками
коммитится обратно в репозиторий:

- `state.json` — последний статус АЗС (для детекции «нет → есть»);
- `chat_state.json` — `last_id` последнего сообщения чата;
- `history.jsonl` — накопленная история снимков (для анализа подвоза).

---

## 5. Бот Telegram для настройки (`tg_bot.py`)

Меняет настройки монитора (координаты, радиус, топливо, город) прямо из чата
с ботом в Telegram — не заходя в GitHub. Боты Telegram доступны частным лицам
без какого-либо статуса.

```
/help            — помощь
/status          — текущие значения переменных
/set lat 48.700  — изменить настройку
/set lon 44.500
/set radius 8
/set fuel 92 95
/set city volgograd
```

Ключи соответствуют переменным GitHub: `lat`→`LAT`, `lon`→`LON`,
`radius`→`RADIUS`, `fuel`→`FUEL`, `city`→`CHAT_CITY`. После изменения
следующая итерация `fuel-monitor`/`chat-monitor` подхватит значение.

### Подготовка (разово)

1. Создайте бота у `@BotFather` (`/newbot`) и возьмите токен.
2. В GitHub добавьте секрет `TELEGRAM_BOT_TOKEN` (токен) и `GH_PAT`
   (PAT с правами на **Variables**), переменные `GH_REPO` и `TELEGRAM_ADMIN`
   (ваш `user_id` — узнайте у `@userinfobot`).
3. Отправьте боту `/setup` — зарегистрировать команды в меню.

### Запуск

- **Облако** (как остальное): создайте в cron-job.org задание POST на
  `.../actions/workflows/tg-bot-config.yml/dispatches` (интервал ~1 мин).
- **Локально/VPS** (постоянный процесс):
  ```bash
  python3 monitor/tg_bot.py loop --token <токен> --gh-token <PAT> --admin <user_id>
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
