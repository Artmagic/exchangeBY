"""
Источник bankiros.ru — по-банковые курсы наличной валюты.

Страница /currency/{cur}/{city} рендерится на сервере (httpx достаточно), но
вёрстка обфусцирована (классы xxx-*). Список банков лежит в .xxx-tbl-body__content;
каждая видимая строка (.xxx-tbl-row без модификаторов hidden/head) содержит:
  • имя банка — ссылка a[href*='/bank/'];
  • курсы покупки и продажи — два числовых <span class="xxx-fs-18"> по порядку.

Уровень — 'bank' (на странице показан лучший курс банка по городу).
"""

from __future__ import annotations

import re

import httpx
from selectolax.parser import HTMLParser

from .base import UA, Rate, Source, to_rate

URL = "https://bankiros.ru/currency/{cur}/{city}"
_NUM = re.compile(r"\d{2,3}([.,]\d{1,2})?$")

_CITY = {"moskva": "moskva", "sankt-peterburg": "sankt-peterburg"}


class Bankiros(Source):
    name = "bankiros.ru"
    needs_browser = False

    async def fetch(self, city: str = "moskva", currency: str = "USD") -> list[Rate]:
        url = URL.format(cur=currency.lower(), city=_CITY.get(city.lower(), city.lower()))
        async with httpx.AsyncClient(
            headers={"User-Agent": UA, "Accept-Language": "ru-RU,ru"},
            timeout=25,
            follow_redirects=True,
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            html = resp.text

        tree = HTMLParser(html)
        body = tree.css_first(".xxx-tbl-body__content")
        if not body:
            return []

        rates: list[Rate] = []
        seen: set = set()
        for row in body.css(".xxx-tbl-row"):
            cls = row.attributes.get("class") or ""
            if "hidden" in cls or "head" in cls:
                continue
            link = row.css_first("a[href*='/bank/']")
            bank = link.text(strip=True) if link else ""
            # курсы — два числовых span.xxx-fs-18 в порядке: покупка, продажа
            nums = []
            for sp in row.css("span.xxx-fs-18"):
                val = to_rate(sp.text(strip=True))
                if val is not None and _NUM.match(sp.text(strip=True).replace(" ", "")):
                    nums.append(val)
            if not bank or len(nums) < 2:
                continue
            buy, sell = nums[0], nums[1]
            if buy <= 0 or sell <= 0 or buy >= sell:
                continue
            if bank in seen:
                continue
            seen.add(bank)
            rates.append(
                Rate(
                    bank=bank,
                    buy=buy,
                    sell=sell,
                    currency=currency.upper(),
                    city=city.lower(),
                    source=self.name,
                    level="bank",
                )
            )
        return rates
