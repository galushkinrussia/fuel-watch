#!/usr/bin/env python3
"""Анализ времени появления топлива по history.jsonl.

Читает историю снимков (history.jsonl), находит переходы «нет -> есть/очередь»
(признак появления топлива) по каждой АЗС и выводит сводку: сколько раз и когда появлялось топливо.

Историю можно читать из приватного репозитория (DATA_REPO + DATA_PAT) —
тогда не нужно ничего скачивать вручную.

Зависимости: стандартная библиотека Python 3; data_store.py (из той же папки).

Использование:
  python3 analyze.py                                     # история из ./history.jsonl
  python3 analyze.py --history history.jsonl --min 2
  python3 analyze.py --station Лукойл                    # фильтр по названию/адресу
  python3 analyze.py --tz 3 --hourly                     # время UTC+3 + гистограмма
  python3 analyze.py --data-repo owner/repo --data-token <PAT>   # из приватного репо
  python3 analyze.py --data-repo ... --data-token ... --md analysis/ANALYSIS-FULL.md
"""

import argparse
import datetime
import json
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import data_store as ds

AVAIL = {"yes", "queue"}


def plural_form(n, one, few, many):
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def parse_records(text):
    recs = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            recs.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return recs


def load_records(path):
    with open(path, encoding="utf-8") as f:
        return parse_records(f.read())


def fmt_time(t, tz):
    """'2026-09-08T06:30:44Z' -> '08.09 06:30' со сдвигом на tz часов."""
    dt = datetime.datetime.strptime(t, "%Y-%m-%dT%H:%M:%SZ")
    dt += datetime.timedelta(hours=tz)
    return dt.strftime("%d.%m %H:%M")


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
        dists = [r.get("dist") for r in recs
                 if isinstance(r.get("dist"), (int, float))]
        if deliveries:
            results.append({"brand": brand, "addr": addr,
                            "dist": min(dists) if dists else None,
                            "deliveries": deliveries, "out_of_fuel": out_of_fuel})
    return results


def build_markdown(recs, results, tz):
    lines = []
    add = lines.append
    add("# ⛽ Анализ появления топлива\n")
    add("> Метод: переход станции «нет → есть/очередь» по отметкам водителей = "
        "момент появления топлива. Время приблизительное (метка запаздывает до "
        "~15 мин). Источник: https://gdebenz.ru\n")
    if recs:
        snaps = sorted({r["t"] for r in recs})
        add(f"История: **{len(recs)} записей, {len(snaps)} снимков**, "
            f"{snaps[0][:16]} — {snaps[-1][:16]} (UTC). Время — UTC{tz:+d}.\n")

    add("## Все АЗС с появлением топлива (по убыванию)\n")
    for r in results:
        n = len(r["deliveries"])
        add(f"### {r['brand']} · {r['addr']}\n")
        add(f"- Появлений: **{n}**")
        add(f"- Время: {', '.join(fmt_time(t, tz) for t in r['deliveries'])}")
        if r["out_of_fuel"]:
            add(f"- Заканчивался: {len(r['out_of_fuel'])} раз")
        add("")

    hours = Counter()
    for r in results:
        for t in r["deliveries"]:
            hours[hour_of(t, tz)] += 1
    add(f"## Распределение появления топлива по часам (UTC{tz:+d})\n")
    add("```")
    for h in range(24):
        if hours.get(h):
            add(f"  {h:02d}:00  {'#' * hours[h]}")
    add("```\n")

    consist = []
    for r in results:
        if len(r["deliveries"]) < 3:
            continue
        hh = [hour_of(t, tz) for t in r["deliveries"]]
        mh, mc = Counter(hh).most_common(1)[0]
        consist.append((r, mh, mc))
    consist.sort(key=lambda x: (-x[2], -len(x[0]["deliveries"])))
    add("## Самые устойчивые по времени АЗС (>=3 появлений)\n")
    add("| АЗС | Появлений | Час-мода |")
    add("|---|---|---|")
    for r, mh, mc in consist:
        add(f"| {r['brand']} · {r['addr']} | {len(r['deliveries'])} | {mh:02d}:00 ({mc}) |")
    add("")
    return "\n".join(lines)


def _station_line(r):
    base = f"{r['brand']}, {r['addr']}"
    dist = r.get("dist")
    if isinstance(dist, (int, float)):
        base += f" ({dist:.1f} км)"
    return base


