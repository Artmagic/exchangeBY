"""
Агрегатор курсов: собирает все источники параллельно и сводит в один результат.

Принципы:
  • источники-плагины (sources/*) с общим интерфейсом Source.fetch();
  • параллельный сбор с изоляцией ошибок — один упавший источник не роняет всё;
  • дедуп по банку: для каждого банка берём ЛУЧШИЙ курс среди всех пунктов и
    источников (макс buy для bid, мин sell для ask), запоминая источник/адрес;
  • TTL-кэш, чтобы не дёргать сайты на каждый запрос пользователя.

Добавить источник = добавить файл в sources/ и одну строку в SOURCES.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from sources.banki import BankiRu
from sources.bankiros import Bankiros
from sources.base import Rate, normalize_bank
from sources.myfin import Myfin
from sources.sravni import Sravni

logger = logging.getLogger(__name__)

# Реестр активных источников. Добавление нового — одна строка.
SOURCES = [BankiRu(), Myfin(), Sravni(), Bankiros()]


@dataclass
class BankBest:
    """Сводка по одному банку: лучший курс покупки и продажи среди всех пунктов."""

    bank: str
    buy: float                 # лучший (макс) — банк покупает у вас дороже
    sell: float                # лучший (мин)  — банк продаёт вам дешевле
    buy_src: str = ""
    sell_src: str = ""
    buy_where: str = ""        # адрес/метро лучшего пункта покупки
    sell_where: str = ""
    sources: tuple = ()        # все источники, где встретился банк
    offices: int = 0           # сколько пунктов учтено


# --- TTL-кэш ----------------------------------------------------------------
_cache: dict = {}


def _cache_get(key, ttl):
    item = _cache.get(key)
    if item and (time.time() - item[0]) < ttl:
        return item[1]
    return None


def _cache_set(key, value):
    _cache[key] = (time.time(), value)


async def collect_rates(city: str, currency: str) -> list[Rate]:
    """Сырые курсы со всех источников (с изоляцией ошибок)."""

    async def safe(src):
        try:
            rates = await src.fetch(city, currency)
            logger.info("%s: %d записей", src.name, len(rates))
            return rates
        except Exception:
            logger.exception("Источник %s упал", src.name)
            return []

    results = await asyncio.gather(*(safe(s) for s in SOURCES))
    return [r for batch in results for r in batch]


def merge_by_bank(rates: list[Rate]) -> list[BankBest]:
    """Сводим все курсы к одному лучшему предложению на банк."""
    by_key: dict[str, list[Rate]] = {}
    for r in rates:
        by_key.setdefault(normalize_bank(r.bank), []).append(r)

    out: list[BankBest] = []
    for group in by_key.values():
        best_buy = max(group, key=lambda r: r.buy)    # выгоднее продать банку
        best_sell = min(group, key=lambda r: r.sell)  # выгоднее купить у банка
        # человекочитаемое имя — самое длинное (обычно полное)
        name = max((r.bank for r in group), key=len)
        srcs = tuple(sorted({r.source for r in group}))
        out.append(
            BankBest(
                bank=name,
                buy=best_buy.buy,
                sell=best_sell.sell,
                buy_src=best_buy.source,
                sell_src=best_sell.source,
                buy_where=" / ".join(x for x in (best_buy.metro, best_buy.address) if x),
                sell_where=" / ".join(x for x in (best_sell.metro, best_sell.address) if x),
                sources=srcs,
                offices=len(group),
            )
        )
    return out


async def get_top(
    city: str = "moskva",
    currency: str = "USD",
    top: int = 10,
    cache_ttl: int = 180,
) -> tuple[list[BankBest], list[BankBest], dict]:
    """Топ банков по покупке (bid) и продаже (ask) + метаданные сбора."""
    key = (city.lower(), currency.upper())
    cached = _cache_get(key, cache_ttl) if cache_ttl > 0 else None
    if cached is None:
        rates = await collect_rates(city, currency)
        merged = merge_by_bank(rates)
        meta = {
            "banks": len(merged),
            "rates": len(rates),
            "sources": sorted({r.source for r in rates}),
        }
        cached = (merged, meta)
        if cache_ttl > 0 and merged:
            _cache_set(key, cached)
    merged, meta = cached

    top_bid = sorted(merged, key=lambda b: b.buy, reverse=True)[:top]
    top_ask = sorted(merged, key=lambda b: b.sell)[:top]
    return top_bid, top_ask, meta


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    async def _main():
        bid, ask, meta = await get_top("moskva", "USD", top=10, cache_ttl=0)
        print(f"\nИсточники: {meta['sources']} | банков: {meta['banks']} | записей: {meta['rates']}\n")
        print("=== ТОП-10 ПОКУПКА (банк покупает у вас дороже) ===")
        for i, b in enumerate(bid, 1):
            print(f"  {i:>2}. {b.buy:>7.2f}  {b.bank[:30]:30} [{','.join(b.sources)}]")
        print("\n=== ТОП-10 ПРОДАЖА (банк продаёт вам дешевле) ===")
        for i, b in enumerate(ask, 1):
            print(f"  {i:>2}. {b.sell:>7.2f}  {b.bank[:30]:30} [{','.join(b.sources)}]")

    asyncio.run(_main())
