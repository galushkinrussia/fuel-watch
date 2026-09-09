#!/usr/bin/env python3
"""Анализ времени подвоза топлива по history.jsonl.

Читает историю снимков (history.jsonl), которую копит fuel_watch.py, находит
переходы «нет -> есть/очередь» (признак подвоза) по каждой АЗС и выводит
сводку: сколько раз и когда привозили топливо.

Зависимости: только стандартная библиотека Python 3.

Использование:
  python3 analyze.py                            # история из ./history.jsonl
  python3 analyze.py --history history.jsonl --min 2
  python3 analyze.py --station Лукойл           # фильтр по названию/адресу
  python3 analyze.py --tz 3                     # время в UTC+3 (Москва)
  python3 analyze.py --hourly                   # + гистограмма по часам суток
"""

import argparse
import json
import os
import sys
from collections import Counter, defaultdict

AVAIL = {"yes", "queue"}


def load_records(path):
    recs = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                recs.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return recs


def fmt_time(t, tz):
    """'2026-09-08T06:30:44Z' -> '08.09 06:30' со сдвигом на tz часов."""
    mon = t[5:7]
    day = t[8:10]
    hh = (int(t[11:13]) + tz) % 24
    mm = t[14:16]
    return f"{day}.{mon} {hh:02d}:{mm}"


def hour_of(t, tz):
    return (int(t[11:13]) + tz) % 24


def analyze(recs):
    by_station = defaultdict(list)
    for r in recs:
        key = (r.get("brand") or r.get("name") or "АЗС", r.get("addr") or "без адреса")
        by_station[key].append(r)

    results = []
    for (brand, addr), recs in by_station.items():
        recs.sort(key=lambda x: x["t"])
        prev = None
        deliveries, out_of_fuel = [], []
        for r in recs:
            st = r.get("status")
            if prev is not None:
                if st in AVAIL and prev not in AVAIL:
                    deliveries.append(r["t"])
                elif st not in AVAIL and prev in AVAIL:
                    out_of_fuel.append(r["t"])
            prev = st
        if deliveries:
            results.append({
                "brand": brand, "addr": addr,
                "deliveries": deliveries, "out_of_fuel": out_of_fuel,
            })
    return results


def main():
    p = argparse.ArgumentParser(description="Анализ подвоза топлива по history.jsonl")
    p.add_argument("--history", default="history.jsonl", help="путь к history.jsonl")
    p.add_argument("--min", type=int, default=1, help="показывать АЗС с >= N подвозами")
    p.add_argument("--station", help="фильтр по названию/адресу (подстрока)")
    p.add_argument("--tz", type=int, default=0, help="сдвиг часового пояса, ч (3 = Москва)")
    p.add_argument("--hourly", action="store_true", help="показать гистограмму по часам суток")
    args = p.parse_args()

    if not os.path.exists(args.history):
        print(f"Файл не найден: {args.history}", file=sys.stderr)
        return 1

    recs = load_records(args.history)
    if not recs:
        print("В истории нет записей.")
        return 1

    results = analyze(recs)
    if args.station:
        q = args.station.lower()
        results = [r for r in results
                   if q in (r["brand"] + "").lower() or q in (r["addr"] + "").lower()]

    results.sort(key=lambda r: (-len(r["deliveries"]), r["brand"], r["addr"]))

    snapshots = sorted({r["t"] for r in recs})
    tz_sfx = "UTC" if not args.tz else f"UTC{args.tz:+d}"
    print(f"История: {len(recs)} записей, {len(snapshots)} снимков, "
          f"{snapshots[0][:16]} — {snapshots[-1][:16]} (UTC)")
    print()

    shown = 0
    for r in results:
        n = len(r["deliveries"])
        if n < args.min:
            continue
        shown += 1
        label = f"{r['brand']} · {r['addr']}"
        word = "подвоз" if n % 10 == 1 and n % 100 != 11 else \
               ("подвоза" if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else "подвозов")
        times = ", ".join(fmt_time(t, args.tz) for t in r["deliveries"])
        print(f"{label}\n  {n} {word}: {times}")
        if r["out_of_fuel"]:
            print(f"  (заканчивался {len(r['out_of_fuel'])} раз)")
        print()

    if shown == 0:
        print("Нет АЗС с подвозами по заданному фильтру.")

    if args.hourly:
        hours = Counter()
        for r in results:
            for t in r["deliveries"]:
                hours[hour_of(t, args.tz)] += 1
        print(f"Распределение подвозов по часам суток ({tz_sfx}):")
        for h in range(24):
            if hours.get(h):
                print(f"  {h:02d}:00  {'#' * hours[h]}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
