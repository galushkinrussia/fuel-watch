#!/usr/bin/env python3
"""Телеграм-бот для настройки монитора топлива.

Меняет настройки, хранящиеся в GitHub Variables репозитория (LAT, LON,
RADIUS, FUEL, CHAT_CITY), прямо из чата с ботом в Telegram. После изменения
следующая итерация fuel-monitor/chat-monitor подхватит новое значение.

Команды бота:
  /start, /help          — список команд
  /status                — текущие значения переменных
  /set <ключ> <значение> — изменить: lat, lon, radius, fuel, city
  /setup                 — зарегистрировать команды в меню (разово)

Как работает: бот читает обновления через long polling (getUpdates с offset),
обрабатывает команды, обновляет GitHub Variables через REST API и отвечает.
Режим `once` — для запуска по cron (GitHub Actions + cron-job.org),
режим `loop` — для постоянного процесса (VPS/ПК).

Переменные окружения:
  TELEGRAM_BOT_TOKEN — токен бота (обязательно)
  TELEGRAM_ADMIN     — user_id владельца (только ему разрешено менять настройки)
  GH_TOKEN           — GitHub PAT с правами на Variables (обязательно)
  GH_REPO            — "owner/repo" (по умолчанию galushkinrussia/fuel-watch)
  TGBOT_STATE        — файл состояния (offset long polling)

Зависимости: только стандартная библиотека Python 3.
"""

import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request

TG_API = "https://api.telegram.org"
GH_API = "https://api.github.com"

DEFAULT_STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tg_bot_state.json")

KEY_MAP = {
    "lat": "LAT",
    "lon": "LON",
    "radius": "RADIUS",
    "fuel": "FUEL",
    "city": "CHAT_CITY",
}

HELP_TEXT = (
    "Настройки монитора топлива:\n"
    "  /status — показать текущие\n"
    "  /set lat 48.700\n"
    "  /set lon 44.500\n"
    "  /set radius 8\n"
    "  /set fuel 92 95\n"
    "  /set city volgograd"
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


def send_message(token, chat_id, text):
    _tg("sendMessage", token, {"chat_id": chat_id, "text": text})


def set_commands(token):
    commands = [
        {"command": "status", "description": "Текущие настройки монитора"},
        {"command": "set", "description": "Изменить настройку: lat/lon/radius/fuel/city"},
        {"command": "help", "description": "Помощь"},
    ]
    _tg("setMyCommands", token, {"commands": json.dumps(commands)})


# --- GitHub Variables -------------------------------------------------------

def _gh(method, url, token, body=None):
    data = None
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=20) as r:
        raw = r.read().decode("utf-8")
        return json.loads(raw) if raw else {}


def gh_list_vars(repo, token):
    data = _gh("GET", f"{GH_API}/repos/{repo}/actions/variables", token)
    return {v["name"]: v["value"] for v in data.get("variables", [])}


def gh_set_var(repo, token, name, value):
    url = f"{GH_API}/repos/{repo}/actions/variables/{urllib.parse.quote(name)}"
    try:
        _gh("PATCH", url, token, {"name": name, "value": value})
    except urllib.error.HTTPError as e:
        if e.code == 404:
            _gh("POST", f"{GH_API}/repos/{repo}/actions/variables", token,
                {"name": name, "value": value})
        else:
            raise


# --- Обработка команд -------------------------------------------------------

def extract_message(update):
    msg = update.get("message") or {}
    text = (msg.get("text") or "").strip()
    chat_id = (msg.get("chat") or {}).get("id")
    user_id = (msg.get("from") or {}).get("id")
    return text, chat_id, user_id


def parse_set(text):
    parts = text.split()
    if len(parts) >= 3 and parts[0].lower() in ("/set", "set"):
        key = parts[1].lower()
        value = " ".join(parts[2:])
        if key in KEY_MAP:
            return key, value
    return None, None


