"""
Источник banki.ru — по-пунктовые курсы наличной валюты.

Под капотом сайт грузит данные из внутреннего JSON-API:
    /products/currencyNodejsApi/getBanksOrExchanges/?isExchangeOffices=1
Без bankId и с большим perPage он отдаёт ПЛОСКИЙ список всех обменных пунктов
города (для Москвы/USD ~168 пунктов, 23 банка) — с адресом, метро и курсами.

API отдаёт 302 без куки/антибот-сессии, поэтому короткую сессию поднимаем
в headless-браузере и ходим в API через context.request (несёт куки).
"""

from __future__ import annotations

import re

from playwright.async_api import async_playwright

from .base import BROWSER_LOCK, ISO, UA, Rate, Source, to_rate

PAGE_URL = "https://www.banki.ru/products/currency/cash/{currency}/{city}/"
API_URL = "https://www.banki.ru/products/currencyNodejsApi/getBanksOrExchanges/"


class BankiRu(Source):
    name = "banki.ru"
    needs_browser = True

    async def fetch(self, city: str = "moskva", currency: str = "USD") -> list[Rate]:
        page_url = PAGE_URL.format(currency=currency.lower(), city=city.lower())
        currency_id = ISO.get(currency.upper(), 840)

        async with BROWSER_LOCK, async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True, args=["--disable-blink-features=AutomationControlled"]
            )
            context = await browser.new_context(
                locale="ru-RU", user_agent=UA, viewport={"width": 1366, "height": 1000}
            )
            page = await context.new_page()
            await page.goto(page_url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(2500)

            # уточняем currencyId со страницы, если получится
            m = re.search(
                rf'"id"\s*:\s*(\d+)\s*,\s*"code"\s*:\s*"{currency.upper()}"',
                await page.content(),
            )
            if m:
                currency_id = int(m.group(1))

            offices: list[dict] = []
            page_num = 1
            while True:
                params = {
                    "regionUrl": city.lower(),
                    "currencyId": str(currency_id),
                    "amount": "1",
                    "perPage": "500",
                    "page": str(page_num),
                    "sortAttribute": "sale",
                    "order": "asc",
                    "isExchangeOffices": "1",
                }
                resp = await context.request.get(
                    API_URL,
                    params=params,
                    headers={
                        "X-Requested-With": "XMLHttpRequest",
                        "Accept": "application/json, text/plain, */*",
                        "Referer": page_url,
                    },
                )
                data = await resp.json()
                chunk = data.get("list") or []
                offices += chunk
                if not data.get("isNextPage") or not chunk or page_num > 20:
                    break
                page_num += 1

            await browser.close()

        rates: list[Rate] = []
        for o in offices:
            ex = o.get("exchange") or {}
            buy, sell = to_rate(ex.get("buy")), to_rate(ex.get("sale"))
            bank = o.get("bankName") or o.get("name")
            if not bank or buy is None or sell is None:
                continue
            ci = o.get("contactInformation") or {}
            rates.append(
                Rate(
                    bank=bank,
                    buy=buy,
                    sell=sell,
                    currency=currency.upper(),
                    city=city.lower(),
                    source=self.name,
                    level="office",
                    address=ci.get("address") or o.get("name") or "",
                    metro=ci.get("metroStation") or "",
                    updated=ex.get("refreshDate") or "",
                )
            )
        return rates
