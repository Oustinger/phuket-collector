#!/usr/bin/env python3
"""
Сборщик цен на билеты Екатеринбург -> Пхукет.
Запускается на раннере GitHub Actions, пишет data/latest.json и data/history/<дата>.json.
Цены Travelpayouts отдаёт ЗА ОДНОГО пассажира и БЕЗ учёта багажа — это кэш минимальных цен.
"""
import json, os, pathlib, sys, urllib.parse, urllib.request
from datetime import datetime, timezone

TOKEN = os.environ.get("TP_TOKEN", "").strip()
MONTHS = ["2026-10", "2026-11"]
LEGS = [("SVX", "HKT"), ("MOW", "HKT"), ("MOW", "BKK"), ("BKK", "HKT"), ("SVX", "MOW")]
UA = {"User-Agent": "Mozilla/5.0 (compatible; price-monitor/1.0)"}


def get_json(url, headers=None):
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=45) as r:
        return json.loads(r.read().decode("utf-8"))


def from_travelpayouts(origin, dest, month):
    """Официальный API. Возвращает {'ГГГГ-ММ-ДД': {price, airline, transfers, duration}}."""
    q = urllib.parse.urlencode({
        "origin": origin, "destination": dest, "departure_at": month,
        "currency": "rub", "one_way": "true", "sorting": "price",
        "limit": 1000, "token": TOKEN,
    })
    payload = get_json("https://api.travelpayouts.com/aviasales/v3/prices_for_dates?" + q)
    by_day = {}
    for row in payload.get("data") or []:
        day = (row.get("departure_at") or "")[:10]
        price = row.get("price")
        if not day or price is None:
            continue
        by_day.setdefault(day, []).append(row)

    out = {}
    for day, rows in by_day.items():
        rows.sort(key=lambda r: r["price"])
        first = rows[0]

        def brief(r):
            rec = {
                "price": r["price"],
                "airline": r.get("airline"),
                "transfers": r.get("transfers"),
                "duration": r.get("duration"),
            }
            link = r.get("link")
            if link:
                rec["link"] = link if link.startswith("http") else "https://www.aviasales.ru" + link
            return rec

        rec = brief(first)
        # Самый быстрый среди тех, кто дороже минимума не более чем на треть.
        # Без этого в фид попадает только «дёшево и мучительно»: рейс за те же
        # деньги, но вдвое короче, просто не виден.
        near = [r for r in rows if r["price"] <= first["price"] * 1.33 and r.get("duration")]
        if near:
            fast = min(near, key=lambda r: (r["duration"], r["price"]))
            if fast is not first and fast["duration"] < (first.get("duration") or 10**9):
                rec["fast"] = brief(fast)
        # Запас дешёвых предложений на дату.
        rec["offers"] = len(rows)
        rec["cheapOffers"] = sum(1 for r in rows if r["price"] <= first["price"] * 1.1)
        rec["gap"] = (rows[1]["price"] - first["price"]) if len(rows) > 1 else None
        out[day] = rec
    return out


def from_calendar(origin, dest, month):
    """Недокументированный запасной источник Aviasales, без токена."""
    q = urllib.parse.urlencode({
        "origin": origin, "destination": dest, "depart_date": month,
        "one_way": "true", "currency": "rub",
    })
    payload = get_json("https://min-prices.aviasales.ru/calendar_preload?" + q)
    best = (payload.get("best_prices") or {}) if isinstance(payload, dict) else {}
    return {d: {"price": v} for d, v in best.items() if isinstance(v, (int, float))}


def collect_leg(origin, dest):
    cal, sources, errors = {}, [], []
    for month in MONTHS:
        for name, fn in (("travelpayouts", from_travelpayouts), ("calendar", from_calendar)):
            if name == "travelpayouts" and not TOKEN:
                continue
            try:
                got = fn(origin, dest, month)
            except Exception as e:
                errors.append(f"{name} {month}: {type(e).__name__}: {e}")
                continue
            if got:
                cal.update({d: v for d, v in got.items() if d not in cal})
                sources.append(f"{name}:{month}")
                break
    return {
        "calendar": dict(sorted(cal.items())),
        "min": min((v["price"] for v in cal.values()), default=None),
        "sources": sources,
        "errors": errors,
    }


def main():
    now = datetime.now(timezone.utc)
    result = {
        "collectedAt": now.isoformat(timespec="seconds"),
        "date": now.date().isoformat(),
        "pricesAre": "за 1 пассажира, без подтверждённого багажа",
        "legs": {f"{o}-{d}": collect_leg(o, d) for o, d in LEGS},
    }
    ok = sum(1 for v in result["legs"].values() if v["calendar"])
    result["legsWithData"] = f"{ok}/{len(LEGS)}"

    root = pathlib.Path(__file__).parent
    (root / "data" / "history").mkdir(parents=True, exist_ok=True)
    body = json.dumps(result, ensure_ascii=False, indent=1)
    (root / "data" / "latest.json").write_text(body, encoding="utf-8")
    (root / "data" / "history" / f"{result['date']}.json").write_text(body, encoding="utf-8")

    print(json.dumps({k: {"min": v["min"], "days": len(v["calendar"]), "src": v["sources"],
                          "err": v["errors"]} for k, v in result["legs"].items()},
                     ensure_ascii=False, indent=1))
    if ok == 0:
        sys.exit("Ни одно направление не отдало данных — смотри ошибки выше.")


if __name__ == "__main__":
    main()