def build_digest(recs, results, tz=3):
    """Короткая персональная сводка (для отправки пользователю)."""
    if not recs:
        return ("📊 Пока копим данные для анализа.\n\n"
                "История собирается автоматически; чтобы появились "
                "закономерности, нужно 2–3 дня. Загляните позже.")
    snaps = sorted({r["t"] for r in recs})
    days = {r["t"][:10] for r in recs}
    tz_lbl = "UTC" if tz == 0 else f"UTC{tz:+d}"
    lines = ["📊 Анализ по вашим АЗС", "",
             f"Период: {fmt_time(snaps[0], tz)} — {fmt_time(snaps[-1], tz)} "
             f"({tz_lbl}, {len(snaps)} "
             f"{plural_form(len(snaps), 'обновление', 'обновления', 'обновлений')})"]
    if not results:
        lines.append("")
        lines.append("Появлений топлива пока не замечено — это нормально на старте. "
                     "Обычно нужно 2–3 дня. Загляните позже.")
        return "\n".join(lines)

    total = sum(len(r["deliveries"]) for r in results)
    max_n = max(len(r["deliveries"]) for r in results)

    hours = Counter()
    for r in results:
        for t in r["deliveries"]:
            hours[hour_of(t, tz)] += 1
    lines.append("")
    lines.append("Чаще всего топливо появляется:")
    for h, c in hours.most_common(4):
        lines.append(f"  • {h:02d}:00–{(h + 1) % 24:02d}:00  ({c})")

    def _dist_key(r):
        d = r.get("dist")
        return d if isinstance(d, (int, float)) else 1e9

    top = sorted(results, key=lambda r: (-len(r["deliveries"]), _dist_key(r),
                                         r["brand"], r["addr"]))[:5]
    lines.append("")
    if max_n >= 3:
        lines.append("АЗС с 3+ появлениями:")
        for r in top:
            if len(r["deliveries"]) < 3:
                continue
            hh = [hour_of(t, tz) for t in r["deliveries"]]
            mh, mc = Counter(hh).most_common(1)[0]
            when = f"чаще всего ~{mh:02d}:00" if mc >= 2 else "время разное"
            lines.append(f"  • {_station_line(r)} — {len(r['deliveries'])} "
                         f"{plural_form(len(r['deliveries']), 'появление', 'появления', 'появлений')}, {when}")
    else:
        lines.append("Где замечали появление:")
        for r in top:
            hh = sorted({hour_of(t, tz) for t in r["deliveries"]})
            times = ", ".join(f"~{h:02d}:00" for h in hh)
            lines.append(f"  • {_station_line(r)} — "
                         f"{len(r['deliveries'])} "
                         f"{plural_form(len(r['deliveries']), 'раз', 'раза', 'раз')} в {times}")
        if len(days) < 3 or total < 10:
            lines.append("")
            lines.append("⚠️ Данных пока мало — картина станет точнее через 2–3 дня.")
    return "\n".join(lines)


def main():
    p = argparse.ArgumentParser(description="Анализ появления топлива")
    p.add_argument("--history", default="history.jsonl", help="путь/имя history.jsonl")
    p.add_argument("--data-repo", help="owner/repo приватного репо (или DATA_REPO)")
    p.add_argument("--data-token", help="PAT с правами contents (или DATA_PAT)")
    p.add_argument("--min", type=int, default=1, help="показывать АЗС с >= N появлениями")
    p.add_argument("--user", help="фильтр по user_id (персональный анализ)")
    p.add_argument("--station", help="фильтр по названию/адресу (подстрока)")
    p.add_argument("--tz", type=int, default=3, help="сдвиг часового пояса, ч (по умолчанию 3 = Москва)")
    p.add_argument("--hourly", action="store_true", help="гистограмма по часам суток")
    p.add_argument("--md", help="записать полный отчёт в markdown-файл")
    args = p.parse_args()

    repo = args.data_repo or os.environ.get("DATA_REPO")
    token = args.data_token or os.environ.get("DATA_PAT")

    if repo and token:
        try:
            text, _ = ds.load_text(repo, args.history, token, default="")
        except Exception as e:
            print(f"Не удалось прочитать историю из {repo}: {e}", file=sys.stderr)
            return 1
        recs = parse_records(text)
    else:
        if not os.path.exists(args.history):
            print(f"Файл не найден: {args.history}", file=sys.stderr)
            return 1
        recs = load_records(args.history)

    if not recs:
        print("В истории нет записей.")
        return 1

    if args.user:
        recs = [r for r in recs if str(r.get("user")) == str(args.user)]
        if not recs:
            print(f"Нет данных для пользователя {args.user}.")
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
          f"{snapshots[0][:16]} — {snapshots[-1][:16]} (UTC)\n")

    shown = 0
    for r in results:
        n = len(r["deliveries"])
        if n < args.min:
            continue
        shown += 1
        print(f"{r['brand']} · {r['addr']}\n  {n} {plural_form(n, 'появление', 'появления', 'появлений')}: "
              f"{', '.join(fmt_time(t, args.tz) for t in r['deliveries'])}\n")
    if shown == 0:
        print("Нет АЗС с появлением топлива по заданному фильтру.")

    if args.hourly:
        hours = Counter()
        for r in results:
            for t in r["deliveries"]:
                hours[hour_of(t, args.tz)] += 1
        print(f"\nРаспределение появления топлива по часам суток ({tz_sfx}):")
        for h in range(24):
            if hours.get(h):
                print(f"  {h:02d}:00  {'#' * hours[h]}")

    if args.md:
        with open(args.md, "w", encoding="utf-8") as f:
            f.write(build_markdown(recs, results, args.tz))
        print(f"\nОтчёт записан: {args.md}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
