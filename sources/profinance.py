"""
Курс рубля с profinance.ru — реалтайм USD/RUB, EUR/RUB, CNY/RUB (bid/ask).

Тянем котировки так (чистый HTTP, без браузера, только хост jq.profinance.ru):
  1) GET  jq.profinance.ru/.../q  — выдаёт SID сессии;
  2) POST jq.../q (тело — подписка на рублёвые пары с этим SID) — снапшот котировок
     строками вида: S=USD/RUB;TICK=USDRUB;B=<bid>;A=<ask>;NCH=<изм>;NCHP=<%>;T=<время>

Раньше подписку брали из www.profinance.ru/quotes/s.txt, но этот хост доступен
не со всех серверов — поэтому подписку на нужные пары формируем сами.

Значения B/A иногда со знаком (±) — это направление последнего тика, берём модуль.
Рублёвые пары изредка не попадают в первый снапшот, поэтому повторяем POST.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import httpx

from .base import UA

Q_URL = "https://jq.profinance.ru/html/htmlquotes/q"
# рублёвые пары (команда /profinance, /market)
PAIRS = {
    "USDRUB": "USD/RUB",
    "EURRUB": "EUR/RUB",
    "CNYRUB": "CNY/RUB",
    "TRYRUB": "TRY/RUB",
    "AEDRUB": "AED/RUB",
}
# пары с долларом — форекс (команда /forex)
FOREX = {
    "EURUSD": "EUR/USD",
    "GBPUSD": "GBP/USD",
    "USDJPY": "USD/JPY",
    "USDCNY": "USD/CNY",
}
_ICON = {
    "USDRUB": "💵", "EURRUB": "💶", "CNYRUB": "🇨🇳", "TRYRUB": "🇹🇷", "AEDRUB": "🇦🇪",
    "EURUSD": "🇪🇺", "GBPUSD": "🇬🇧", "USDJPY": "🇯🇵", "USDCNY": "🇨🇳",
}


@dataclass
class PfRate:
    bid: float
    ask: float
    change: str = ""       # знаковое изменение, напр. "0.330" / "-0.12"
    change_pct: str = ""   # напр. "0.43"
    time: str = ""


def _absf(x) -> float | None:
    try:
        return abs(float(str(x).replace("+", "").replace(",", ".")))
    except (ValueError, TypeError):
        return None


async def _fetch(pairs: dict[str, str]) -> dict[str, PfRate]:
    """Снимок котировок по заданным символам (с ретраем на TLS-сбой)."""
    last_exc = None
    for _ in range(3):
        try:
            return await _fetch_once(pairs)
        except Exception as e:
            last_exc = e
            await asyncio.sleep(1.5)
    raise last_exc


async def _fetch_once(pairs: dict[str, str]) -> dict[str, PfRate]:
    headers = {"User-Agent": UA, "Referer": "https://www.profinance.ru/quotes/"}
    async with httpx.AsyncClient(headers=headers, timeout=12) as client:
        sid = (await client.get(Q_URL)).text.strip()
        # подписку на нужные пары формируем сами (без www.profinance.ru)
        subs = "".join(f"S={t};" for t in pairs)
        body = f"1;SID={sid};B=;A=;NCH=;NCHP=;{subs}\n".encode()

        out: dict[str, PfRate] = {}
        for _ in range(3):  # пары иногда не в первом снапшоте
            if pairs.keys() <= out.keys():
                break
            resp = await client.post(Q_URL, content=body)
            for line in resp.text.splitlines():
                kv = dict(p.split("=", 1) for p in line.split(";") if "=" in p)
                tick = kv.get("TICK")
                if tick in pairs and tick not in out:
                    bid, ask = _absf(kv.get("B")), _absf(kv.get("A"))
                    if bid is None:
                        continue
                    out[tick] = PfRate(
                        bid=bid, ask=ask or bid,
                        change=kv.get("NCH", ""), change_pct=kv.get("NCHP", ""),
                        time=kv.get("T", ""),
                    )
    return out


async def fetch_profinance() -> dict[str, PfRate]:
    return await _fetch(PAIRS)


async def fetch_forex() -> dict[str, PfRate]:
    return await _fetch(FOREX)


def _fmt(v: float) -> str:
    if v >= 100:
        return f"{v:.3f}"
    if v >= 10:
        return f"{v:.4f}"
    return f"{v:.5f}"


def _format(data: dict[str, PfRate], pairs: dict[str, str], header: str) -> str:
    lines = [header]
    tm = ""
    for tick, pair in pairs.items():
        r = data.get(tick)
        if not r:
            continue
        tm = r.time or tm
        chg = r.change.lstrip("+")
        sign = "" if chg.startswith("-") else "+"
        lines.append(
            f"{_ICON.get(tick, '•')} {pair}: <b>{_fmt(r.bid)}</b> / {_fmt(r.ask)}  "
            f"{sign}{chg} ({sign}{r.change_pct}%)"
        )
    if tm:
        lines.append(f"🕒 {tm} МСК")
    return "\n".join(lines)


def format_profinance(data: dict[str, PfRate]) -> str:
    return _format(data, PAIRS, "📊 <b>Курс рубля</b> (Profinance, биржа)")


def format_forex(data: dict[str, PfRate]) -> str:
    return _format(data, FOREX, "💱 <b>Пары с долларом</b> (Profinance, биржа)")
