"""
Курсы брокера cifra.by (крипто-брокер, Беларусь) — страница /catalog.

Nuxt-приложение, но данные отдаются в серверном HTML: таблица с колонками
[Торговая пара, Наименование, Цена, Плечо Long, Плечо Short, Купить].
Главное для нас — цена USDT-RUB и крипта в USDT.

cifra.by изредка роняет TLS-соединение, поэтому запрос с ретраем.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass

import httpx
from selectolax.parser import HTMLParser

from .base import UA

URL = "https://cifra.by/catalog"
# ячейка пары = "BASE-QUOTE" + повтор BASE, напр. "USDT-RUBUSDT" / "BTC-USDTBTC"
_PAIR = re.compile(r"^([A-Z0-9]+)-([A-Z0-9]+?)\1$")


@dataclass
class CifraRate:
    pair: str        # напр. "USDT-RUB"
    name: str        # напр. "Tether"
    price: float


def _to_price(x: str) -> float | None:
    try:
        return float(x.replace(",", "."))
    except (ValueError, AttributeError):
        return None


async def fetch_cifra() -> list[CifraRate]:
    html = None
    for _ in range(3):  # cifra.by иногда обрывает TLS
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
        raise RuntimeError("cifra.by недоступен")

    tree = HTMLParser(html)
    out: list[CifraRate] = []
    for tr in tree.css("tr"):
        tds = [td.text(strip=True) for td in tr.css("td")]
        if len(tds) < 3:
            continue
        m = _PAIR.match(tds[0])
        price = _to_price(tds[2])
        if not m or price is None:
            continue
        pair = f"{m.group(1)}-{m.group(2)}"
        out.append(CifraRate(pair=pair, name=tds[1], price=price))
    return out


def format_cifra(rows: list[CifraRate]) -> str:
    lines = ["🏦 <b>Cifra.by</b> (брокер)"]
    for r in rows:
        if r.pair == "USDT-RUB":
            lines.append(f"💵 <b>{r.pair}</b>: <b>{r.price:g}</b> ₽")
    return "\n".join(lines)
