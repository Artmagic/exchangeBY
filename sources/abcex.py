"""
Курс USDT/RUB с биржи ABCEX.

Публичный API (без ключа), база — hub.abcex.io. Дневной тикер отдаёт
{"tickers": [...]}, берём запись symbol == "USDTRUB": lastPrice, bid/ask,
high/low и priceChangePercent (изменение за сутки в процентах).
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from .base import UA

URL = "https://hub.abcex.io/api/v2/exchange/public/candle/spot/ticker/day"
SYMBOL = "USDTRUB"


@dataclass
class Usdt:
    last: float
    bid: float
    ask: float
    high: float
    low: float
    change_pct: float       # изменение за сутки, %


async def fetch_abcex_usdt() -> Usdt | None:
    async with httpx.AsyncClient(
        headers={"User-Agent": UA, "Accept": "application/json"}, timeout=15
    ) as client:
        resp = await client.get(URL)
        resp.raise_for_status()
        tickers = (resp.json() or {}).get("tickers") or []

    row = next((x for x in tickers if x.get("symbol") == SYMBOL), None)
    if not row:
        return None

    def f(key: str) -> float:
        try:
            return float(row.get(key) or 0)
        except (TypeError, ValueError):
            return 0.0

    return Usdt(
        last=f("lastPrice"),
        bid=f("bidPrice"),
        ask=f("askPrice"),
        high=f("highPrice"),
        low=f("lowPrice"),
        change_pct=f("priceChangePercent"),
    )


def format_abcex(u: Usdt) -> str:
    arrow = "▲" if u.change_pct > 0 else ("▼" if u.change_pct < 0 else "=")
    return (
        "💲 <b>ABCEX USDT/RUB</b>\n"
        f"Цена: <b>{u.last:.2f}</b> ₽  {arrow} {u.change_pct:+.2f}%\n"
        f"Покупка: {u.bid:.2f} · Продажа: {u.ask:.2f}\n"
        f"За сутки: {u.low:.2f}–{u.high:.2f}"
    )
