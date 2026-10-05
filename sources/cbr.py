"""
Официальные курсы ЦБ РФ — USD, EUR, CNY на сегодня и на предыдущий день.

Источник: https://www.cbr-xml-daily.ru/daily_json.js — JSON-зеркало ЦБ. Отдаёт
дату действующих курсов (Date), предыдущую дату (PreviousDate) и по каждой
валюте Value (на Date) и Previous (на PreviousDate) с номиналом.

Это НЕ обменный курс банков, а официальный ориентир ЦБ, поэтому модуль стоит
особняком от агрегатора банковских касс (своя модель, свои команды/пуш).
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from .base import UA

URL = "https://www.cbr-xml-daily.ru/daily_json.js"
CODES = ("USD", "EUR", "CNY", "INR", "TRY", "AED")
_SYMBOL = {
    "USD": "🇺🇸 USD",
    "EUR": "🇪🇺 EUR",
    "CNY": "🇨🇳 CNY",
    "INR": "🇮🇳 INR",
    "TRY": "🇹🇷 TRY",
    "AED": "🇦🇪 AED",
}


@dataclass
class CbrRates:
    date: str           # YYYY-MM-DD — дата действующих курсов
    prev_date: str      # YYYY-MM-DD — предыдущая дата
    today: dict         # code -> курс за единицу
    prev: dict          # code -> курс за единицу


async def fetch_cbr() -> CbrRates:
    async with httpx.AsyncClient(headers={"User-Agent": UA}, timeout=20) as client:
        resp = await client.get(URL)
        resp.raise_for_status()
        d = resp.json()

    today, prev = {}, {}
    for code in CODES:
        v = d["Valute"][code]
        nominal = v.get("Nominal") or 1
        today[code] = v["Value"] / nominal
        prev[code] = v["Previous"] / nominal
    return CbrRates(
        date=d["Date"][:10],
        prev_date=d["PreviousDate"][:10],
        today=today,
        prev=prev,
    )


def format_cbr(c: CbrRates) -> str:
    lines = [f"🏛 <b>Курсы ЦБ РФ</b>", f"<i>{c.prev_date} → {c.date}</i>", ""]
    for code in CODES:
        t, p = c.today[code], c.prev[code]
        arrow = "▲" if t > p else ("▼" if t < p else "=")
        delta = t - p
        pct = (delta / p * 100) if p else 0.0
        lines.append(
            f"{_SYMBOL[code]}: {p:.4f} → <b>{t:.4f}</b> ₽  "
            f"{arrow} {delta:+.4f} ({pct:+.2f}%)"
        )
    return "\n".join(lines)
