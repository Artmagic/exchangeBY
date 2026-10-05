"""
Новости для рассылки: profinance.ru (RSS) + kapitalist.finance (HTML).

profinance.ru отдаёт RSS-фиды: econom.xml / forex.xml / fond.xml — берём econom
(экономика, широкий поток) с заголовком, ссылкой и датой.
kapitalist.finance RSS не имеет — парсим заголовки статей с главной (/articles/...).

Возвращаем объединённый список свежих новостей (новые сверху).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime

import httpx
from selectolax.parser import HTMLParser

from .base import UA

PF_RSS = "https://www.profinance.ru/forex.xml"  # валютные новости/аналитика
KAP_URL = "https://kapitalist.finance"


@dataclass
class NewsItem:
    title: str
    url: str
    source: str
    ts: datetime | None = None  # время публикации (если известно)


def _clean(s: str) -> str:
    s = re.sub(r"<[^>]+>", "", s or "")
    return re.sub(r"\s+", " ", s).strip()


async def _profinance(client: httpx.AsyncClient) -> list[NewsItem]:
    r = await client.get(PF_RSS)
    r.raise_for_status()
    out: list[NewsItem] = []
    for block in re.findall(r"<item>(.*?)</item>", r.text, re.S):
        title = re.search(r"<title>(.*?)</title>", block, re.S)
        link = re.search(r"<link>(.*?)</link>", block, re.S)
        date = re.search(r"<pubDate>(.*?)</pubDate>", block, re.S)
        if not title:
            continue
        ts = None
        if date:
            try:
                ts = parsedate_to_datetime(date.group(1).strip())
            except Exception:
                ts = None
        out.append(NewsItem(
            title=_clean(title.group(1)),
            url=_clean(link.group(1)) if link else PF_RSS,
            source="profinance.ru",
            ts=ts,
        ))
    return out


async def _kapitalist(client: httpx.AsyncClient) -> list[NewsItem]:
    r = await client.get(KAP_URL)
    r.raise_for_status()
    tree = HTMLParser(r.text)
    out: list[NewsItem] = []
    seen: set = set()
    for a in tree.css("a[href*='/articles/']"):
        title = a.text(strip=True)
        href = a.attributes.get("href") or ""
        if len(title) < 25 or href in seen:
            continue
        seen.add(href)
        url = href if href.startswith("http") else KAP_URL + href
        out.append(NewsItem(title=title, url=url, source="kapitalist.finance"))
    return out


async def fetch_news(limit: int = 8) -> list[NewsItem]:
    """Свежие новости из обоих источников поровну (изоляция ошибок).

    Оба источника отдают новое сверху, поэтому берём верхние из каждого —
    это надёжнее сортировки по времени (у kapitalist даты нет).
    """
    headers = {"User-Agent": UA, "Accept-Language": "ru-RU,ru"}
    async with httpx.AsyncClient(headers=headers, timeout=20, follow_redirects=True) as c:
        pf, kap = [], []
        try:
            pf = await _profinance(c)
        except Exception:
            pass
        try:
            kap = await _kapitalist(c)
        except Exception:
            pass
    per = max(1, limit // 2)
    return pf[:per] + kap[:per]


def format_news(items: list[NewsItem], header: str = "📰 <b>Новости</b>") -> str:
    lines = [header, ""]
    for n in items:
        tm = f" <i>{n.ts.strftime('%H:%M')}</i>" if n.ts else ""
        lines.append(f"• <a href=\"{n.url}\">{n.title}</a>{tm}\n  <i>{n.source}</i>")
    return "\n".join(lines)
