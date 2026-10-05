"""
Источник myfin.by — по-банковые курсы наличной валюты (один курс на банк).

Страница /currency/{city} отдаёт обычную HTML-таблицу (без JS):
    Банк | USD покупка | USD продажа | EUR покупка | EUR продажа
Парсим её через httpx + selectolax. Уровень данных — 'bank' (не по пунктам),
зато покрытие шире banki.ru: тут есть банки, которых на banki.ru нет.
"""

from __future__ import annotations

import httpx
from selectolax.parser import HTMLParser

from .base import UA, Rate, Source, to_rate

URL = "https://myfin.by/currency/{city}"

# смещение пары (покупка, продажа) в числовых ячейках строки для каждой валюты
_OFFSET = {"USD": (0, 1), "EUR": (2, 3)}

# myfin использует свои слаги городов
_CITY = {"moskva": "moskva", "sankt-peterburg": "sankt-peterburg"}


class Myfin(Source):
    name = "myfin.by"
    needs_browser = False

    async def fetch(self, city: str = "moskva", currency: str = "USD") -> list[Rate]:
        currency = currency.upper()
        if currency not in _OFFSET:
            return []  # на этой таблице только USD/EUR
        bi, si = _OFFSET[currency]
        url = URL.format(city=_CITY.get(city.lower(), city.lower()))

        async with httpx.AsyncClient(
            headers={"User-Agent": UA, "Accept-Language": "ru-RU,ru"},
            timeout=25,
            follow_redirects=True,
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            html = resp.text

        tree = HTMLParser(html)
        rates: list[Rate] = []
        for tr in tree.css("tr"):
            # строка банка = есть ссылка на /bank...
            if not any("bank" in (a.attributes.get("href") or "") for a in tr.css("a")):
                continue
            tds = tr.css("td")
            if not tds:
                continue
            bank = tds[0].text(strip=True)
            nums = []
            for td in tds[1:]:
                r = to_rate(td.text(strip=True))
                if r is not None:
                    nums.append(r)
            if not bank or len(nums) <= max(bi, si):
                continue
            buy, sell = nums[bi], nums[si]
            if buy <= 0 or sell <= 0 or buy >= sell:
                continue  # некорректный спред → мусорная строка
            rates.append(
                Rate(
                    bank=bank,
                    buy=buy,
                    sell=sell,
                    currency=currency,
                    city=city.lower(),
                    source=self.name,
                    level="bank",
                )
            )
        return rates
