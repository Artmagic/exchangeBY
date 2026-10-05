"""
Базовый слой для источников курсов.

Каждый источник (banki.ru, myfin, sravni, …) реализует класс-наследник Source
с асинхронным методом fetch(city, currency) -> list[Rate]. Агрегатор собирает
их параллельно и сводит в единый список.

Единая модель Rate — общий знаменатель для всех источников:
  • bank        — название банка (нормализуем для дедупа);
  • buy         — банк ПОКУПАЕТ у вас валюту (вам выгоден больший курс) → bid;
  • sell        — банк ПРОДАЁТ вам валюту (вам выгоден меньший курс)   → ask;
  • level       — 'office' (курс конкретного пункта) или 'bank' (один на банк);
  • source      — откуда данные (для провенанса);
  • address/metro/updated — опциональный контекст.
"""

from __future__ import annotations

import asyncio
import re
import unicodedata
from dataclasses import dataclass, field

# Глобальный лок на headless-браузер: одновременно работает только ОДИН Chromium
# (banki / sravni / investing). Держит пик памяти в ~1 экземпляр — критично для
# VPS с 1 ГБ ОЗУ, где два браузера разом дали бы OOM.
BROWSER_LOCK = asyncio.Semaphore(1)

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# ISO-коды валют (numeric) — пригодятся источникам с API.
ISO = {"USD": 840, "EUR": 978, "CNY": 156, "GBP": 826, "JPY": 392, "CHF": 756}


@dataclass
class Rate:
    bank: str
    buy: float
    sell: float
    currency: str = "USD"
    city: str = "moskva"
    source: str = ""
    level: str = "office"          # 'office' | 'bank'
    address: str = ""
    metro: str = ""
    updated: str = ""

    @property
    def bank_key(self) -> str:
        """Ключ для дедупа банков из разных источников."""
        return normalize_bank(self.bank)


# --- нормализация названий банков ------------------------------------------
# Разные источники пишут один банк по-разному: «ПСБ» / «Промсвязьбанк»,
# «Альфа-Банк» / «Альфа банк» / «АО Альфа-Банк». Сводим к единому ключу.
_ALIASES = {
    "промсвязьбанк": "псб",
    "сбербанк": "сбер",
    "тинькофф": "т-банк",
    "тинькофф банк": "т-банк",
    "втб банк": "втб",
}
_NOISE = re.compile(
    r"\b(пао|ао|оао|зао|ооо|нко|кб|акб|банк|bank|публичное|акционерное|общество)\b",
    re.I,
)


def normalize_bank(name: str) -> str:
    s = unicodedata.normalize("NFKC", name or "").lower().strip()
    s = s.replace("ё", "е").replace("«", "").replace("»", "").replace('"', "")
    s = re.sub(r"\s*\([^)]*\)", "", s)  # отрезаем «(бывш. …)», «(Вятка Банк)»
    s = _ALIASES.get(s, s)
    s = _NOISE.sub("", s)
    s = re.sub(r"[^a-zа-я0-9]+", " ", s).strip()
    s = re.sub(r"\s+", " ", s)
    return _ALIASES.get(s, s)


def to_rate(val) -> float | None:
    """Превращаем '79,80 ₽' / '79.8' / 79.8 в float, иначе None."""
    if val is None or isinstance(val, bool):
        return None
    if isinstance(val, (int, float)):
        f = float(val)
    else:
        m = re.search(r"\d{1,4}(?:[.,]\d{1,4})?", str(val).replace("\xa0", ""))
        if not m:
            return None
        f = float(m.group(0).replace(",", "."))
    return f if 1.0 < f < 100000 else None


class Source:
    """Базовый источник. Наследники задают name и реализуют fetch()."""

    name: str = "base"
    # источникам с антиботом нужен браузер; чистым API/HTML — нет
    needs_browser: bool = False

    async def fetch(self, city: str = "moskva", currency: str = "USD") -> list[Rate]:
        raise NotImplementedError
