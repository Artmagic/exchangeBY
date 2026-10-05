"""
Источник sravni.ru — по-пунктовые курсы (страница /valjuty/punkty-obmena/).

Особенности:
  • страница на Next.js; данные лежат в <script id="__NEXT_DATA__"> внутри
    кэша react-query (initialState.query.queries);
  • по httpx сайт капризничает (то полный SSR, то лёгкая заглушка), поэтому
    грузим через короткую браузерную сессию;
  • имена банков НЕ лежат рядом с курсами: курсы привязаны к branchId, а имена —
    в объектах {organization:{name},rates:[…]}. Мы ищем именно такие объекты:
    у каждого банка берём его массив rates (по пунктам) с buy/sell.

Уровень — 'office' (курс конкретного пункта), но без уличного адреса (его в
payload нет), поэтому address/metro оставляем пустыми.
"""

from __future__ import annotations

import json
import re

from playwright.async_api import async_playwright

from .base import BROWSER_LOCK, UA, Rate, Source, to_rate

URL = "https://www.sravni.ru/valjuty/punkty-obmena/{city}/"
_NEXT = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)


def _iter_org_rates(node):
    """Рекурсивно находим объекты вида {organization:{name},rates:[…]}."""
    if isinstance(node, dict):
        if isinstance(node.get("organization"), dict) and isinstance(node.get("rates"), list):
            yield node
        for v in node.values():
            yield from _iter_org_rates(v)
    elif isinstance(node, list):
        for v in node:
            yield from _iter_org_rates(v)


class Sravni(Source):
    name = "sravni.ru"
    needs_browser = True

    async def fetch(self, city: str = "moskva", currency: str = "USD") -> list[Rate]:
        url = URL.format(city=city.lower())
        currency = currency.upper()

        async with BROWSER_LOCK, async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True, args=["--disable-blink-features=AutomationControlled"]
            )
            context = await browser.new_context(locale="ru-RU", user_agent=UA)
            page = await context.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(3500)
            html = await page.content()
            await browser.close()

        m = _NEXT.search(html)
        if not m:
            return []
        data = json.loads(m.group(1))

        seen: set = set()
        rates: list[Rate] = []
        for node in _iter_org_rates(data):
            org = node["organization"]
            name = (org.get("name") or {}).get("short") or org.get("alias")
            if not name:
                continue
            for r in node["rates"]:
                if r.get("currency") != currency:
                    continue
                buy, sell = to_rate(r.get("buy")), to_rate(r.get("sell"))
                if buy is None or sell is None or buy >= sell:
                    continue
                key = (org.get("alias"), r.get("branchId"), buy, sell)
                if key in seen:
                    continue
                seen.add(key)
                rates.append(
                    Rate(
                        bank=name,
                        buy=buy,
                        sell=sell,
                        currency=currency,
                        city=city.lower(),
                        source=self.name,
                        level="office",
                        updated=r.get("updateDate") or "",
                    )
                )
        return rates
