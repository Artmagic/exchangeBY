"""
Рыночный (mid-market) курс USD/RUB и EUR/RUB.

Это межбанковский серединный курс — ровно то, что показывают xe.com и
investing.com. Сами xe/investing закрыты антиботом (xe — auth-токен, investing —
Cloudflare), поэтому берём тот же курс из открытого бесплатного API
open.er-api.com (без ключа, обновление раз в сутки/чаще).

EUR/RUB считаем кросс-курсом: RUB-за-USD ÷ EUR-за-USD.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from .base import UA

URL = "https://open.er-api.com/v6/latest/USD"


@dataclass
class Market:
    usd_rub: float
    eur_rub: float
    updated: str = ""


async def fetch_market() -> Market | None:
    async with httpx.AsyncClient(headers={"User-Agent": UA}, timeout=20) as client:
        resp = await client.get(URL)
        resp.raise_for_status()
        d = resp.json()

    if d.get("result") != "success":
        return None
    rates = d.get("rates") or {}
    rub, eur = rates.get("RUB"), rates.get("EUR")
    if not rub:
        return None
    return Market(
        usd_rub=float(rub),
        eur_rub=float(rub) / float(eur) if eur else 0.0,
        updated=(d.get("time_last_update_utc") or "")[:16],
    )


async def fetch_inr_rub() -> float | None:
    """INR/RUB как mid-market кросс (RUB-за-USD ÷ INR-за-USD).

    Живого биржевого INR/RUB у profinance/investing нет, поэтому рупию берём
    отсюда — межбанковский серединный курс.
    """
    async with httpx.AsyncClient(headers={"User-Agent": UA}, timeout=20) as client:
        resp = await client.get(URL)
        resp.raise_for_status()
        rates = (resp.json().get("rates")) or {}
    rub, inr = rates.get("RUB"), rates.get("INR")
    return float(rub) / float(inr) if rub and inr else None


def format_market(m: Market) -> str:
    lines = [
        "🌐 <b>Рыночный курс (mid-market)</b>",
        f"💵 USD/RUB: <b>{m.usd_rub:.2f}</b> ₽",
    ]
    if m.eur_rub:
        lines.append(f"💶 EUR/RUB: <b>{m.eur_rub:.2f}</b> ₽")
    if m.updated:
        lines.append(f"<i>обновлено: {m.updated}</i>")
    return "\n".join(lines)
