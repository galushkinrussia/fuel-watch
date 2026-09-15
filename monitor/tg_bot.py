#!/usr/bin/env python3
"""Многопользовательский Telegram-бот для настройки монитора топлива.

Доступ — только по приглашению. Настройки пользователя (координаты, радиус,
топливо) хранятся в users.json в приватном репозитории данных.
Монитор (multi_watch.py) обходит всех пользователей и шлёт уведомления.

Команды (пользователь):
  /start                — приветствие / регистрация (для админа — сразу)
  /invite <код>         — активировать приглашение
  /loc                  — местоположение (меню: автоматически/карта/адрес)
  /stats                — персональный анализ появления топлива
  /notify               — вкл/выкл уведомления в боте
  /status               — мои настройки
  /help                 — помощь

Команды (админ):
  /newinvite            — создать код приглашения
  /listusers            — список пользователей

Переменные окружения:
  TELEGRAM_BOT_TOKEN — токен бота (обязательно)
  TELEGRAM_ADMIN     — user_id админа (или список через запятую)
  USERS_FILE         — путь к users.json (по умолчанию ../users.json)
  TGBOT_STATE        — файл состояния offset (по умолчанию tg_bot_state.json)

Зависимости: только стандартная библиотека Python 3.
"""

import argparse
import json
import os
import re
import secrets
import socket
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import data_store as ds

# На части VPS IPv6 объявлен, но нерабочий (Errno 99: Cannot assign requested
# address). Предпочитаем IPv4, если он доступен.
_orig_getaddrinfo = socket.getaddrinfo


def _getaddrinfo_prefer_ipv4(host, port, family=0, type=0, proto=0, flags=0):
    infos = _orig_getaddrinfo(host, port, family, type, proto, flags)
    v4 = [i for i in infos if i[0] == socket.AF_INET]
    return v4 or infos


socket.getaddrinfo = _getaddrinfo_prefer_ipv4

TG_API = "https://api.telegram.org"
BOT_USERNAME = "give_me_fuel_give_me_fire_bot"

DEFAULT_USERS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "users.json")
DEFAULT_STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tg_bot_state.json")

# ключ команды -> (поле в users.json, тип)
SETTINGS = {
    "lat": ("lat", float),
    "lon": ("lon", float),
    "radius": ("radius", float),
    "fuel": ("fuel", list),
}

# слова-отмены при вводе адреса/радиуса
CANCEL_WORDS = {"отмена", "назад", "отменить", "cancel", "стоп"}

HELP_TEXT = (
    "Как пользоваться:\n"
    "  🧭 Местоположение — выбрать способ\n"
    "  ⚙️ Настройки — радиус, топливо, уведомления\n"
    "  📊 Анализ — когда появляется топливо\n"
    "  ❓ Помощь — эта справка\n"
    "\n"
    "Команды:\n"
    "  /status — мои настройки\n"
    "  /notify — вкл/выкл уведомления\n"
    "  /test — проверить доставку уведомлений\n"
    "  /set — задать радиус и топливо вручную\n"
    "\n"
    "Уведомления приходят в этот чат.\n"
    "Данные: отметки водителей на gdebenz.ru."
)

ADMIN_HELP = (
    "\n\nАдмин:\n"
    "  /newinvite — создать код приглашения\n"
    "  /listusers — список пользователей\n"
    "  /broadcast — обновить клавиатуру и меню у всех\n"
    "  /setup — зарегистрировать команды в меню"
)

REGISTER_PROMPT = (
    "Бот доступен по приглашению. Получите код у администратора и "
    "отправьте: /invite <код>"
)


# --- Telegram API -----------------------------------------------------------

def _tg(method, token, params=None, timeout=25, attempts=3):
    url = f"{TG_API}/bot{token}/{method}"
    data = None
    if params:
        data = urllib.parse.urlencode(params).encode("utf-8")
    last = None
    for i in range(attempts):
        try:
            req = urllib.request.Request(url, data=data, method="POST")
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            last = e
            if i + 1 < attempts:
                time.sleep(2 * (i + 1))
    raise last


