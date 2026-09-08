#!/usr/bin/env python3
"""Часовая сводка городского чата gdebenz.ru в ntfy.

Собирает новые сообщения чата водителей города и шлёт ОДНО push-уведомление
(сводку) за период вместо спама отдельными сообщениями. Предназначен для
запуска раз в час из внешнего cron.

Зависимости: только стандартная библиотека Python 3.

Использование:
  python3 chat_watch.py once --city volgograd --topic mytopic --state chat_state.json

Параметры: CHATWATCH_CITY, CHATWATCH_TOPIC, CHATWATCH_STATE, CHATWATCH_LIMIT.
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

# макс. размер тела сводки (символов). Кириллица = 2 байта/символ, а ntfy
# превращает в файл всё, что больше 4096 байт, поэтому держим запас.
MAX_BODY = 1800
# обрезка текста одного сообщения
BODY_LIMIT = 80


def _get_json(url, timeout=15):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/json",
        "Referer": "https://gdebenz.ru/",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch_page(city, cursor=None, limit=30):
    url = f"{API}/api/chats/city/{urllib.parse.quote(city)}/messages?limit={limit}"
    if cursor:
        url += f"&cursor={cursor}"
    return _get_json(url)


def fetch_new_messages(city, last_id, max_pages=10, limit=30):
    """Возвращает (все_сообщения, новые) — новые отсортированы от старых к новым."""
    if not last_id:
        data = fetch_page(city, limit=limit)
        all_msgs = data.get("messages", [])
    else:
        all_msgs = []
        cursor = None
        for _ in range(max_pages):
            data = fetch_page(city, cursor=cursor, limit=limit)
            msgs = data.get("messages", [])
            if not msgs:
                break
            all_msgs.extend(msgs)
            if any(int(m.get("id", 0)) <= last_id for m in msgs):
                break
            cursor = data.get("next_cursor")
            if not cursor:
                break
    new = [m for m in all_msgs if int(m.get("id", 0)) > last_id]
    new.sort(key=lambda m: int(m["id"]), reverse=True)  # свежие первыми
    return all_msgs, new


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


def build_summary(messages):
    lines = []
    for m in messages:
        author = m.get("author_name") or "Водитель"
        body = " ".join((m.get("body") or "").split())
        if len(body) > BODY_LIMIT:
            body = body[:BODY_LIMIT - 1] + "…"
        lines.append(f"• {author}: {body}")

    text = "\n".join(lines)
    if len(text) <= MAX_BODY:
        return text

    keep, total = [], 0
    for line in lines:
        if total + len(line) + 1 > MAX_BODY:
            break
        keep.append(line)
        total += len(line) + 1
    omitted = len(messages) - len(keep)
    text = "\n".join(keep)
    if omitted > 0:
        text += f"\n…и ещё {omitted} сообщ."
    return text


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


def poll(city, topic, state_file):
    state = load_state(state_file)
    last_id = int(state.get("last_id", 0) or 0)

    all_msgs, new = fetch_new_messages(city, last_id)
    newest_id = max((int(m.get("id", 0)) for m in all_msgs), default=last_id)

    if not last_id:
        print(f"[{time.strftime('%H:%M:%S')}] Первый опрос: фиксирую базу "
              f"({len(all_msgs)} сообщ. в кеше), без уведомлений.")
    elif new:
        n = len(new)
        text = build_summary(new)
        title = f"💬 Чат водителей · {n} сообщ."
        try:
            send_ntfy(topic, title, text, click=f"https://gdebenz.ru/chat/{urllib.parse.quote(city)}")
            print(f"[{time.strftime('%H:%M:%S')}] Сводка отправлена ({n} сообщ.)")
        except Exception as e:
            print(f"  -> ntfy FAIL: {e}")
    else:
        print(f"[{time.strftime('%H:%M:%S')}] Нет новых сообщений.")

    state["last_id"] = newest_id
    state["city"] = city
    state["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    save_state(state_file, state)


def main():
    p = argparse.ArgumentParser(description="Часовая сводка чата gdebenz.ru в ntfy")
    sub = p.add_subparsers(dest="cmd", required=True)

    op = sub.add_parser("once", help="одна сводка (для cron/облака)")
    op.add_argument("--city", help="slug города (напр. volgograd)")
    op.add_argument("--topic", help="ntfy.sh тема для push")
    op.add_argument("--state", default=DEFAULT_STATE_FILE, help="файл состояния")
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
        poll(args.city, args.topic, args.state)
    except Exception as e:
        print(f"Ошибка сводки чата: {e}")
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


if __name__ == "__main__":
    main()
