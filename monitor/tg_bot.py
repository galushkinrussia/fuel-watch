#!/usr/bin/env python3
"""Многопользовательский Telegram-бот для настройки монитора топлива.

Доступ — только по приглашению. Каждый пользователь хранит СВОИ настройки
(координаты, радиус, топливо, ntfy-тему) в users.json, который коммитится
обратно в репозиторий workflow'ом. Монитор (multi_watch.py) обходит всех
пользователей и шлёт каждому push в его тему.

Команды (пользователь):
  /start                — приветствие / регистрация (для админа — сразу)
  /invite <код>         — активировать приглашение
  /status               — мои настройки
  /set lat 48.700       — задать настройку
  /set lon 44.500
  /set radius 8
  /set fuel 92 95
  /set topic <тема>     — моя ntfy-тема (как пароль)
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
import secrets
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import data_store as ds

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
    "topic": ("topic", str),
}

HELP_TEXT = (
    "Мои настройки:\n"
    "  /loc — отправить геолокацию\n"
    "  /set radius 8 — радиус поиска, км\n"
    "  /set fuel 92 95 — марки топлива\n"
    "  /status — показать (в т.ч. вашу тему ntfy)\n"
    "  /help — помощь\n\n"
    "Координаты можно ввести и вручную: /set lat …, /set lon …\n"
    "Тема ntfy создаётся автоматически — смотрите её в /status.\n"
    "Уведомления приходят в этот чат и в приложение ntfy."
)

ADMIN_HELP = (
    "\n\nАдмин:\n"
    "  /newinvite — создать код приглашения\n"
    "  /listusers — список пользователей"
)

REGISTER_PROMPT = (
    "Бот доступен по приглашению. Получите код у администратора и "
    "отправьте: /invite <код>"
)


# --- Telegram API -----------------------------------------------------------

def _tg(method, token, params=None, timeout=25):
    url = f"{TG_API}/bot{token}/{method}"
    data = None
    if params:
        data = urllib.parse.urlencode(params).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def get_updates(token, offset=None, timeout=0):
    params = {"timeout": timeout, "allowed_updates": json.dumps(["message"])}
    if offset is not None:
        params["offset"] = offset
    return _tg("getUpdates", token, params, timeout=timeout + 20).get("result", [])


def send_message(token, chat_id, text, reply_markup=None, parse_mode=None):
    params = {"chat_id": chat_id, "text": text}
    if reply_markup is not None:
        params["reply_markup"] = json.dumps(reply_markup)
    if parse_mode:
        params["parse_mode"] = parse_mode
    _tg("sendMessage", token, params)


LOCATION_KEYBOARD = {
    "keyboard": [[{"text": "📍 Отправить геолокацию", "request_location": True}]],
    "resize_keyboard": True,
    "one_time_keyboard": True,
}
REMOVE_KEYBOARD = {"remove_keyboard": True}


def set_commands(token):
    commands = [
        {"command": "start", "description": "Приветствие"},
        {"command": "invite", "description": "Активировать приглашение"},
        {"command": "loc", "description": "Отправить геолокацию"},
        {"command": "status", "description": "Мои настройки"},
        {"command": "set", "description": "Задать настройку"},
        {"command": "help", "description": "Помощь"},
    ]
    _tg("setMyCommands", token, {"commands": json.dumps(commands)})


# --- Хранилище users.json ---------------------------------------------------

def load_users(path, repo=None, token=None):
    """Возвращает (data, sha). Из приватного репо (repo+token) или локально."""
    if repo and token:
        try:
            return ds.load_json(repo, path, token,
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
        return ds.save_json(repo, path, token, data, sha, message=message)
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


def generate_topic():
    return "fuelwatch-" + secrets.token_hex(6)


def register_user(data, user_id, username):
    uid = str(user_id)
    if uid not in data["users"]:
        data["users"][uid] = {
            "username": username,
            "lat": None,
            "lon": None,
            "radius": 8,
            "fuel": ["92", "95"],
            "topic": generate_topic(),
            "registered_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
    elif not data["users"][uid].get("topic"):
        data["users"][uid]["topic"] = generate_topic()
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
    return True, (f"Вы зарегистрированы! Ваша тема ntfy: {user['topic']}\n"
                  f"Подпишитесь на неё в приложении ntfy.\n"
                  f"Затем отправьте геолокацию: /loc\n" + HELP_TEXT)


def parse_set(text):
    parts = text.split()
    if len(parts) >= 3 and parts[0].lower() in ("/set", "set"):
        key = parts[1].lower()
        value = " ".join(parts[2:])
        if key in SETTINGS:
            return key, value
    return None, None


def apply_set(user, key, value):
    field, cast = SETTINGS[key]
    try:
        user[field] = value.split() if cast is list else cast(value)
        return True, f"OK: {key} = {value}"
    except (ValueError, TypeError):
        return False, f"Неверное значение для {key}: {value}"


def format_user(user):
    lines = ["Мои настройки:"]
    lines.append(f"  lat = {user.get('lat')}")
    lines.append(f"  lon = {user.get('lon')}")
    lines.append(f"  radius = {user.get('radius')}")
    lines.append(f"  fuel = {' '.join(user.get('fuel') or [])}")
    lines.append(f"  topic = {user.get('topic')}")
    active = user.get("lat") is not None and user.get("lon") is not None and user.get("topic")
    lines.append("")
    lines.append("Статус: " + ("✅ активен (уведомления идут)" if active else "⚠️ задайте lat, lon и topic"))
    return "\n".join(lines)


def handle_location(data, user_id, location):
    """Записывает геолокацию пользователя в lat/lon."""
    uid = str(user_id)
    user = data["users"].get(uid)
    if not user:
        return REGISTER_PROMPT, None
    lat, lon = location
    user["lat"] = lat
    user["lon"] = lon
    data["users"][uid] = user
    return (f"OK: координаты заданы\nlat = {lat}\nlon = {lon}", REMOVE_KEYBOARD)


def handle_loc_command(data, user_id):
    """Отправляет клавиатуру с кнопкой геолокации."""
    uid = str(user_id)
    if uid not in data["users"]:
        return REGISTER_PROMPT, None
    return "Отправьте вашу геолокацию, нажав кнопку ниже 👇", LOCATION_KEYBOARD


def handle_command(text, chat_id, user_id, username, admins, token, data, users_path):
    t = text.strip()
    uid = str(user_id)
    admin = is_admin(user_id, admins)
    user = data["users"].get(uid)
    registered = user is not None

    # --- служебные команды (доступны всегда) ---
    if t.startswith("/start"):
        parts = t.split()
        if admin and not registered:
            user = register_user(data, user_id, username)
            return (f"Вы админ и зарегистрированы автоматически.\n"
                    f"Ваша тема ntfy: {user['topic']}\n" + HELP_TEXT)
        if registered:
            user = register_user(data, user_id, username)  # дозаполнит тему, если её нет
            return "С возвращением!\n" + HELP_TEXT
        if len(parts) >= 2:  # /start <code>
            ok, msg = redeem_invite(data, user_id, username, parts[1])
            return msg
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
        return HELP_TEXT + (ADMIN_HELP if admin else "")

    # --- админские команды ---
    if t.startswith("/newinvite"):
        if not admin:
            return "Недостаточно прав."
        code = new_invite(data)
        head = (f"Привет!\nАдрес бота: "
                f"<a href=\"https://t.me/{BOT_USERNAME}\">@{BOT_USERNAME}</a>\n\n")
        return (head + f"Активируй приглашение командой:\n<code>/invite {code}</code>",
                "HTML")

    if t.startswith("/listusers"):
        if not admin:
            return "Недостаточно прав."
        if not data["users"]:
            return "Пользователей нет."
        lines = ["Пользователи:"]
        for u, us in data["users"].items():
            act = "✅" if (us.get("lat") and us.get("lon") and us.get("topic")) else "—"
            lines.append(f"  {act} {u} ({us.get('username')})")
        return "\n".join(lines)

    # --- пользовательские команды (нужна регистрация) ---
    if not registered:
        return REGISTER_PROMPT

    if t.startswith("/status") or t == "status":
        return format_user(user)

    if t.startswith("/setup"):
        set_commands(token)
        return "Команды зарегистрированы в меню."

    key, value = parse_set(t)
    if key:
        ok, msg = apply_set(user, key, value)
        if ok:
            data["users"][uid] = user
        return msg

    return "Не понял команду. Напишите /help."


# --- Режимы запуска ---------------------------------------------------------

def process_update(update, token, admins, data, users_path):
    """Обрабатывает одно обновление (текст или геолокацию) и отправляет ответ."""
    text, chat_id, user_id, username, location = extract_message(update)
    if not chat_id:
        return
    reply_markup = None
    parse_mode = None
    if location:
        print(f"[{time.strftime('%H:%M:%S')}] от {user_id}: геолокация")
        reply, reply_markup = handle_location(data, user_id, location)
    elif text.startswith("/loc") or text == "loc":
        print(f"[{time.strftime('%H:%M:%S')}] от {user_id}: {text!r}")
        reply, reply_markup = handle_loc_command(data, user_id)
    else:
        print(f"[{time.strftime('%H:%M:%S')}] от {user_id}: {text!r}")
        reply = handle_command(text, chat_id, user_id, username, admins, token,
                               data, users_path)
        if isinstance(reply, tuple):
            reply, parse_mode = reply
    try:
        send_message(token, chat_id, reply, reply_markup=reply_markup,
                     parse_mode=parse_mode)
    except Exception as e:
        print(f"  -> send FAIL: {e}")


def process(token, admins, data, users_sha, users_path, state_file, offset,
            repo=None, data_token=None):
    updates = get_updates(token, offset=offset, timeout=0)
    for u in updates:
        process_update(u, token, admins, data, users_path)
    if updates:
        try:
            users_sha = save_users(users_path, data, users_sha,
                                   repo=repo, token=data_token)
        except Exception as e:
            print(f"  -> save users FAIL: {e}")
    new_offset = max((u["update_id"] for u in updates), default=offset) + 1 \
        if updates else offset
    save_state(state_file, {"offset": new_offset,
                            "updated": time.strftime("%Y-%m-%d %H:%M:%S")})
    return len(updates), users_sha


def _storage_note(args):
    if args.data_repo and args.data_token:
        return f"приватный репо {args.data_repo}"
    if args.data_repo or args.data_token:
        return "ЛОКАЛЬНЫЙ файл (задан только один из DATA_REPO/DATA_PAT — проверь оба!)"
    return "ЛОКАЛЬНЫЙ файл (DATA_REPO/DATA_PAT не заданы)"


def cmd_once(args):
    print(f"[{time.strftime('%H:%M:%S')}] хранилище users.json: {_storage_note(args)}")
    offset = load_state(args.state).get("offset")
    data, users_sha = load_users(args.users, repo=args.data_repo, token=args.data_token)
    admins = parse_admins(args.admin)
    n, _ = process(args.token, admins, data, users_sha, args.users, args.state,
                   offset, repo=args.data_repo, data_token=args.data_token)
    print(f"обработано обновлений: {n}")
    return 0


def cmd_loop(args):
    state = load_state(args.state)
    offset = state.get("offset")
    admins = parse_admins(args.admin)
    print(f"хранилище users.json: {_storage_note(args)}")
    print("Бот запущен (loop). Ctrl+C для выхода.")
    while True:
        data, users_sha = load_users(args.users, repo=args.data_repo, token=args.data_token)
        try:
            updates = get_updates(args.token, offset=offset, timeout=30)
            for u in updates:
                process_update(u, args.token, admins, data, args.users)
            if updates:
                users_sha = save_users(args.users, data, users_sha,
                                       repo=args.data_repo, token=args.data_token)
                offset = max(u["update_id"] for u in updates) + 1
                save_state(args.state, {"offset": offset,
                                        "updated": time.strftime("%Y-%m-%d %H:%M:%S")})
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
