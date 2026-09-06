#!/usr/bin/env python3
"""Релей сообщений городского чата gdebenz.ru в ntfy.

Опрашивает открытый эндпоинт чата и шлёт push-уведомление на каждое новое
сообщение водителей города.

Зависимости: только стандартная библиотека Python 3.

Использование:
  python3 chat_watch.py once --city volgograd --topic mytopic --state chat_state.json

Параметры можно задавать переменными окружения: CHATWATCH_CITY, CHATWATCH_TOPIC,
CHATWATCH_STATE, CHATWATCH_LIMIT.
"""

import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request

API = "https://api.gdebenz.ru"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
DEFAULT_STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chat_state.json")


def fetch_messages(city, limit=30, timeout=15):
    url = f"{API}/api/chats/city/{urllib.parse.quote(city)}/messages?limit={limit}"
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/json",
        "Referer": "https://gdebenz.ru/",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode("utf-8"))
    return data.get("messages", [])


def send_ntfy(topic, title, message, click=None):
    headers = {
        "Title": title.encode("utf-8"),
        "Priority": "default",
        "Tags": "speech_balloon",
    }
    if click:
        headers["Click"] = click.encode("utf-8")
    req = urllib.request.Request(
        f"https://ntfy.sh/{urllib.parse.quote(topic)}",
        data=message.encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.status == 200


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


def message_text(m):
    text = (m.get("body") or "").strip()
    if m.get("reply_to_name"):
        excerpt = (m.get("reply_to_excerpt") or "").strip()
        text += f"\n↪ {m['reply_to_name']}" + (f": {excerpt}" if excerpt else "")
    return text


def poll(city, topic, state_file, limit=30):
    messages = fetch_messages(city, limit=limit)
    state = load_state(state_file)
    last_id = int(state.get("last_id", 0) or 0)

    new = [m for m in messages if int(m.get("id", 0)) > last_id]
    new.sort(key=lambda m: int(m["id"]))  # хронологически: от старых к новым

    if not last_id:
        print(f"[{time.strftime('%H:%M:%S')}] Первый опрос: фиксирую базу "
              f"({len(messages)} сообщений в кеше), без уведомлений.")
    else:
        print(f"[{time.strftime('%H:%M:%S')}] Новых сообщений: {len(new)}")
        for m in new:
            author = m.get("author_name") or "Водитель"
            text = message_text(m)
            title = f"💬 {author}"
            try:
                send_ntfy(topic, title, text, click=f"https://gdebenz.ru/chat/{urllib.parse.quote(city)}")
                print(f"  -> ntfy ok ({author})")
            except Exception as e:
                print(f"  -> ntfy FAIL: {e}")

    if messages:
        state["last_id"] = max(int(m.get("id", 0)) for m in messages)
    state["city"] = city
    state["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    save_state(state_file, state)


def main():
    p = argparse.ArgumentParser(description="Релей чата водителей gdebenz.ru в ntfy")
    sub = p.add_subparsers(dest="cmd", required=True)

    op = sub.add_parser("once", help="один опрос чата (для cron/облака)")
    op.add_argument("--city", help="slug города (напр. volgograd)")
    op.add_argument("--topic", help="ntfy.sh тема для push")
    op.add_argument("--state", default=DEFAULT_STATE_FILE, help="файл состояния")
    op.add_argument("--limit", type=int, default=30)
    op.set_defaults(func=cmd_once)

    args = p.parse_args()
    _env_override(args)
    sys.exit(args.func(args) or 0)


def cmd_once(args):
    if not args.city:
        print("Ошибка: не задан город (--city или CHATWATCH_CITY)", file=sys.stderr)
        return 2
    if not args.topic:
        print("Ошибка: не задана тема ntfy (--topic или CHATWATCH_TOPIC)", file=sys.stderr)
        return 2
    try:
        poll(args.city, args.topic, args.state, limit=args.limit)
    except Exception as e:
        print(f"Ошибка опроса чата: {e}")
        return 1
    return 0


def _env_override(args):
    def apply(name, env, cast):
        v = os.environ.get(env)
        if v is not None and v != "":
            try:
                setattr(args, name, cast(v))
            except (TypeError, ValueError):
                pass

    apply("city", "CHATWATCH_CITY", str)
    apply("topic", "CHATWATCH_TOPIC", str)
    apply("state", "CHATWATCH_STATE", str)
    apply("limit", "CHATWATCH_LIMIT", int)


if __name__ == "__main__":
    main()
