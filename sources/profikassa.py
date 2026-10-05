"""
Курсы обменника profikassa.ru (точка «Ленинградка») — наличные USD/EUR/AED/CNY/TRY.

Значения лежат в HTML в элементах с классами rate-<...>-buy / rate-<...>-sell
(их читает и JS-тикер сайта). Достаём по этим классам через selectolax.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass

import httpx
from selectolax.parser import HTMLParser

from .base import UA

URL = "https://profikassa.ru/leningradka-valyuta"

# (флаг, подпись, класс покупки, класс продажи) — порядок как на сайте
ROWS = [
    ("🇺🇸", "USD «NexGen»", "rate-usd-blue-buy", "rate-usd-blue-sell"),
    ("🇺🇸", "USD 1996–2006", "rate-usd-old-buy", "rate-usd-old-sell"),
    ("🇪🇺", "EUR «Европа»", "rate-eur-buy", "rate-eur-sell"),
    ("🇪🇺", "EUR 2002", "rate-eur2002-buy", "rate-eur2002-sell"),
    ("🇦🇪", "AED Дирхам", "rate-aed-buy", "rate-aed-sell"),
    ("🇨🇳", "CNY Юань", "rate-cny-buy", "rate-cny-sell"),
    ("🇹🇷", "TRY Лира", "rate-try-buy", "rate-try-sell"),
    ("🇺🇸", "USD от 5000 «NexGen»", "rate-usd5000-buy", "rate-usd5000-sell"),
    ("🇪🇺", "EUR от 5000 «Европа»", "rate-eur5000-buy", "rate-eur5000-sell"),
]
_NUM = re.compile(r"\d+[.,]\d{1,2}")


@dataclass
class PkRate:
    flag: str
    label: str
    buy: str
    sell: str


async def fetch_profikassa() -> list[PkRate]:
    html = None
    for _ in range(3):  # profikassa изредка обрывает соединение
        try:
            async with httpx.AsyncClient(
                headers={"User-Agent": UA, "Accept-Language": "ru-RU,ru"},
                timeout=20,
                follow_redirects=True,
            ) as client:
                resp = await client.get(URL)
                resp.raise_for_status()
                html = resp.text
                break
        except Exception:
            await asyncio.sleep(1.5)
    if html is None:
        raise RuntimeError("profikassa.ru недоступен")

    tree = HTMLParser(html)

    def val(cls: str) -> str:
        el = tree.css_first("." + cls)
        if not el:
            return ""
        m = _NUM.search(el.text(strip=True))
        return m.group(0) if m else ""

    out: list[PkRate] = []
    for flag, label, bc, sc in ROWS:
        buy, sell = val(bc), val(sc)
        if buy or sell:
            out.append(PkRate(flag=flag, label=label, buy=buy or "—", sell=sell or "—"))
    return out


def format_profikassa(rows: list[PkRate]) -> str:
    lines = ["🏦 <b>Вернадка</b> (profikassa.ru)", "<i>покупка / продажа</i>", ""]
    for r in rows:
        lines.append(f"{r.flag} <b>{r.label}</b>: {r.buy} / {r.sell}")
    return "\n".join(lines)
