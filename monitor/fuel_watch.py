#!/usr/bin/env python3
"""Монитор появления бензина на АЗС (данные gdebenz.ru).

Опрашивает открытый JSON-эндпоинт gdebenz.ru и шлёт push-уведомление,
когда на выбранных заправках появляется топливо (переход «нет» -> «есть/очередь»).

Уведомления:
  * ntfy.sh  (рекомендуется: есть Android-приложение, push без Firebase)
  * Telegram (опционально, через бота)

Зависимости: только стандартная библиотека Python 3.

Использование:
  python3 fuel_watch.py list      --lat 48.680 --lon 44.470 --radius 8 [--fuel 92 95 ДТ]
  python3 fuel_watch.py watch     --lat 48.680 --lon 44.470 --radius 8 --topic mytopic [--fuel 92 95]
  python3 fuel_watch.py watch     --config config.json
  python3 fuel_watch.py once      --topic mytopic --state state.json   # для cron/облака

Параметры можно задавать переменными окружения: FUELWATCH_LAT, FUELWATCH_LON,
FUELWATCH_RADIUS, FUELWATCH_FUEL, FUELWATCH_TOPIC, FUELWATCH_STATE, FUELWATCH_INTERVAL.
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

API = "https://gdebenz.ru/api/nearby"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
DEFAULT_STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")

# status «есть топливо» — «yes» и «queue» (очередь = топливо есть, но ждать)
AVAILABLE_STATUSES = {"yes", "queue"}


def normalize_fuel(f):
    """'dt' -> 'ДТ', '92' -> '92', 'ДТ' -> 'ДТ'."""
    f = (f or "").strip().lower()
    if f in ("dt", "дт", "diesel", "дизель"):
        return "ДТ"
    if f in ("92", "95", "98", "100"):
        return f
    return f.upper()


def fetch_stations(lat, lon, radius_km, timeout=15):
    q = urllib.parse.urlencode({
        "lat": lat, "lon": lon, "radius_km": radius_km, "full": 1,
    })
    url = f"{API}?{q}"
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/json",
        "Referer": "https://gdebenz.ru/",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode("utf-8"))
    return data.get("stations", []), data.get("updated")


def station_fuels(station):
    """Множество нормализованных видов топлива, которые сейчас есть."""
    fuels = set()
    for f in (station.get("fuels_now") or "").replace(",", " ").split():
        fuels.add(normalize_fuel(f))
    # подстраховка: из detail берём только заголовочную часть со списком марок
    # (до «Очередь»/«Лимит»/«·»), чтобы не путать «100+ машин» с АИ-100.
    detail = station.get("detail") or ""
    head = re.split(r"Очередь|Лимит|·", detail)[0]
    for tok in re.findall(r"(?:^|[\s,])(92|95|98|100|ДТ|дт|Дт)(?=[\s,]|$)", head):
        fuels.add(normalize_fuel(tok))
    return fuels


def station_available(station, wanted_fuels):
    """Есть ли сейчас топливо (нужных марок) на станции."""
    if station.get("status") not in AVAILABLE_STATUSES:
        return False
    if not wanted_fuels:
        return True
    have = station_fuels(station)
    return bool(have & set(wanted_fuels))


def station_label(s):
    return f"{s.get('brand') or s.get('name') or 'АЗС'} · {s.get('addr') or 'без адреса'}"


def station_map_url(s):
    """Ссылка на Яндекс.Карты с точкой заправки (по координатам, иначе по адресу)."""
    lat, lon = s.get("lat"), s.get("lon")
    if lat is not None and lon is not None:
        return f"https://yandex.ru/maps/?pt={lon},{lat}&z=17"
    addr = s.get("addr")
    if addr:
        return "https://yandex.ru/maps/?text=" + urllib.parse.quote(addr)
    return ""


def send_ntfy(topic, title, message, priority="high", tags="fuel", click=None):
    headers = {
        "Title": title.encode("utf-8"),
        "Priority": priority,
        "Tags": tags,
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


def send_telegram(token, chat_id, text):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    body = urllib.parse.urlencode({
        "chat_id": chat_id, "text": text,
    }).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    with urllib.request.urlopen(req, timeout=15) as r:
        r.read()


class Monitor:
    def __init__(self, lat, lon, radius_km, wanted_fuels, ntfy_topic,
                 telegram_token=None, telegram_chat=None, state_file=DEFAULT_STATE_FILE,
                 interval=180, history_file=None):
        self.lat, self.lon, self.radius = lat, lon, radius_km
        self.wanted = [normalize_fuel(f) for f in (wanted_fuels or [])]
        self.ntfy_topic = ntfy_topic
        self.tg_token, self.tg_chat = telegram_token, telegram_chat
        self.state_file = state_file
        self.interval = interval
        self.history_file = history_file
        self.state = self._load_state()

    def _load_state(self):
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
        return {}

    def _save_state(self):
        with open(self.state_file, "w", encoding="utf-8") as f:
            json.dump(self.state, f, ensure_ascii=False, indent=2)

    def _append_history(self, stations):
        """Дописывает снимок всех АЗС в history.jsonl (для анализа графиков подвоза)."""
        if not self.history_file:
            return
        t = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with open(self.history_file, "a", encoding="utf-8") as f:
            for s in stations:
                rec = {
                    "t": t,
                    "osm_id": str(s.get("osm_id")),
                    "brand": s.get("brand") or s.get("name"),
                    "addr": s.get("addr"),
                    "status": s.get("status"),
                    "fuels": s.get("fuels_now"),
                    "last_at": s.get("last_at"),
                    "dist": s.get("distance_km"),
                }
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def _notify(self, text, click=None):
        print(f"[NOTIFY] {text}")
        if self.ntfy_topic:
            try:
                send_ntfy(self.ntfy_topic, "⛽ Бензин появился", text, click=click)
                print("  -> ntfy ok")
            except Exception as e:
                print(f"  -> ntfy FAIL: {e}")
        if self.tg_token and self.tg_chat:
            try:
                send_telegram(self.tg_token, self.tg_chat, "⛽ " + text)
                print("  -> telegram ok")
            except Exception as e:
                print(f"  -> telegram FAIL: {e}")

    def poll(self):
        stations, updated = fetch_stations(self.lat, self.lon, self.radius)
        print(f"\n[{time.strftime('%H:%M:%S')}] updated={updated}, станций={len(stations)}")
        self._append_history(stations)
        for s in stations:
            osm = s.get("osm_id")
            avail = station_available(s, self.wanted)
            prev = self.state.get(str(osm), {}).get("avail")
            if avail and prev is False:
                fuels = ", ".join(sorted(station_fuels(s))) or "?"
                map_url = station_map_url(s)
                text = f"{station_label(s)}\n{fuels}\n{s.get('detail') or ''}".strip()
                if map_url:
                    text += f"\n{map_url}"
                self._notify(text, click=map_url or None)
            elif avail and prev is None:
                pass  # впервые видим с топливом — фиксируем без уведомления
            elif not avail and prev:
                print(f"  (закончился) {station_label(s)}")
            self.state[str(osm)] = {
                "avail": avail,
                "label": station_label(s),
                "at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
        self._save_state()

    def run(self):
        print(f"Слежу за радиусом {self.radius} км от {self.lat},{self.lon}"
              f"{' | топливо: ' + ','.join(self.wanted) if self.wanted else ''}"
              f"{' | ntfy: ' + self.ntfy_topic if self.ntfy_topic else ''}")
        print("Первый опрос — фиксирую текущее состояние (без уведомлений).")
        try:
            self.poll()
        except Exception as e:
            print(f"Первый опрос не удался: {e} (продолжаю)")
        while True:
            time.sleep(self.interval)
            try:
                self.poll()
            except Exception as e:
                print(f"Ошибка опроса: {e}")


def cmd_list(args):
    stations, updated = fetch_stations(args.lat, args.lon, args.radius)
    print(f"Обновлено: {updated}\n")
    stations.sort(key=lambda s: s.get("distance_km", 1e9))
    for s in stations:
        mark = {"no": "✗", "queue": "⏳", "yes": "✓"}.get(s.get("status"), "?")
        fuels = ", ".join(sorted(station_fuels(s))) or "—"
        wanted = args.fuel
        if wanted and not (station_fuels(s) & set(wanted)):
            continue
        print(f"{mark} {station_label(s):45s} | {s.get('distance_km'):4.1f}км | {fuels:10s} | {s.get('detail') or ''}")


def main():
    p = argparse.ArgumentParser(description="Монитор бензина (gdebenz.ru)")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_common(sp):
        sp.add_argument("--lat", type=float, default=48.680)
        sp.add_argument("--lon", type=float, default=44.470)
        sp.add_argument("--radius", type=float, default=8.0)
        sp.add_argument("--fuel", nargs="*", default=[],
                        help="марки: 92 95 98 100 ДТ")
        sp.add_argument("--config", help="JSON-конфиг (переопределяет флаги)")

    lp = sub.add_parser("list", help="показать текущее наличие")
    add_common(lp)
    lp.set_defaults(func=cmd_list)

    wp = sub.add_parser("watch", help="следить и уведомлять")
    add_common(wp)
    wp.add_argument("--topic", help="ntfy.sh тема для push")
    wp.add_argument("--interval", type=int, default=180)
    wp.add_argument("--tg-token", help="токен Telegram-бота")
    wp.add_argument("--tg-chat", help="chat_id для Telegram")
    wp.add_argument("--history", help="файл истории снимков (JSONL)")
    wp.set_defaults(func=cmd_watch)

    op = sub.add_parser("once", help="один опрос с детекцией (для cron/облака)")
    add_common(op)
    op.add_argument("--topic", help="ntfy.sh тема для push")
    op.add_argument("--state", default=DEFAULT_STATE_FILE, help="файл состояния")
    op.add_argument("--history", help="файл истории снимков (JSONL)")
    op.set_defaults(func=cmd_once)

    args = p.parse_args()
    if getattr(args, "config", None):
        with open(args.config, encoding="utf-8") as f:
            cfg = json.load(f)
        for k, v in cfg.items():
            if not getattr(args, k, None):
                setattr(args, k, v)
    _env_override(args)
    sys.exit(args.func(args) or 0)


def cmd_watch(args):
    m = Monitor(args.lat, args.lon, args.radius, args.fuel, args.topic,
                args.tg_token, args.tg_chat, interval=args.interval,
                history_file=getattr(args, "history", None))
    m.run()


def cmd_once(args):
    m = Monitor(args.lat, args.lon, args.radius, args.fuel, args.topic,
                state_file=args.state, history_file=getattr(args, "history", None))
    try:
        m.poll()
    except Exception as e:
        print(f"Ошибка опроса: {e}")
        return 1
    return 0


def _env_override(args):
    """Подхватывает параметры из переменных окружения (для облака/cron)."""
    def apply(name, env, cast):
        v = os.environ.get(env)
        if v is not None and v != "":
            try:
                setattr(args, name, cast(v))
            except (TypeError, ValueError):
                pass
    apply("lat", "FUELWATCH_LAT", float)
    apply("lon", "FUELWATCH_LON", float)
    apply("radius", "FUELWATCH_RADIUS", float)
    apply("interval", "FUELWATCH_INTERVAL", int)
    apply("topic", "FUELWATCH_TOPIC", str)
    apply("state", "FUELWATCH_STATE", str)
    apply("history", "FUELWATCH_HISTORY", str)
    fuels = os.environ.get("FUELWATCH_FUEL")
    if fuels:
        setattr(args, "fuel", fuels.split())


if __name__ == "__main__":
    main()
