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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fuel_watch as fw
import data_store as ds

DEFAULT_USERS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "users.json")
DEFAULT_STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "users_state.json")


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
    """Пользователи с заданными координатами и темой (готовые к мониторингу)."""
    out = {}
    for uid, u in users.get("users", {}).items():
        if u.get("lat") is not None and u.get("lon") is not None and u.get("topic"):
            out[uid] = u
    return out


def poll_user(uid, u, state):
    wanted = u.get("fuel") or []
    stations, updated = fw.fetch_stations(u["lat"], u["lon"], u.get("radius", 8))
    for s in stations:
        osm = str(s.get("osm_id"))
        avail = fw.station_available(s, wanted)
        prev = state.get(osm, {}).get("avail")
        if avail and prev is False:
            fuels = ", ".join(sorted(fw.station_fuels(s))) or "?"
            text = f"{fw.station_label(s)}\n{fuels}\n{s.get('detail') or ''}".strip()
            map_url = fw.station_map_url(s)
            if map_url:
                text += f"\n{map_url}"
            try:
                fw.send_ntfy(u["topic"], "⛽ Бензин появился", text,
                             click=map_url or None)
                print(f"  -> notify ok: {fw.station_label(s)}")
            except Exception as e:
                print(f"  -> ntfy FAIL: {e}")
        elif not avail and prev:
            print(f"  (закончился) {fw.station_label(s)}")
        state[osm] = {
            "avail": avail,
            "label": fw.station_label(s),
            "at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
    return updated, len(stations)


def poll_all(users_path, state_path, repo=None, token=None):
    users = load_users(users_path, repo=repo, token=token)
    state = load_state(state_path)
    actives = active_users(users)
    if not actives:
        print("Нет активных пользователей (не заданы координаты/тема).")
    for uid, u in actives.items():
        ustate = state.setdefault(str(uid), {})
        try:
            updated, n = poll_user(uid, u, ustate)
            print(f"[{time.strftime('%H:%M:%S')}] user {uid}: "
                  f"updated={updated}, станций={n}")
        except Exception as e:
            print(f"[{time.strftime('%H:%M:%S')}] user {uid}: ОШИБКА {e}")
    save_state(state_path, state)
    return len(actives)


def _storage_note(args):
    if args.data_repo and args.data_token:
        return f"приватный репо {args.data_repo}"
    if args.data_repo or args.data_token:
        return "ЛОКАЛЬНЫЙ файл (задан только один из DATA_REPO/DATA_PAT — проверь оба!)"
    return "ЛОКАЛЬНЫЙ файл (DATA_REPO/DATA_PAT не заданы)"


def cmd_once(args):
    print(f"[{time.strftime('%H:%M:%S')}] хранилище users.json: {_storage_note(args)}")
    n = poll_all(args.users, args.state, repo=args.data_repo, token=args.data_token)
    print(f"активных пользователей: {n}")
    return 0


def cmd_loop(args):
    print(f"хранилище users.json: {_storage_note(args)}")
    print("Мультимонитор запущен (loop). Ctrl+C для выхода.")
    while True:
        try:
            poll_all(args.users, args.state, repo=args.data_repo, token=args.data_token)
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


if __name__ == "__main__":
    sys.exit(main())
