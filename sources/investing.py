"""
Котировки с investing.com (ru.investing.com) — рыночный курс к рублю.

Антибот investing.com не пускает httpx и обычный domcontentloaded-переход
(страница не «достраивается»). Рабочий приём: переходить с wait_until="commit"
и ЖДАТЬ конкретный элемент цены [data-test="instrument-price-last"] — он
появляется через несколько секунд, после чего читаем цену/изменение.

Пары берём из /currencies/<pair>: usd-rub, eur-rub, cny-rub.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from playwright.async_api import async_playwright

from .base import BROWSER_LOCK, UA, to_rate

PAIRS = {"USD/RUB": "usd-rub", "EUR/RUB": "eur-rub", "CNY/RUB": "cny-rub"}
_BASE = "https://ru.investing.com/currencies/{slug}"
_SEL_LAST = '[data-test="instrument-price-last"]'
_SEL_CHG = '[data-test="instrument-price-change"]'
_SEL_PCT = '[data-test="instrument-price-change-percent"]'
_ICON = {"USD/RUB": "💵", "EUR/RUB": "💶", "CNY/RUB": "🇨🇳"}


@dataclass
class InvRate:
    pair: str
    last: float
    change: str = ""       # как на сайте, напр. "+1,0145"
    change_pct: str = ""   # напр. "(+1,32%)"


# кэш результата (investing медленный — браузер) на CACHE_TTL секунд
_cache: tuple[float, dict] | None = None
CACHE_TTL = 120


async def _grab_pair(context, sem, pair: str, slug: str) -> tuple[str, InvRate | None]:
    async def _txt(page, sel):
        try:
            return (await page.inner_text(sel)).strip()
        except Exception:
            return ""

    for _ in range(2):  # ретрай — investing иногда не догружает страницу
        async with sem:  # не больше N страниц одновременно (надёжнее + меньше ОЗУ)
            page = await context.new_page()
            try:
                await page.goto(_BASE.format(slug=slug), wait_until="commit", timeout=30000)
                await page.wait_for_selector(_SEL_LAST, timeout=20000)
                last = to_rate(await page.inner_text(_SEL_LAST))
                if last is None:
                    continue
                return pair, InvRate(
                    pair=pair, last=last,
                    change=await _txt(page, _SEL_CHG),
                    change_pct=await _txt(page, _SEL_PCT),
                )
            except Exception:
                pass
            finally:
                await page.close()
    return pair, None


async def fetch_investing(cache_ttl: int = CACHE_TTL) -> dict[str, InvRate]:
    global _cache
    if cache_ttl > 0 and _cache and (time.time() - _cache[0]) < cache_ttl:
        return _cache[1]

    async with BROWSER_LOCK, async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True, args=["--disable-blink-features=AutomationControlled"]
        )
        context = await browser.new_context(
            locale="ru-RU",
            user_agent=UA,
            viewport={"width": 1366, "height": 900},
            extra_http_headers={"Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8"},
        )
        # грузим пары параллельно, но не больше 3 страниц одновременно
        sem = asyncio.Semaphore(3)
        results = await asyncio.gather(
            *(_grab_pair(context, sem, pair, slug) for pair, slug in PAIRS.items())
        )
        await browser.close()

    out = {pair: r for pair, r in results if r is not None}
    if cache_ttl > 0 and out:
        _cache = (time.time(), out)
    return out


def format_investing(data: dict[str, InvRate]) -> str:
    lines = ["📈 <b>Investing.com</b> (рыночный курс)"]
    for pair in PAIRS:
        r = data.get(pair)
        if not r:
            continue
        tail = f"  {r.change} {r.change_pct}".rstrip()
        lines.append(f"{_ICON.get(pair, '•')} {pair}: <b>{r.last:.2f}</b> ₽{tail}")
    return "\n".join(lines)
