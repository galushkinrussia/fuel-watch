# FuelWatch — итоговый статус работы (handoff)

Дата: 6 сентября 2026
Проект: монитор появления бензина на АЗС Волгограда (Ворошиловский район).

---

## 1. Что это

Сервис следит за живыми отметками водителей (краудсорсинговый источник
`gdebenz.ru`) и присылает уведомление, когда на АЗС появляется бензин
(переход «нет → есть/очередь»).

Уведомления приходят через **ntfy.sh** (есть Android-приложение + веб).
Telegram больше не используется.

---

## 2. Источник данных

Открытый JSON-эндпоинт (без ключа/токена):

```
https://gdebenz.ru/api/nearby?lat=48.680&lon=44.470&radius_km=8&full=1
```

Возвращает `{"stations": [...], "updated": "..."}`. У каждой АЗС:
- `osm_id`, `brand`, `addr`, `distance_km`
- `status`: `no` (нет) / `queue` (есть, очередь) / `yes` (есть)
- `fuels_now`: список марок, напр. `"92,95,ДТ"`
- `detail`: текст, напр. `"92, 95 · Очередь 100+ машин · Лимит 40 л"`
- `last_at`: время последней отметки

Данные — субъективные отметки водителей, не оферта; проверять на месте.
Запасной адрес сервиса: `gdebenz.org`.

---

## 3. Что сделано

### 3.1 Python-монитор (`monitor/fuel_watch.py`)
Только стандартная библиотека Python 3, кроссплатформенный (Linux/Win/macOS).

Команды:
```bash
# посмотреть текущее наличие (без уведомлений)
python3 monitor/fuel_watch.py list --lat 48.680 --lon 44.470 --radius 8 --fuel 92 95

# следить в цикле (локально, для ПК/сервера)
python3 monitor/fuel_watch.py watch --lat 48.680 --lon 44.470 --radius 8 --fuel 92 95 --topic volga-fuel-mike --interval 180

# один опрос с детекцией перехода (для cron/облака)
python3 monitor/fuel_watch.py once --topic volga-fuel-mike --state state.json
```

Параметры можно задавать переменными окружения:
`FUELWATCH_LAT`, `FUELWATCH_LON`, `FUELWATCH_RADIUS`, `FUELWATCH_FUEL`,
`FUELWATCH_TOPIC`, `FUELWATCH_STATE`, `FUELWATCH_INTERVAL`.

Логика детекции: состояние хранится в JSON-файле (`state.json`). Уведомление
шлётся только при переходе станции из `no` в `yes`/`queue`. Первый запуск
фиксирует состояние молча (без спама).

### 3.2 Android-приложение (`android/`)
Kotlin, foreground service опрашивает тот же API прямо с телефона,
показывает локальные уведомления. Без Firebase/бэкенда.

- Собрано: `FuelWatch-debug.apk` (debug-подпись).
- ВНИМАНИЕ: debug-подпись вызывала ложное срабатывание антивируса Сбербанка.
  Пользователь удалил приложение с телефона. При желании — пересобрать
  release-версию с нормальной подписью.

### 3.3 Облако (GitHub Actions) — основной способ
Workflow: `.github/workflows/fuel-monitor.yml`. Опрос каждые 15 минут,
состояние сохраняется в `state.json` (коммитится обратно в репо).

---

## 4. Текущее состояние развёртывания

- Репозиторий: **https://github.com/galushkinrussia/fuel-watch** (публичный).
- Код запущен (коммит `init`), remote через SSH.
- SSH-ключ пользователя: `~/.ssh/id_ed25519`, GitHub-логин `galushkinrussia`.

### Что ещё НЕ сделано (следующие шаги)
1. Добавить в репо **Variables** (Settings → Secrets and variables →
   Actions → Variables):
   - `LAT` = `48.680`
   - `LON` = `44.470`
   - `RADIUS` = `8`
   - `FUEL` = `92 95`
   - `TOPIC` = `volga-fuel-mike`
2. Включить **Actions** (вкладка Actions → enable workflows).
3. Проверить вручную: **Actions → fuel-monitor → Run workflow**.
4. На телефоне подписаться в приложении **ntfy** на тему `volga-fuel-mike`.
   Браузер: `https://ntfy.sh/volga-fuel-mike`.

---

## 5. Ограничение бесплатного тарифа GitHub Actions

- Публичный репо → без лимита минут (можно `*/15` и чаще).
- Приватный репо → ~2000 мин/мес; тогда в `fuel-monitor.yml` сменить cron
  на `*/30 * * * *`.

Если репо публичный и не хочется светить тему ntfy — включить пароль
(access token) на `https://ntfy.sh/<тема>` → Settings.

---

## 6. Полезные команды (памятка)

```bash
# локально (Linux/macOS), фон:
nohup python3 monitor/fuel_watch.py watch --lat 48.680 --lon 44.470 --radius 8 --fuel 92 95 --topic volga-fuel-mike --interval 180 > monitor.log 2>&1 &

# Windows (PowerShell):
python monitor\fuel_watch.py watch --lat 48.680 --lon 44.470 --radius 8 --fuel 92 95 --topic volga-fuel-mike --interval 180

# остановить локальный монитор:
pkill -f fuel_watch.py

# git (SSH):
git remote set-url origin git@github.com:galushkinrussia/fuel-watch.git
git push -u origin main
```

---

## 7. Важные заметки / безопасность

- **Токен Telegram-бота был засвечен** в списке процессов (`ps aux`) при
  локальном запуске с токеном в аргументах. Рекомендуется отозвать его в
  `@BotFather` (/revoke) — даже если Telegram больше не используется.
- Локальный монитор на Linux-машине мог остаться запущенным
  (`pkill -f fuel_watch.py`, если нужно остановить).
- В репозиторий случайно попал бинарник `FuelWatch-debug.zip` (~5 МБ).
  Пользователь попросил НЕ удалять — оставлен как есть.
- Координаты по умолчанию (48.680, 44.470) — приблизительный центр
  Ворошиловского района, НЕ точный адрес Кузнецкая 73.

---

## 8. Что можно улучшить (бэклог)

- Вшить точные координаты Кузнецкой 73 как дефолт.
- Собрать подписанный release-APK (убрать ложное срабатывание Сбера).
- Добавить расстояние до АЗС и цену в текст уведомления.
- Настроить пароль на тему ntfy.
