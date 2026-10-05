"""
Курс USDT/RUB с биржи Rapira.

Публичный market-API: https://api.rapira.net/open/market/rates — массив data,
по символам. Берём запись symbol == "USDT/RUB": close (последняя цена),
bidPrice/askPrice, high/low, change за сутки.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from .base import UA

URL = "https://api.rapira.net/open/market/rates"
SYMBOL = "USDT/RUB"


@dataclass
class Usdt:
    close: float
    bid: float
    ask: float
    high: float
    low: float
    change: float       # изменение за сутки в рублях


async def fetch_rapira_usdt() -> Usdt | None:
    async with httpx.AsyncClient(
        headers={"User-Agent": UA, "Accept": "application/json"}, timeout=15
    ) as client:
        resp = await client.get(URL)
        resp.raise_for_status()
        data = resp.json().get("data") or []

    row = next((x for x in data if x.get("symbol") == SYMBOL), None)
    if not row:
        return None
    return Usdt(
        close=float(row.get("close") or 0),
        bid=float(row.get("bidPrice") or 0),
        ask=float(row.get("askPrice") or 0),
        high=float(row.get("high") or 0),
        low=float(row.get("low") or 0),
        change=float(row.get("change") or 0),
    )


def format_usdt(u: Usdt) -> str:
    arrow = "▲" if u.change > 0 else ("▼" if u.change < 0 else "=")
    return (
        "💲 <b>Rapira USDT/RUB</b>\n"
        f"Цена: <b>{u.close:.2f}</b> ₽  {arrow} {u.change:+.2f}\n"
        f"Покупка: {u.bid:.2f} · Продажа: {u.ask:.2f}\n"
        f"За сутки: {u.low:.2f}–{u.high:.2f}"
    )