def handle_command(text, user_id, allowed_user, token, repo, gh_token):
    t = text.strip()

    if allowed_user and str(user_id) != str(allowed_user):
        return "У вас нет доступа к изменению настроек."

    if t.startswith("/start") or t.startswith("/help") or t == "help":
        return HELP_TEXT

    if t.startswith("/status") or t == "status":
        try:
            vars_ = gh_list_vars(repo, gh_token)
        except Exception as e:
            return f"Не удалось прочитать переменные: {e}"
        lines = ["Текущие настройки:"]
        for key, name in KEY_MAP.items():
            lines.append(f"  {key} = {vars_.get(name, '—')}")
        return "\n".join(lines)

    if t.startswith("/setup"):
        try:
            set_commands(token)
            return "Команды зарегистрированы в меню."
        except Exception as e:
            return f"Ошибка регистрации команд: {e}"

    key, value = parse_set(t)
    if key:
        name = KEY_MAP[key]
        try:
            gh_set_var(repo, gh_token, name, value)
            return f"OK: {key} → {value} (переменная {name})"
        except Exception as e:
            return f"Не удалось записать {key}: {e}"

    return "Не понял команду. Напишите /help."


# --- Режимы запуска ---------------------------------------------------------

def load_state(state_file):
    if os.path.exists(state_file):
        try:
            with open(state_file, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_state(state_file, state):
    with open(state_file, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def process(token, repo, gh_token, allowed_user, offset, state_file):
    updates = get_updates(token, offset=offset, timeout=0)
    for u in updates:
        text, chat_id, user_id = extract_message(u)
        if not text or not chat_id:
            continue
        print(f"[{time.strftime('%H:%M:%S')}] от {user_id}: {text!r}")
        reply = handle_command(text, user_id, allowed_user, token, repo, gh_token)
        try:
            send_message(token, chat_id, reply)
        except Exception as e:
            print(f"  -> send FAIL: {e}")
    new_offset = max((u["update_id"] for u in updates), default=offset) + 1 \
        if updates else offset
    state = {"offset": new_offset, "updated": time.strftime("%Y-%m-%d %H:%M:%S")}
    save_state(state_file, state)
    return len(updates)


def cmd_once(args):
    offset = load_state(args.state).get("offset")
    n = process(args.token, args.repo, args.gh_token, args.admin, offset, args.state)
    print(f"обработано обновлений: {n}")
    return 0


def cmd_loop(args):
    state = load_state(args.state)
    offset = state.get("offset")
    print("Бот запущен (loop). Ctrl+C для выхода.")
    while True:
        try:
            updates = get_updates(args.token, offset=offset, timeout=30)
            for u in updates:
                text, chat_id, user_id = extract_message(u)
                if not text or not chat_id:
                    continue
                print(f"[{time.strftime('%H:%M:%S')}] от {user_id}: {text!r}")
                reply = handle_command(text, user_id, args.admin, args.token,
                                       args.repo, args.gh_token)
                try:
                    send_message(args.token, chat_id, reply)
                except Exception as e:
                    print(f"  -> send FAIL: {e}")
            if updates:
                offset = max(u["update_id"] for u in updates) + 1
                state = {"offset": offset, "updated": time.strftime("%Y-%m-%d %H:%M:%S")}
                save_state(args.state, state)
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"ошибка цикла: {e}")
            time.sleep(5)
    return 0


def main():
    p = argparse.ArgumentParser(description="Телеграм-бот для настройки монитора")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_common(sp):
        sp.add_argument("--token", help="токен бота (или TELEGRAM_BOT_TOKEN)")
        sp.add_argument("--gh-token", help="GitHub PAT (или GH_TOKEN)")
        sp.add_argument("--repo", default="galushkinrussia/fuel-watch")
        sp.add_argument("--admin", help="user_id владельца (или TELEGRAM_ADMIN)")
        sp.add_argument("--state", default=DEFAULT_STATE_FILE)

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
    if not args.gh_token:
        print("Ошибка: не задан GitHub PAT (--gh-token или GH_TOKEN)",
              file=sys.stderr)
        return 2
    return args.func(args) or 0


def _env_override(args):
    def apply(name, env):
        v = os.environ.get(env)
        if v is not None and v != "":
            setattr(args, name, v)
    apply("token", "TELEGRAM_BOT_TOKEN")
    apply("gh_token", "GH_TOKEN")
    apply("repo", "GH_REPO")
    apply("admin", "TELEGRAM_ADMIN")
    apply("state", "TGBOT_STATE")


if __name__ == "__main__":
    sys.exit(main())
