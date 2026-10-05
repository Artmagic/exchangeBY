"""
Курсы с xe.com (mid-market) — пары к доллару.

Публичный API xe закрыт Basic-авторизацией, но токен зашит в их же фронтенд-JS
(один на всех, не секрет). Эндпоинт midmarket-converter отдаёт курсы всех валют
к USD: rates[X] = сколько X за 1 USD. Значит X/USD = 1 / rates[X].

Если xe однажды сменит токен — источник просто перестанет отвечать (обрабатываем
мягко), и токен надо будет обновить (взять из запроса на xe.com/currencyconverter).
"""

from __future__ import annotations

import httpx

from .base import UA

API = "https://www.xe.com/api/protected/midmarket-converter/"
AUTH = "Basic bG9kZXN0YXI6cHVnc25heA=="  # публичный ключ фронтенда xe.com

# ярлык пары -> код валюты (X/USD = 1 / rates[X])
PAIRS = {
    "EUR/USD": "EUR",
    "GBP/USD": "GBP",
    "CHF/USD": "CHF",
    "CNY/USD": "CNY",
    "INR/USD": "INR",
}
_ICON = {
    "EUR/USD": "🇪🇺",
    "GBP/USD": "🇬🇧",
    "CHF/USD": "🇨🇭",
    "CNY/USD": "🇨🇳",
    "INR/USD": "🇮🇳",
}


def _fmt(v: float) -> str:
    # мелкие значения (напр. INR/USD ≈ 0.012) показываем точнее
    return f"{v:.4f}" if v >= 0.1 else f"{v:.5f}"


async def fetch_xe() -> dict[str, float]:
    headers = {"User-Agent": UA, "Authorization": AUTH, "Accept": "application/json"}
    async with httpx.AsyncClient(headers=headers, timeout=15) as client:
        resp = await client.get(API)
        resp.raise_for_status()
        rates = (resp.json().get("rates")) or {}

    out: dict[str, float] = {}
    for label, code in PAIRS.items():
        v = rates.get(code)
        if v:
            out[label] = 1.0 / float(v)
    return out


def format_xe(data: dict[str, float]) -> str:
    lines = ["🌐 <b>xe.com</b> (mid-market)"]
    for label in PAIRS:
        if label not in data:
            continue
        base = label.split("/")[0]       # напр. EUR из EUR/USD
        v = data[label]                  # X/USD
        rev = 1.0 / v                     # USD/X (обратный курс)
        lines.append(
            f"{_ICON[label]} {label} <b>{_fmt(v)}</b> · "
            f"USD/{base} <b>{_fmt(rev)}</b>"
        )
    return "\n".join(lines)