def get_updates(token, offset=None, timeout=0):
    params = {"timeout": timeout,
              "allowed_updates": json.dumps(["message", "callback_query"])}
    if offset is not None:
        params["offset"] = offset
    return _tg("getUpdates", token, params, timeout=timeout + 10).get("result", [])


def send_message(token, chat_id, text, reply_markup=None, parse_mode=None):
    params = {"chat_id": chat_id, "text": text}
    if reply_markup is not None:
        params["reply_markup"] = json.dumps(reply_markup)
    if parse_mode:
        params["parse_mode"] = parse_mode
    _tg("sendMessage", token, params)


def edit_message(token, chat_id, message_id, text, reply_markup=None,
                 parse_mode=None):
    params = {"chat_id": chat_id, "message_id": message_id, "text": text}
    if reply_markup is not None:
        params["reply_markup"] = json.dumps(reply_markup)
    if parse_mode:
        params["parse_mode"] = parse_mode
    try:
        _tg("editMessageText", token, params)
    except Exception as e:
        print(f"  -> edit FAIL: {e}")


def answer_callback(token, callback_id, text=None):
    params = {"callback_query_id": callback_id}
    if text:
        params["text"] = text
    try:
        _tg("answerCallbackQuery", token, params)
    except Exception as e:
        print(f"  -> answerCallback FAIL: {e}")


MAIN_KEYBOARD = {
    "keyboard": [
        [{"text": "🧭 Местоположение"}, {"text": "⚙️ Настройки"}],
        [{"text": "📊 Анализ"}, {"text": "❓ Помощь"}],
    ],
    "resize_keyboard": True,
}

# одноразовая клавиатура «📤 Отправить местоположение»
REQUEST_LOCATION_KEYBOARD = {
    "keyboard": [[{"text": "📤 Отправить местоположение", "request_location": True}]],
    "resize_keyboard": True,
    "one_time_keyboard": True,
}

# меню способов указать местоположение
LOC_MENU = {
    "inline_keyboard": [
        [{"text": "📍 Отправить текущую геолокацию", "callback_data": "loc:current"}],
        [{"text": "🗺 Указать точку на карте", "callback_data": "loc:map"}],
        [{"text": "✍️ Ввести адрес вручную", "callback_data": "loc:text"}],
        [{"text": "❌ Отмена", "callback_data": "loc:cancel"}],
    ]
}

HISTORY_FILE = "history.jsonl"

# текст кнопки -> команда (для обработки тапов по клавиатуре)
BUTTONS = {
    "🧭 Местоположение": "loc",
    "📊 Анализ": "stats",
    "⚙️ Настройки": "settings",
    "❓ Помощь": "help",
}


def set_commands(token):
    commands = [
        {"command": "start", "description": "Приветствие"},
        {"command": "invite", "description": "Активировать приглашение"},
        {"command": "loc", "description": "Местоположение"},
        {"command": "stats", "description": "Анализ появления топлива"},
        {"command": "status", "description": "Мои настройки"},
        {"command": "notify", "description": "Вкл/выкл уведомления"},
        {"command": "set", "description": "Задать настройки"},
        {"command": "test", "description": "Проверить доставку уведомлений"},
        {"command": "help", "description": "Помощь"},
    ]
    _tg("setMyCommands", token, {"commands": json.dumps(commands)})


# --- Хранилище users.json ---------------------------------------------------

def _repo_path(path):
    """Имя файла внутри репозитория данных (локальный путь может быть абсолютным)."""
    return os.path.basename(str(path).replace("\\", "/"))


def load_users(path, repo=None, token=None):
    """Возвращает (data, sha). Из приватного репо (repo+token) или локально."""
    if repo and token:
        try:
            return ds.load_json(repo, _repo_path(path), token,
                                default={"invites": {}, "users": {}})
        except Exception as e:
            print(f"Не удалось прочитать users.json из {repo}: {e}")
            return {"invites": {}, "users": {}}, None
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f), None
        except (json.JSONDecodeError, OSError):
            pass
    return {"invites": {}, "users": {}}, None


