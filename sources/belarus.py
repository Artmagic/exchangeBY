"""
Курсы в Беларуси (Минск) с myfin.by — кросс-курсы USD/RUB и EUR/RUB.

Страницы /currency/usdrub и /currency/eurrub отдают серверный HTML. Сводная
строка по банку — <tr class="currencies-courses__row-main"> с атрибутом
data-bank-sef-alias (надёжный идентификатор банка) и ячейками
[название/реклама, Покупает, Продает, ...].

Семантика (кросс к рублю РФ):
  • Покупает (buy)  — банк покупает у вас валюту за RUB. Чтобы ПРОДАТЬ валюту —
    выгоден БОЛЬШИЙ курс.
  • Продает (sell)  — банк продаёт вам валюту за RUB. Чтобы КУПИТЬ — выгоден
    МЕНЬШИЙ курс.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import httpx

from .base import UA, to_rate

PAIRS = {
    "USD/RUB": "https://myfin.by/currency/usdrub",
    "EUR/RUB": "https://myfin.by/currency/eurrub",
}

# алиас myfin → человекочитаемое имя банка
_NAMES = {
    "bnbank": "БНБ-Банк",
    "statusbank": "Статусбанк",
    "alfabank": "Альфа-Банк",
    "rrb-bank": "РРБ-Банк",
    "mtbank": "МТБанк",
    "belswissbank": "БСБ Банк",
    "zepterbank": "Цептер Банк",
    "btabank": "БТА Банк",
    "bvebank": "БВ Банк",
    "bank-vtb": "Банк ВТБ (Беларусь)",
    "dabrabyt": "Банк Дабрабыт",
    "reshenie": "Банк Решение",
    "belagroprombank": "Белагропромбанк",
    "belarusbank": "Беларусбанк",
    "belgazprombank": "Белгазпромбанк",
    "belinvestbank": "Белинвестбанк",
    "belinkasgrupp": "Белинкасгрупп",
    "paritetbank": "Паритетбанк",
    "priorbank": "Приорбанк",
    "bps-sberbank": "БПС-Сбербанк",
    "technobank": "Технобанк",
    "tkbank": "ТК Банк",
}


def _name(alias: str) -> str:
    return _NAMES.get(alias) or alias.replace("-", " ").title()


@dataclass
class ByRate:
    bank: str
    buy: float       # банк покупает валюту за RUB (продать выгодно — макс.)
    sell: float      # банк продаёт валюту за RUB (купить выгодно — мин.)


async def _fetch_pair(client: httpx.AsyncClient, url: str) -> list[ByRate]:
    from selectolax.parser import HTMLParser

    resp = await client.get(url)
    resp.raise_for_status()
    tree = HTMLParser(resp.text)

    out: dict[str, ByRate] = {}
    for tr in tree.css("tr.currencies-courses__row-main"):
        alias = (tr.attributes or {}).get("data-bank-sef-alias", "")
        if not alias or alias in out:
            continue
        nums = []
        for td in tr.css("td")[1:]:
            r = to_rate(td.text(strip=True))
            if r is not None and re.fullmatch(
                r"\d{2,3}([.,]\d{1,4})?", td.text(strip=True).replace(" ", "")
            ):
                nums.append(r)
        if len(nums) < 2 or nums[0] <= 0 or nums[1] <= 0 or nums[0] >= nums[1]:
            continue
        out[alias] = ByRate(bank=_name(alias), buy=nums[0], sell=nums[1])
    return list(out.values())


async def fetch_belarus() -> dict[str, list[ByRate]]:
    """Возвращает {'USD/RUB': [...], 'EUR/RUB': [...]} по банкам Минска."""
    result: dict[str, list[ByRate]] = {}
    async with httpx.AsyncClient(
        headers={"User-Agent": UA, "Accept-Language": "ru-RU,ru"},
        timeout=25,
        follow_redirects=True,
    ) as client:
        for pair, url in PAIRS.items():
            try:
                result[pair] = await _fetch_pair(client, url)
            except Exception:
                result[pair] = []
    return result


def format_belarus(data: dict[str, list[ByRate]], top: int = 5) -> str:
    blocks = ["🇧🇾 <b>Курсы в Минске (myfin.by)</b>"]
    icon = {"USD/RUB": "💵", "EUR/RUB": "💶"}
    for pair, rows in data.items():
        if not rows:
            continue
        sell_best = sorted(rows, key=lambda r: r.buy, reverse=True)[:top]   # продать
        buy_best = sorted(rows, key=lambda r: r.sell)[:top]                 # купить
        blocks.append(f"\n{icon.get(pair, '•')} <b>{pair}</b>")
        blocks.append("  <u>Продать</u> (банк покупает дороже):")
        blocks += [f"   • {r.buy:.4g} — {r.bank}" for r in sell_best]
        blocks.append("  <u>Купить</u> (банк продаёт дешевле):")
        blocks += [f"   • {r.sell:.4g} — {r.bank}" for r in buy_best]
    return "\n".join(blocks)
