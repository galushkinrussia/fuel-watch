#!/usr/bin/env python3
"""Многопользовательский монитор топлива.

Читает users.json (настройки пользователей) и для каждого активного
пользователя опрашивает его координаты, шлёт push в его ntfy-тему при
появлении топлива. Состояние детекции хранится в users_state.json.

users.json можно хранить в приватном GitHub-репозитории (через DATA_REPO
и DATA_PAT) — тогда координаты/темы пользователей не попадают в публичный код.

Режимы:
  once — один проход по всем пользователям (для cron/GitHub Actions)
  loop — бесконечный цикл (для VPS/ПК)

Использование:
  python3 multi_watch.py once --users users.json --state users_state.json
  python3 multi_watch.py loop --users users.json --state users_state.json --interval 180

Переменные окружения: MULTI_USERS, MULTI_STATE, MULTI_INTERVAL,
DATA_REPO (owner/repo приватного репо), DATA_PAT (токен с правами contents).

Зависимости: fuel_watch.py, data_store.py (из той же папки), стандартная библиотека.
"""

import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fuel_watch as fw
import data_store as ds

TG_API = "https://api.telegram.org"

DEFAULT_USERS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "users.json")
DEFAULT_STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "users_state.json")


def send_telegram(token, chat_id, text):
    """Дублирует уведомление в чат пользователя с ботом (chat_id = user_id)."""
    if not token:
        return
    url = f"{TG_API}/bot{token}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=20) as r:
        r.read()


def load_users(path, repo=None, token=None):
    """Читает users.json: из приватного репо (repo+token) или локально."""
    if repo and token:
        try:
            data, _ = ds.load_json(repo, path, token,
                                   default={"invites": {}, "users": {}})
            return data
        except Exception as e:
            print(f"Не удалось прочитать users.json из {repo}: {e}")
            return {"invites": {}, "users": {}}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {"invites": {}, "users": {}}


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


def active_users(users):
    """Пользователи с координатами, темой и включёнными уведомлениями."""
    out = {}
    for uid, u in users.get("users", {}).items():
        if (u.get("lat") is not None and u.get("lon") is not None
                and u.get("topic") and u.get("enabled", True)):
            out[uid] = u
    return out


def _log_send(buf, uid, label, fuels, ntfy_ok, telegram_ok):
    """Добавляет строку журнала отправок в буфер (пишется в конце прохода)."""
    if buf is None:
        return
    buf.append(json.dumps({
        "t": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "user": uid,
        "station": label,
        "fuel": fuels,
        "ntfy": ntfy_ok,
        "telegram": telegram_ok,
    }, ensure_ascii=False) + "\n")


def _read_local(path):
    if path and os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return f.read()
        except OSError:
            pass
    return ""