def save_users(path, data, sha, repo=None, token=None, message="обновление пользователей"):
    """Сохраняет users.json. Возвращает новый sha (или None для локального)."""
    if repo and token:
        return ds.save_json(repo, _repo_path(path), token, data, sha, message=message)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return None


def load_state(path):
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_state(path, state):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# --- Команды ----------------------------------------------------------------

def extract_message(update):
    msg = update.get("message") or {}
    text = (msg.get("text") or "").strip()
    chat_id = (msg.get("chat") or {}).get("id")
    user = msg.get("from") or {}
    user_id = user.get("id")
    username = user.get("username") or user.get("first_name") or ""
    loc = msg.get("location")
    location = (loc.get("latitude"), loc.get("longitude")) if loc else None
    return text, chat_id, user_id, username, location


def is_admin(user_id, admins):
    return admins and str(user_id) in admins


def register_user(data, user_id, username):
    uid = str(user_id)
    if uid not in data["users"]:
        data["users"][uid] = {
            "username": username,
            "lat": None,
            "lon": None,
            "radius": 10,
            "fuel": ["92", "95"],
            "enabled": True,
            "registered_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
    return data["users"][uid]


def new_invite(data):
    code = secrets.token_hex(4)
    data["invites"][code] = {
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "used_by": None,
    }
    return code


def redeem_invite(data, user_id, username, code):
    code = code.strip().lower()
    inv = data["invites"].get(code)
    if not inv:
        return False, "Неверный код приглашения."
    if inv.get("used_by") is not None:
        return False, "Этот код уже использован."
    user = register_user(data, user_id, username)
    inv["used_by"] = str(user_id)
    return True, ("Готово! Осталось указать местоположение — нажмите «🧭 Местоположение» ниже.\n\n"
                  + HELP_TEXT)


def parse_set(text):
    """'/set ключ значение [ключ значение ...]' → список пар (ключ, значение)."""
    parts = text.split()
    if len(parts) < 3 or parts[0].lower() not in ("/set", "set"):
        return None
    pairs = []
    i = 1
    while i < len(parts):
        key = parts[i].lower()
        if key not in SETTINGS:
            return None
        vals = []
        j = i + 1
        while j < len(parts) and parts[j].lower() not in SETTINGS:
            vals.append(parts[j])
            j += 1
        if not vals:
            return None
        pairs.append((key, " ".join(vals)))
        i = j
    return pairs


def apply_set(user, key, value):
    field, cast = SETTINGS[key]
    try:
        user[field] = value.split() if cast is list else cast(value)
        return True, f"Готово: {key} = {value}"
    except (ValueError, TypeError):
        return False, f"Неверное значение для {key}: {value}"


def format_user(user):
    """Экран настроек с инлайн-кнопками. Возвращает (text, inline_keyboard)."""
    lat, lon = user.get("lat"), user.get("lon")
    loc = f"{lat:.4f}, {lon:.4f}" if lat is not None and lon is not None else "не указано"
    radius = user.get("radius", 10)
    fuel = user.get("fuel") or []
    enabled = user.get("enabled", True)

    text = (f"⚙️ Настройки\n\n"
            f"🧭 Местоположение: {loc}\n"
            f"📏 Радиус: {radius} км\n"
            f"⛽ Топливо: {', '.join(fuel) if fuel else '—'}\n"
            f"🔔 Уведомления: {'включены' if enabled else 'выключены'}")
    if lat is None or lon is None:
        text += "\n\n⚠️ Нажмите «🧭 Местоположение», чтобы указать его."
    elif not enabled:
        text += "\n\nУведомления выключены."

    def mark(cond):
        return "✓ " if cond else ""

    radius_row = [{"text": f"{mark(float(radius) == r)}{r} км", "callback_data": f"r:{r}"}
                  for r in (5, 10, 15)]
    radius_row.append({"text": "✏️ свой", "callback_data": "r:custom"})
    fuel_row = [{"text": f"{mark(f in fuel)}{f}", "callback_data": f"f:{f}"}
                for f in ("92", "95", "100", "ДТ")]
    toggle = {"text": ("🔕 выключить" if enabled else "🔔 включить"),
              "callback_data": "t"}
    keyboard = {"inline_keyboard": [radius_row, fuel_row, [toggle]]}
    return text, None, keyboard


def apply_callback(user, cb_data):
    """Применяет нажатие инлайн-кнопки. Возвращает текст всплывающей подсказки."""
    if cb_data.startswith("r:"):
        user["radius"] = float(cb_data[2:])
        return f"Радиус: {user['radius']} км"
    if cb_data.startswith("f:"):
        f = cb_data[2:]
        fuels = list(user.get("fuel") or [])
        if f in fuels:
            fuels.remove(f)
        else:
            fuels.append(f)
        user["fuel"] = fuels
        return f"Топливо: {', '.join(fuels) if fuels else '—'}"
    if cb_data == "t":
        user["enabled"] = not user.get("enabled", True)
        return "Уведомления " + ("включены" if user["enabled"] else "выключены")
    return ""


def user_stats(data_repo, data_token, user_id):
    """Персональный анализ появления топлива по истории пользователя."""
    if not (data_repo and data_token):
        return "Анализ доступен только в облачном режиме."
    try:
        text, _ = ds.load_text(data_repo, HISTORY_FILE, data_token, default="")
    except Exception as e:
        return f"Не удалось получить статистику: {e}"
    if not text.strip():
        return ("📊 Пока копим данные для анализа.\n\n"
                "История копится автоматически (каждые ~15 минут). "
                "Чтобы появились закономерности — в какое время обычно "
                "появляется топливо — нужно 2–3 дня. Это нормально, "
                "ничего делать не нужно.\n\n"
                "Загляните сюда позже.")
    import analyze as az
    mine = [r for r in az.parse_records(text) if str(r.get("user")) == str(user_id)]
    if not mine:
        return ("📊 По вашим АЗС данных пока мало.\n\n"
                "История копится автоматически, обычно нужно 2–3 дня. "
                "Загляните позже — тогда покажу, когда обычно появляется топливо "
                "рядом с вами.")
    return az.build_digest(mine, az.analyze(mine))


def geocode(query):
    """Nominatim (OpenStreetMap): адрес/город → (lat, lon, display_name)."""
    url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(
        {"q": query, "format": "json", "limit": 1, "accept-language": "ru"})
    req = urllib.request.Request(url, headers={"User-Agent": "FuelWatchBot/1.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read().decode("utf-8"))
    if not data:
        return None, None, None
    item = data[0]
    return float(item["lat"]), float(item["lon"]), item.get("display_name", query)


def location_ok(user, place=None):
    """Единое подтверждение: местоположение указано, бот следит."""
    radius = user.get("radius", 10)
    fuel = user.get("fuel") or []
    fuel_str = f" ({', '.join(fuel)})" if fuel else ""
    lines = []
    if place:
        if len(place) > 90:
            place = place[:87] + "…"
        lines.append(f"🧭 Местоположение: {place}")
    lines.append(f"Готово! Слежу за топливом в радиусе {radius:g} км{fuel_str}.")
    if user.get("enabled", True):
        lines.append("Уведомление придёт, когда появится топливо.")
        lines.append("Настроить — «⚙️ Настройки»")
    else:
        lines.append("Уведомления выключены — включите в «⚙️ Настройки».")
    return "\n".join(lines)


def handle_location(data, user_id, location):
    """Записывает геолокацию пользователя в lat/lon."""
    uid = str(user_id)
    user = data["users"].get(uid)
    if not user:
        return REGISTER_PROMPT
    lat, lon = location
    user["lat"] = lat
    user["lon"] = lon
    user.pop("await_address", None)
    user.pop("await_radius", None)
    data["users"][uid] = user
    return location_ok(user)


def handle_command(text, chat_id, user_id, username, admins, token, data, users_path,
                   data_repo=None, data_token=None):
    t = text.strip()
    # тап по кнопке клавиатуры = команда
    if t in BUTTONS:
        t = "/" + BUTTONS[t]
    uid = str(user_id)
    admin = is_admin(user_id, admins)
    user = data["users"].get(uid)
    registered = user is not None

    # --- служебные команды (доступны всегда) ---
    if t.startswith("/start"):
        if admin and not registered:
            user = register_user(data, user_id, username)
            return ("Вы админ. Укажите местоположение кнопкой «🧭 Местоположение».\n\n" + HELP_TEXT)
        if registered:
            user = register_user(data, user_id, username)  # дозаполнит тему, если её нет
            return "С возвращением!\n\n" + HELP_TEXT
        return "Привет!\n" + REGISTER_PROMPT

    if t.startswith("/invite") or t.startswith("invite"):
        if registered:
            return "Вы уже зарегистрированы."
        parts = t.split()
        if len(parts) < 2:
            return "Формат: /invite <код>"
        ok, msg = redeem_invite(data, user_id, username, parts[1])
        return msg

    if t.startswith("/help") or t == "help":
        if not registered:
            return "Привет!\n" + REGISTER_PROMPT
        return HELP_TEXT + (ADMIN_HELP if admin else "")

    # --- админские команды ---
    if t.startswith("/newinvite"):
        if not admin:
            return "Недостаточно прав."
        code = new_invite(data)
        head = (f"Приглашаю в бота: @{BOT_USERNAME} "
                f"(https://t.me/{BOT_USERNAME})\n"
                f"Он следит за появлением топлива на АЗС рядом с вами "
                f"и присылает уведомления.\n\n")
        return (head + f"Подключитесь командой:\n<code>/invite {code}</code>",
                "HTML")

    if t.startswith("/listusers"):
        if not admin:
            return "Недостаточно прав."
        if not data["users"]:
            return "Пользователей нет."
        lines = ["Пользователи:"]
        for u, us in data["users"].items():
            act = "✅" if (us.get("lat") and us.get("lon")) else "—"
            lines.append(f"  {act} {u} ({us.get('username')})")
        return "\n".join(lines)

    if t.startswith("/broadcast"):
        if not admin:
            return "Недостаточно прав."
        try:
            set_commands(token)  # обновить меню команд (глобально)
        except Exception as e:
            print(f"  -> set_commands FAIL: {e}")
        msg = t[len("/broadcast"):].strip() or "🔄 Обновил меню — кнопки внизу актуальны."
        n = 0
        for uid2 in list(data["users"].keys()):
            try:
                send_message(token, uid2, msg, reply_markup=MAIN_KEYBOARD)
                n += 1
                time.sleep(0.05)
            except Exception as e:
                print(f"  -> broadcast FAIL {uid2}: {e}")
        return f"Разослано {n} пользователям, меню команд обновлено."

    # --- пользовательские команды (нужна регистрация) ---
    if not registered:
        return REGISTER_PROMPT

    # ожидаем адрес/город текстом
    if user.get("await_address"):
        user["await_address"] = False
        if t.lower() in CANCEL_WORDS:
            data["users"][uid] = user
            return "Хорошо, отменил. Указать местоположение можно кнопкой «🧭 Местоположение»."
        if t and not t.startswith("/") and t not in BUTTONS:
            try:
                lat, lon, name = geocode(t)
            except Exception as e:
                data["users"][uid] = user
                return f"Не удалось определить адрес: {e}"
            if lat is None:
                data["users"][uid] = user
                return ("Не нашёл такой адрес. Попробуйте иначе, например: "
                        "«Волгоград, центр» или «ул. Ленина, 1».")
            user["lat"], user["lon"] = lat, lon
            data["users"][uid] = user
            return location_ok(user, place=name)
        data["users"][uid] = user

    # ожидаем радиус числом
    if user.get("await_radius"):
        user["await_radius"] = False
        if t.lower() in CANCEL_WORDS:
            data["users"][uid] = user
            return "Хорошо, отменил. Радиус можно задать в «⚙️ Настройки»."
        if t and not t.startswith("/") and t not in BUTTONS:
            m = re.search(r"\d+(?:[.,]\d+)?", t)
            if not m:
                data["users"][uid] = user
                return "Не понял. Пришлите число, например: 12"
            val = float(m.group(0).replace(",", "."))
            if not (1 <= val <= 100):
                data["users"][uid] = user
                return "Радиус должен быть от 1 до 100 км."
            user["radius"] = val
            data["users"][uid] = user
            return f"Радиус: {val:g} км"
        data["users"][uid] = user

    if (t.startswith("/status") or t.startswith("/settings")
            or t in ("status", "settings")):
        return format_user(user)

    if t.startswith("/stats") or t == "stats":
        if user.get("lat") is None or user.get("lon") is None:
            return ("📊 Сначала укажите местоположение — нажмите «🧭 Местоположение».\n"
                    "После этого я начну собирать данные и смогу показать, "
                    "в какое время появляется топливо рядом.")
        return user_stats(data_repo, data_token, uid)

    if t.startswith("/test") or t == "test":
        try:
            send_message(token, uid, "🔔 Тестовое уведомление. Если вы это видите — "
                                     "доставка работает.")
            return "Тест отправлен."
        except Exception as e:
            return f"Ошибка отправки: {e}"

    if t.startswith("/loc") or t == "loc":
        return ("Как указать местоположение? Выберите способ:", None, LOC_MENU)

    if t.startswith("/notify") or t == "notify":
        enabled = not user.get("enabled", True)
        user["enabled"] = enabled
        data["users"][uid] = user
        return ("Уведомления: " + ("включены ✅" if enabled else "выключены ⏸"))

    if t.startswith("/setup"):
        if not admin:
            return "Недостаточно прав."
        set_commands(token)
        return "Команды зарегистрированы в меню."

    pairs = parse_set(t)
    if pairs:
        msgs = []
        for key, value in pairs:
            ok, msg = apply_set(user, key, value)
            msgs.append(msg)
        data["users"][uid] = user
        return "\n".join(msgs)

    return "Не понял команду. Напишите /help."


# --- Режимы запуска ---------------------------------------------------------

def process_update(update, token, admins, data, users_path,
                   data_repo=None, data_token=None):
    """Обрабатывает одно обновление (текст, геолокацию или нажатие кнопки)."""
    t_start = time.time()
    # --- нажатие инлайн-кнопки ---
    cb = update.get("callback_query")
    if cb:
        cid = cb.get("id")
        cb_data = cb.get("data") or ""
        from_id = (cb.get("from") or {}).get("id")
        msg = cb.get("message") or {}
        chat_id = (msg.get("chat") or {}).get("id")
        message_id = msg.get("message_id")
        uid = str(from_id)
        user = data["users"].get(uid)
        print(f"[{time.strftime('%H:%M:%S')}] callback от {from_id}: {cb_data!r}")
        if not user:
            answer_callback(token, cid, "Сначала зарегистрируйтесь")
            return
        if cb_data.startswith("loc:"):
            if cb_data == "loc:current":
                answer_callback(token, cid, "Отправьте местоположение")
                send_message(token, chat_id, "Нажмите кнопку ниже, чтобы "
                             "отправить местоположение.",
                             reply_markup=REQUEST_LOCATION_KEYBOARD)
            elif cb_data == "loc:map":
                answer_callback(token, cid, "")
                send_message(token, chat_id,
                             "Выбор на карте: нажмите скрепку 📎 → «Геопозиция» → "
                             "«Выбрать на карте», поставьте точку и отправьте.")
            elif cb_data == "loc:text":
                user["await_address"] = True
                data["users"][uid] = user
                answer_callback(token, cid, "")
                send_message(token, chat_id,
                             "Пришлите город или адрес текстом, например: "
                             "«Волгоград, центр». Чтобы отменить — напишите «отмена».")
            elif cb_data == "loc:cancel":
                user.pop("await_address", None)
                user.pop("await_radius", None)
                data["users"][uid] = user
                answer_callback(token, cid, "Отменено")
                send_message(token, chat_id, "Хорошо, отменил.",
                             reply_markup=MAIN_KEYBOARD)
            return
        if cb_data == "r:custom":
            user["await_radius"] = True
            data["users"][uid] = user
            answer_callback(token, cid, "")
            send_message(token, chat_id, "Пришлите радиус в километрах, например: 12. "
                         "Чтобы отменить — напишите «отмена».")
            return
        toast = apply_callback(user, cb_data)
        data["users"][uid] = user
        answer_callback(token, cid, toast)
        text, _, keyboard = format_user(user)
        edit_message(token, chat_id, message_id, text, reply_markup=keyboard)
        return

    text, chat_id, user_id, username, location = extract_message(update)
    if not chat_id:
        return
    reply_markup = None
    parse_mode = None
    inline = None
    if location:
        print(f"[{time.strftime('%H:%M:%S')}] от {user_id}: геолокация")
        reply = handle_location(data, user_id, location)
    else:
        print(f"[{time.strftime('%H:%M:%S')}] от {user_id}: {text!r}")
        reply = handle_command(text, chat_id, user_id, username, admins, token,
                               data, users_path, data_repo, data_token)
        if isinstance(reply, tuple):
            if len(reply) == 3:
                reply, parse_mode, inline = reply
            else:
                reply, parse_mode = reply
    # зарегистрированным показываем главную клавиатуру (если нет инлайн-кнопок)
    if inline is not None:
        reply_markup = inline
    elif str(user_id) in data.get("users", {}):
        reply_markup = MAIN_KEYBOARD
    t_send = time.time()
    try:
        send_message(token, chat_id, reply, reply_markup=reply_markup,
                     parse_mode=parse_mode)
        print(f"  -> ответ отправлен: обработка {t_send - t_start:.2f}s, "
              f"отправка {time.time() - t_send:.2f}s")
    except Exception as e:
        print(f"  -> send FAIL: {e}")


def _load_offset(state_file, repo=None, token=None):
    """Читает offset: из приватного репо (repo+token) или локально. → (offset, sha)."""
    if repo and token:
        data, sha = ds.load_json(repo, _repo_path(state_file), token, default={})
        return data.get("offset"), sha
    return load_state(state_file).get("offset"), None


def _save_offset(state_file, offset, sha=None, repo=None, token=None):
    payload = {"offset": offset, "updated": time.strftime("%Y-%m-%d %H:%M:%S")}
    if repo and token:
        path = _repo_path(state_file)
        try:
            return ds.save_json(repo, path, token, payload, sha,
                                message="состояние бота")
        except Exception as e:
            if "409" in str(e):
                # Файл изменился извне (другой процесс/устройство) — перечитываем
                # sha и пробуем ещё раз.
                try:
                    _, sha = ds.load_json(repo, path, token, default={})
                    return ds.save_json(repo, path, token, payload, sha,
                                        message="состояние бота")
                except Exception as e2:
                    e = e2
            print(f"  -> save offset FAIL: {e}")
            return sha
    save_state(state_file, payload)
    return None


def process(token, admins, data, users_sha, users_path, state_file, offset,
            state_sha=None, repo=None, data_token=None):
    updates = get_updates(token, offset=offset, timeout=0)
    for u in updates:
        process_update(u, token, admins, data, users_path, repo, data_token)
    if updates:
        try:
            users_sha = save_users(users_path, data, users_sha,
                                   repo=repo, token=data_token)
        except Exception as e:
            print(f"  -> save users FAIL: {e}")
        new_offset = max(u["update_id"] for u in updates) + 1
        _save_offset(state_file, new_offset, state_sha, repo, data_token)
    return len(updates), users_sha


def _storage_note(args):
    if args.data_repo and args.data_token:
        return f"приватный репо {args.data_repo}"
    if args.data_repo or args.data_token:
        return "ЛОКАЛЬНЫЙ файл (задан только один из DATA_REPO/DATA_PAT — проверь оба!)"
    return "ЛОКАЛЬНЫЙ файл (DATA_REPO/DATA_PAT не заданы)"


def cmd_once(args):
    print(f"[{time.strftime('%H:%M:%S')}] хранилище данных: {_storage_note(args)}")
    offset, state_sha = _load_offset(args.state, args.data_repo, args.data_token)
    data, users_sha = load_users(args.users, repo=args.data_repo, token=args.data_token)
    admins = parse_admins(args.admin)
    n, _ = process(args.token, admins, data, users_sha, args.users, args.state,
                   offset, state_sha, repo=args.data_repo, data_token=args.data_token)
    print(f"обработано обновлений: {n}")
    return 0


def cmd_loop(args):
    offset, state_sha = _load_offset(args.state, args.data_repo, args.data_token)
    admins = parse_admins(args.admin)
    print(f"хранилище данных: {_storage_note(args)}")
    print("Бот запущен (loop). Ctrl+C для выхода.")
    while True:
        data, users_sha = load_users(args.users, repo=args.data_repo, token=args.data_token)
        try:
            t_poll = time.time()
            updates = get_updates(args.token, offset=offset, timeout=0)
            if updates:
                print(f"  <- getUpdates: {time.time() - t_poll:.1f}s, "
                      f"апдейтов: {len(updates)}")
            for u in updates:
                process_update(u, args.token, admins, data, args.users,
                               args.data_repo, args.data_token)
            if updates:
                users_sha = save_users(args.users, data, users_sha,
                                       repo=args.data_repo, token=args.data_token)
                offset = max(u["update_id"] for u in updates) + 1
                state_sha = _save_offset(args.state, offset, state_sha,
                                         args.data_repo, args.data_token)
            else:
                time.sleep(1)
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"ошибка цикла: {e}")
            time.sleep(5)
    return 0


def main():
    p = argparse.ArgumentParser(description="Многопользовательский Telegram-бот")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_common(sp):
        sp.add_argument("--token", help="токен бота (или TELEGRAM_BOT_TOKEN)")
        sp.add_argument("--admin", help="user_id админа(ов) через запятую (или TELEGRAM_ADMIN)")
        sp.add_argument("--users", default=DEFAULT_USERS, help="файл пользователей")
        sp.add_argument("--state", default=DEFAULT_STATE, help="файл состояния")
        sp.add_argument("--data-repo", help="owner/repo приватного репо с users.json")
        sp.add_argument("--data-token", help="PAT с правами contents")

    op = sub.add_parser("once", help="один проход (для cron/облака)")
    add_common(op)
    op.set_defaults(func=cmd_once)

    lp = sub.add_parser("loop", help="постоянный опрос (VPS/ПК)")
    add_common(lp)
    lp.set_defaults(func=cmd_loop)

    args = p.parse_args()
    _env_override(args)
    if not args.token:
        print("Ошибка: не задан токен бота (--token или TELEGRAM_BOT_TOKEN)",
              file=sys.stderr)
        return 2
    return args.func(args) or 0


def _env_override(args):
    def apply(name, env):
        v = os.environ.get(env)
        if v is not None and v != "":
            setattr(args, name, v)
    apply("token", "TELEGRAM_BOT_TOKEN")
    apply("admin", "TELEGRAM_ADMIN")
    apply("users", "USERS_FILE")
    apply("state", "TGBOT_STATE")
    apply("data_repo", "DATA_REPO")
    apply("data_token", "DATA_PAT")


def parse_admins(raw):
    """'123,456' -> {'123','456'}"""
    if not raw:
        return set()
    return {s.strip() for s in str(raw).split(",") if s.strip()}


if __name__ == "__main__":
    sys.exit(main())