def poll_user(uid, u, state, tg_token=None, sends_buf=None):
    wanted = u.get("fuel") or []
    stations, updated = fw.fetch_stations(u["lat"], u["lon"], u.get("radius", 8))
    for s in stations:
        osm = str(s.get("osm_id"))
        avail = fw.station_available(s, wanted)
        prev = state.get(osm, {}).get("avail")
        if avail and prev is False:
            fuels = ", ".join(sorted(fw.station_fuels(s))) or "?"
            label = fw.station_label(s)
            text = f"{label}\n{fuels}\n{s.get('detail') or ''}".strip()
            map_url = fw.station_map_url(s)
            if map_url:
                text += f"\n{map_url}"
            ntfy_ok = False
            try:
                fw.send_ntfy(u["topic"], "⛽ Бензин появился", text,
                             click=map_url or None)
                ntfy_ok = True
                print(f"  -> notify ok: {label}")
            except Exception as e:
                print(f"  -> ntfy FAIL: {e}")
            telegram_ok = False
            if tg_token:
                try:
                    send_telegram(tg_token, uid, "⛽ Бензин появился\n\n" + text)
                    telegram_ok = True
                    print(f"  -> telegram ok: {label}")
                except Exception as e:
                    print(f"  -> telegram FAIL: {e}")
            _log_send(sends_buf, uid, label, fuels, ntfy_ok, telegram_ok)
        elif not avail and prev:
            print(f"  (закончился) {fw.station_label(s)}")
        state[osm] = {
            "avail": avail,
            "label": fw.station_label(s),
            "at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
    return updated, len(stations)


def poll_all(users_path, state_path, repo=None, token=None, tg_token=None,
             sends_path=None):
    users = load_users(users_path, repo=repo, token=token)
    remote = bool(repo and token)
    if remote:
        state, state_sha = ds.load_json(repo, state_path, token, default={})
        sends_text, sends_sha = (ds.load_text(repo, sends_path, token, default="")
                                 if sends_path else ("", None))
    else:
        state = load_state(state_path)
        state_sha = None
        sends_text, sends_sha = _read_local(sends_path), None
    sends_buf = [] if sends_path else None

    actives = active_users(users)
    if not actives:
        print("Нет активных пользователей (не заданы координаты/тема).")
    for uid, u in actives.items():
        ustate = state.setdefault(str(uid), {})
        try:
            updated, n = poll_user(uid, u, ustate, tg_token=tg_token,
                                   sends_buf=sends_buf)
            print(f"[{time.strftime('%H:%M:%S')}] user {uid}: "
                  f"updated={updated}, станций={n}")
        except Exception as e:
            print(f"[{time.strftime('%H:%M:%S')}] user {uid}: ОШИБКА {e}")

    if remote:
        try:
            ds.save_json(repo, state_path, token, state, state_sha,
                         message="состояние монитора")
        except Exception as e:
            print(f"не удалось сохранить состояние: {e}")
        if sends_path and sends_buf:
            try:
                ds.save_text(repo, sends_path, token, sends_text + "".join(sends_buf),
                             sends_sha, message="журнал отправок")
            except Exception as e:
                print(f"не удалось сохранить журнал отправок: {e}")
    else:
        save_state(state_path, state)
        if sends_path and sends_buf:
            with open(sends_path, "a", encoding="utf-8") as f:
                f.write("".join(sends_buf))
    return len(actives)


def _storage_note(args):
    if args.data_repo and args.data_token:
        return f"приватный репо {args.data_repo}"
    if args.data_repo or args.data_token:
        return "ЛОКАЛЬНЫЙ файл (задан только один из DATA_REPO/DATA_PAT — проверь оба!)"
    return "ЛОКАЛЬНЫЙ файл (DATA_REPO/DATA_PAT не заданы)"


def cmd_once(args):
    print(f"[{time.strftime('%H:%M:%S')}] хранилище данных: {_storage_note(args)}")
    n = poll_all(args.users, args.state, repo=args.data_repo, token=args.data_token,
                 tg_token=args.telegram_token, sends_path=args.sends)
    print(f"активных пользователей: {n}")
    return 0


def cmd_loop(args):
    print(f"хранилище данных: {_storage_note(args)}")
    print("Мультимонитор запущен (loop). Ctrl+C для выхода.")
    while True:
        try:
            poll_all(args.users, args.state, repo=args.data_repo, token=args.data_token,
                     tg_token=args.telegram_token, sends_path=args.sends)
        except Exception as e:
            print(f"ошибка цикла: {e}")
        try:
            time.sleep(args.interval)
        except KeyboardInterrupt:
            break
    return 0


def main():
    p = argparse.ArgumentParser(description="Многопользовательский монитор топлива")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_common(sp):
        sp.add_argument("--users", default=DEFAULT_USERS, help="файл пользователей")
        sp.add_argument("--state", default=DEFAULT_STATE, help="файл состояния")
        sp.add_argument("--data-repo", help="owner/repo приватного репо с users.json")
        sp.add_argument("--data-token", help="PAT с правами contents")
        sp.add_argument("--telegram-token", help="токен бота для дубля в Telegram (или TELEGRAM_BOT_TOKEN)")
        sp.add_argument("--sends", default=None, help="файл журнала отправок (JSONL)")

    op = sub.add_parser("once", help="один проход (для cron/облака)")
    add_common(op)
    op.set_defaults(func=cmd_once)

    lp = sub.add_parser("loop", help="постоянный цикл (VPS/ПК)")
    add_common(lp)
    lp.add_argument("--interval", type=int, default=180)
    lp.set_defaults(func=cmd_loop)

    args = p.parse_args()
    _env_override(args)
    return args.func(args) or 0


def _env_override(args):
    def apply(name, env):
        v = os.environ.get(env)
        if v is not None and v != "":
            setattr(args, name, v)
    apply("users", "MULTI_USERS")
    apply("state", "MULTI_STATE")
    apply("interval", "MULTI_INTERVAL")
    apply("data_repo", "DATA_REPO")
    apply("data_token", "DATA_PAT")
    apply("telegram_token", "TELEGRAM_BOT_TOKEN")
    apply("sends", "MULTI_SENDS")


if __name__ == "__main__":
    sys.exit(main())
