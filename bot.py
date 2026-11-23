"""
Телеграм-бот: лучшие курсы наличной валюты по нескольким агрегаторам.

Данные собираются из всех источников (banki.ru, myfin.by, …) и сводятся к
лучшему предложению на банк. См. aggregator.py / sources/.

Команды:
  /start        — приветствие и помощь
  /rates        — топ банков по покупке И по продаже сразу
  /buy [EUR]    — где выгоднее КУПИТЬ валюту (банк продаёт дешевле)
  /sell [EUR]   — где выгоднее ПРОДАТЬ валюту (банк покупает дороже)
  /city moskva  — сменить город на лету
"""

import asyncio
import fcntl
import json
import logging
import os
import re
import time
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQuery,
    InlineQueryResultArticle,
    InlineQueryResultsButton,
    InputTextMessageContent,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

import config
from aggregator import get_top
from sources.belarus import fetch_belarus, format_belarus
from sources.cbr import fetch_cbr, format_cbr
from sources.news import fetch_news, format_news
from sources.cifra import fetch_cifra, format_cifra
from sources.profikassa import fetch_profikassa, format_profikassa
from sources.profinance import fetch_forex, format_forex
from sources.xe import fetch_xe, format_xe
from sources.investing import fetch_investing, format_investing
from sources.market import fetch_inr_rub, fetch_market, format_market
from sources.profinance import fetch_profinance, format_profinance
from sources.rapira import fetch_rapira_usdt, format_usdt
from sources.abcex import fetch_abcex_usdt, format_abcex

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

dp = Dispatcher()

# username бота (без @), узнаём при старте через get_me() — нужен, чтобы
# распознавать упоминание бота в групповых чатах ("@ИмяБота курсы").
BOT_USERNAME: str | None = None

# простое хранилище города в памяти процесса (на пользователя)
_user_city: dict[int, str] = {}

# --- кнопки -----------------------------------------------------------------
# Постоянная клавиатура под полем ввода. Тексты кнопок = ключи обработчиков.
BTN = {
    "rates": "🏦 Банки РФ",     # наличка в обменниках (покупка/продажа по банкам)
    "market": "📈 Рынок",       # рыночный/биржевой курс (profinance + investing)
    "cbr": "🏛 ЦБ РФ",
    "usdt": "💲 USDT",
    "xe": "🌐 xe.com",          # пары к доллару (EUR/GBP/CHF/CNY/INR)
    "by": "🇧🇾 Беларусь",
    "subs": "🔔 Подписки",
    "cifra": "🏦 Cifra",        # только для админов
    "vernadka": "🏢 Вернадка",  # только для админов
}
# ID пользователей, которым доступен брокер Cifra (и админ-команды)
ADMIN_IDS = {8762086055, 943531954}

_MAIN_ROWS = [
    [KeyboardButton(text=BTN["rates"]), KeyboardButton(text=BTN["cbr"])],
    [KeyboardButton(text=BTN["market"]), KeyboardButton(text=BTN["usdt"])],
    [KeyboardButton(text=BTN["xe"]), KeyboardButton(text=BTN["by"])],
    [KeyboardButton(text=BTN["subs"])],
]
MAIN_KB = ReplyKeyboardMarkup(
    keyboard=_MAIN_ROWS, resize_keyboard=True, is_persistent=True
)
# у админов — дополнительные кнопки брокеров
ADMIN_KB = ReplyKeyboardMarkup(
    keyboard=_MAIN_ROWS
    + [[KeyboardButton(text=BTN["cifra"]), KeyboardButton(text=BTN["vernadka"])]],
    resize_keyboard=True,
    is_persistent=True,
)


def _kb_for(uid: int) -> ReplyKeyboardMarkup:
    return ADMIN_KB if uid in ADMIN_IDS else MAIN_KB

# Инлайн-меню раздела «Подписки».
SUBS_MENU = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="🔔 Подписка РФ", callback_data="menu:sub")],
        [InlineKeyboardButton(text="🇧🇾 Подписка Беларусь", callback_data="menu:sub_by")],
        [InlineKeyboardButton(text="📊 Статус / отписаться", callback_data="menu:status")],
    ]
)

# --- подписки на рассылку ---------------------------------------------------
# Хранятся в JSON рядом с ботом, чтобы переживать перезапуск.
# Формат: {user_id: {"interval": минуты, "currency": "USD", "city": "moskva",
#                     "next_run": epoch_seconds}}
SUBS_FILE = Path(__file__).with_name("subscriptions.json")

# варианты периодичности для инлайн-меню: (подпись, минуты)
INTERVALS = [
    ("Каждый час", 60),
    ("Каждые 3 часа", 180),
    ("Каждые 6 часов", 360),
    ("Раз в день", 1440),
]
_INTERVAL_LABEL = {m: lbl for lbl, m in INTERVALS}


def _load_subs() -> dict[int, dict]:
    try:
        raw = json.loads(SUBS_FILE.read_text(encoding="utf-8"))
        return {int(k): v for k, v in raw.items()}
    except (FileNotFoundError, ValueError):
        return {}


def _save_subs() -> None:
    SUBS_FILE.write_text(
        json.dumps(_subs, ensure_ascii=False, indent=2), encoding="utf-8"
    )


_subs: dict[int, dict] = _load_subs()

# --- отдельная подписка на курсы Беларуси (Минск, USD/RUB, EUR/RUB) ----------
# Формат: {user_id: {"interval": минуты, "next_run": epoch}}
BY_SUBS_FILE = Path(__file__).with_name("subscriptions_by.json")


def _load_by_subs() -> dict[int, dict]:
    try:
        raw = json.loads(BY_SUBS_FILE.read_text(encoding="utf-8"))
        return {int(k): v for k, v in raw.items()}
    except (FileNotFoundError, ValueError):
        return {}


def _save_by_subs() -> None:
    BY_SUBS_FILE.write_text(
        json.dumps(_by_subs, ensure_ascii=False, indent=2), encoding="utf-8"
    )


_by_subs: dict[int, dict] = _load_by_subs()

# --- общий стейт (последняя увиденная дата курсов ЦБ для авто-пуша) ----------
STATE_FILE = Path(__file__).with_name("state.json")


def _load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def _save_state() -> None:
    STATE_FILE.write_text(json.dumps(_state, ensure_ascii=False), encoding="utf-8")


_state: dict = _load_state()

# --- учёт пользователей -----------------------------------------------------
# Каждое взаимодействие (сообщение/кнопка/inline) записывает автора в users.json:
# id, username, имя, когда впервые и последний раз обращались.
USERS_FILE = Path(__file__).with_name("users.json")


def _load_users() -> dict[str, dict]:
    try:
        return json.loads(USERS_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def _save_users() -> None:
    USERS_FILE.write_text(
        json.dumps(_users, ensure_ascii=False, indent=2), encoding="utf-8"
    )


_users: dict[str, dict] = _load_users()


def _record_user(u) -> None:
    if not u or getattr(u, "is_bot", False):
        return
    uid = str(u.id)
    now = time.strftime("%Y-%m-%d %H:%M")
    rec = _users.get(uid, {})
    rec.update({
        "id": u.id,
        "username": u.username,
        "name": " ".join(p for p in (u.first_name, u.last_name) if p),
    })
    rec.setdefault("first_seen", now)
    prev_seen = rec.get("last_seen")
    rec["last_seen"] = now
    _users[uid] = rec
    # пишем на диск не чаще раза в минуту на пользователя
    if prev_seen != now:
        _save_users()


@dp.update.outer_middleware()
async def _track_user(handler, event, data):
    try:
        _record_user(data.get("event_from_user"))
    except Exception:
        logger.exception("Не удалось записать пользователя")
    return await handler(event, data)


@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "👋 Привет! Я показываю лучшие курсы наличной валюты по нескольким агрегаторам.\n\n"
        "Команды:\n"
        f"• <b>/rates</b> — топ-{config.TOP_N} банков по покупке и по продаже\n"
        "• <b>/rates_full</b> — то же + адреса и метро лучших пунктов\n"
        "• <b>/buy</b> — где выгоднее <b>купить</b> валюту (можно <code>/buy EUR</code>)\n"
        "• <b>/sell</b> — где выгоднее <b>продать</b> валюту\n"
        "• <b>/cbr</b> — официальные курсы ЦБ (USD/EUR/CNY/INR/TRY)\n"
        "• <b>/usdt</b> — курс USDT/RUB (Rapira + ABCEX)\n"
        "• <b>/market</b> — курс рубля: profinance + investing вместе\n"
        "• <b>/profinance</b> — биржевой курс (bid/ask)\n"
        "• <b>/investing</b> — рыночный курс с investing.com\n"
        "• <b>/forex</b> — пары с долларом (EUR/USD, USD/CNY, USD/JPY, GBP/USD)\n"
        "• <b>/by</b> — курсы в Минске (USD/RUB, EUR/RUB)\n"
        "• <b>/subscribe</b> — рассылка РФ: банки по расписанию, USDT раз в час, ЦБ при обновлении\n"
        "• <b>/subscribe_by</b> — отдельная рассылка курсов Беларуси\n"
        "• <b>/city moskva</b> — сменить город\n\n"
        "Внизу — кнопки для быстрого доступа. 👇\n"
        f"Сейчас: город <b>{config.CITY_SLUG}</b>, валюта <b>{config.CURRENCY}</b>.",
        reply_markup=_kb_for(message.from_user.id),
    )


@dp.message(Command("city"))
async def cmd_city(message: Message, command: CommandObject):
    city = (command.args or "").strip().lower()
    if not city:
        await message.answer("Укажите город: <code>/city sankt-peterburg</code>")
        return
    _user_city[message.from_user.id] = city
    await message.answer(f"✅ Город установлен: <b>{city}</b>")


@dp.message(Command("users"))
async def cmd_users(message: Message):
    """Список всех, кто взаимодействовал с ботом (только для админов)."""
    if message.from_user.id not in ADMIN_IDS:
        return
    if not _users:
        await message.answer("Пока никого нет.")
        return
    rows = sorted(_users.values(), key=lambda r: r.get("last_seen", ""), reverse=True)
    lines = [f"👥 <b>Пользователи: {len(rows)}</b>\n"]
    for r in rows:
        un = f"@{r['username']}" if r.get("username") else "—"
        name = r.get("name") or ""
        lines.append(f"• {un} · {name} · <code>{r['id']}</code> · {r.get('last_seen','')}")
    text = "\n".join(lines)
    # режем под лимит Telegram
    for i in range(0, len(text), 3900):
        await message.answer(text[i:i + 3900])


async def _send_cifra(message: Message):
    """Курсы брокера cifra.by — только для админов."""
    if message.from_user.id not in ADMIN_IDS:
        return
    status = await message.answer("⏳ cifra.by…")
    try:
        rows = await fetch_cifra()
    except Exception as e:
        logger.exception("Cifra")
        await status.edit_text(f"⚠️ cifra.by недоступен: {e}")
        return
    await status.edit_text(format_cifra(rows) if rows else "😕 cifra.by не отдал данные.")


@dp.message(Command("cifra"))
async def cmd_cifra(message: Message):
    await _send_cifra(message)


@dp.message(F.text == BTN["cifra"])
async def btn_cifra(message: Message):
    await _send_cifra(message)


async def _send_vernadka(message: Message):
    """Курсы обменника profikassa.ru («Вернадка») — только для админов."""
    if message.from_user.id not in ADMIN_IDS:
        return
    status = await message.answer("⏳ Вернадка…")
    try:
        rows = await fetch_profikassa()
    except Exception as e:
        logger.exception("Profikassa")
        await status.edit_text(f"⚠️ profikassa.ru недоступен: {e}")
        return
    await status.edit_text(format_profikassa(rows) if rows else "😕 Нет данных.")


@dp.message(Command("vernadka"))
async def cmd_vernadka(message: Message):
    await _send_vernadka(message)


@dp.message(F.text == BTN["vernadka"])
async def btn_vernadka(message: Message):
    await _send_vernadka(message)


def _currency_of(command: CommandObject) -> str:
    return (command.args or config.CURRENCY).strip().upper()


async def _load(message: Message, currency: str):
    """Общая загрузка: город из настроек пользователя, валюта передаётся явно.

    Возвращает (currency, city, status_msg, top_bid, top_ask, meta) либо
    (None, ...) если случилась ошибка/пусто — тогда сообщение уже отправлено.
    """
    currency = currency.strip().upper()
    city = _user_city.get(message.from_user.id, config.CITY_SLUG)
    status = await message.answer(f"⏳ Собираю курсы {currency} ({city}) по агрегаторам…")

    try:
        top_bid, top_ask, meta = await get_top(
            city=city, currency=currency, top=config.TOP_N, cache_ttl=config.CACHE_TTL
        )
    except Exception as e:
        logger.exception("Ошибка сбора")
        await status.edit_text(f"⚠️ Не удалось получить данные: {e}")
        return None, city, status, None, None, None

    if not top_bid and not top_ask:
        await status.edit_text(
            "😕 Список пуст. Возможно, изменились источники "
            "или сработала антибот-защита. Проверьте логи."
        )
        return None, city, status, None, None, None

    return currency, city, status, top_bid, top_ask, meta


def _line(i: int, bank: str, rate: float, where: str, srcs: tuple, full: bool) -> str:
    tag = "✓✓" if len(srcs) > 1 else ""
    loc = f"\n   📍 {where}" if (full and where) else ""
    return f"{i}. 🏦 <b>{bank}</b> — {rate:.2f} ₽ {tag}{loc}"


def _bid_block(currency, city, top_bid, meta, full=False) -> str:
    body = "\n".join(
        _line(i, b.bank, b.buy, b.buy_where, b.sources, full)
        for i, b in enumerate(top_bid, 1)
    )
    return (
        f"💰 <b>{currency} — {city}</b>\n"
        f"<b>ТОП-{len(top_bid)} ПРОДАЖА вами</b> (банк покупает у вас дороже всех):\n\n"
        + body
    )


def _ask_block(currency, city, top_ask, meta, full=False) -> str:
    body = "\n".join(
        _line(i, a.bank, a.sell, a.sell_where, a.sources, full)
        for i, a in enumerate(top_ask, 1)
    )
    return (
        f"🛒 <b>{currency} — {city}</b>\n"
        f"<b>ТОП-{len(top_ask)} ПОКУПКА вами</b> (банк продаёт вам дешевле всех):\n\n"
        + body
    )


# --- ядра (используются и командами, и кнопками) ----------------------------
async def _do_rates(message: Message, currency: str, full: bool = False):
    currency, city, status, top_bid, top_ask, meta = await _load(message, currency)
    if currency is None:
        return
    await status.edit_text(_bid_block(currency, city, top_bid, meta, full))
    await message.answer(_ask_block(currency, city, top_ask, meta, full))


async def _do_buy(message: Message, currency: str):
    currency, city, status, _, top_ask, meta = await _load(message, currency)
    if currency is None:
        return
    await status.edit_text(_ask_block(currency, city, top_ask, meta))


async def _do_sell(message: Message, currency: str):
    currency, city, status, top_bid, _, meta = await _load(message, currency)
    if currency is None:
        return
    await status.edit_text(_bid_block(currency, city, top_bid, meta))


@dp.message(Command("rates"))
async def cmd_rates(message: Message, command: CommandObject):
    await _do_rates(message, _currency_of(command))


@dp.message(Command("rates_full"))
async def cmd_rates_full(message: Message, command: CommandObject):
    """То же, что /rates, но с адресами и метро лучших пунктов."""
    await _do_rates(message, _currency_of(command), full=True)


@dp.message(Command("buy"))
async def cmd_buy(message: Message, command: CommandObject):
    """Где выгоднее КУПИТЬ валюту — банк продаёт дешевле всех (ask)."""
    await _do_buy(message, _currency_of(command))


@dp.message(Command("sell"))
async def cmd_sell(message: Message, command: CommandObject):
    """Где выгоднее ПРОДАТЬ валюту — банк покупает дороже всех (bid)."""
    await _do_sell(message, _currency_of(command))


# --- курсы ЦБ и USDT --------------------------------------------------------
@dp.message(Command("cbr"))
async def cmd_cbr(message: Message):
    """Официальные курсы ЦБ РФ (USD/EUR/CNY/INR/TRY) на сегодня и вчера."""
    try:
        c = await fetch_cbr()
    except Exception as e:
        logger.exception("ЦБ")
        await message.answer(f"⚠️ Не удалось получить курсы ЦБ: {e}")
        return
    await message.answer(format_cbr(c))


async def _usdt_combined() -> str:
    """USDT/RUB с нескольких бирж: Rapira + ABCEX (ошибка одной не роняет вывод)."""
    parts: list[str] = []
    try:
        u = await fetch_rapira_usdt()
        if u:
            parts.append(format_usdt(u))
    except Exception:
        logger.exception("Rapira")
    try:
        a = await fetch_abcex_usdt()
        if a:
            parts.append(format_abcex(a))
    except Exception:
        logger.exception("ABCEX")
    return "\n\n".join(parts)


@dp.message(Command("usdt"))
async def cmd_usdt(message: Message):
    """Курс USDT/RUB с бирж Rapira и ABCEX."""
    text = await _usdt_combined()
    await message.answer(text or "😕 Биржи не вернули пару USDT/RUB.")


async def _profinance_text() -> str:
    """profinance (USD/EUR/CNY/TRY, bid/ask) + строка INR/RUB (mid-market).

    Рупии в биржевом фиде нет, поэтому добавляем её отдельной mid-market строкой.
    """
    try:
        d = await fetch_profinance()
    except Exception:
        logger.exception("Profinance")
        return ""
    if not d:
        return ""
    text = format_profinance(d)
    try:
        inr = await fetch_inr_rub()
        if inr:
            text += f"\n🇮🇳 INR/RUB: <b>{inr:.3f}</b> ₽ <i>(mid-market)</i>"
    except Exception:
        logger.exception("INR mid-market")
    return text


async def _market_text() -> str:
    """Оба источника курса рубля вместе: profinance (+рупия) + investing.

    Забираем параллельно; каждый изолирован. Если оба молчат — mid-market er-api.
    """

    async def pf():
        return await _profinance_text()

    async def inv():
        try:
            d = await fetch_investing()
            return format_investing(d) if d else ""
        except Exception:
            logger.exception("Investing")
            return ""

    pf_txt, inv_txt = await asyncio.gather(pf(), inv())
    parts = [t for t in (pf_txt, inv_txt) if t]
    if parts:
        return "\n\n".join(parts)
    # запасной вариант — mid-market
    try:
        m = await fetch_market()
        return format_market(m) if m else ""
    except Exception:
        logger.exception("Market fallback")
        return ""


@dp.message(Command("market"))
async def cmd_market(message: Message):
    """Курс рубля из двух источников: profinance (bid/ask) + investing."""
    status = await message.answer("⏳ Беру курс рубля…")
    text = await _market_text()
    await status.edit_text(text or "😕 Источники курса недоступны.")


@dp.message(Command("profinance"))
async def cmd_profinance(message: Message):
    """Биржевой курс рубля с profinance.ru (bid/ask) + рупия (mid-market)."""
    status = await message.answer("⏳ profinance…")
    text = await _profinance_text()
    await status.edit_text(text or "😕 profinance не отдал данные.")


@dp.message(Command("investing"))
async def cmd_investing(message: Message):
    """Рыночный курс рубля с investing.com."""
    status = await message.answer("⏳ investing.com…")
    try:
        d = await fetch_investing()
    except Exception as e:
        logger.exception("Investing")
        await status.edit_text(f"⚠️ investing недоступен: {e}")
        return
    await status.edit_text(format_investing(d) if d else "😕 investing не отдал данные.")


@dp.message(Command("forex"))
async def cmd_forex(message: Message):
    """Пары с долларом: биржа (profinance) + xe.com (EUR/USD, CNY/USD)."""
    status = await message.answer("⏳ Форекс…")
    parts: list[str] = []
    try:
        d = await fetch_forex()
        if d:
            parts.append(format_forex(d))
    except Exception:
        logger.exception("Forex profinance")
    try:
        x = await fetch_xe()
        if x:
            parts.append(format_xe(x))
    except Exception:
        logger.exception("Forex xe")
    await status.edit_text("\n\n".join(parts) if parts else "😕 Нет данных.")


# --- курсы Беларуси ---------------------------------------------------------
@dp.message(Command("by"))
async def cmd_by(message: Message):
    """Курсы USD/RUB и EUR/RUB в банках Минска (myfin.by)."""
    status = await message.answer("⏳ Собираю курсы Минска…")
    try:
        data = await fetch_belarus()
    except Exception as e:
        logger.exception("Беларусь")
        await status.edit_text(f"⚠️ Не удалось получить курсы Беларуси: {e}")
        return
    if not any(data.values()):
        await status.edit_text("😕 Пусто — возможно, изменилась вёрстка myfin.by.")
        return
    await status.edit_text(format_belarus(data))


def _by_sub_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=lbl, callback_data=f"bysub:{m}")]
            for lbl, m in INTERVALS
        ]
        + [[InlineKeyboardButton(text="❌ Отписаться", callback_data="byunsub")]]
    )


@dp.message(Command("subscribe_by"))
async def cmd_subscribe_by(message: Message):
    """Инлайн-меню периодичности для рассылки курсов Беларуси."""
    await message.answer(
        "🇧🇾 Как часто присылать курсы Беларуси (Минск)?", reply_markup=_by_sub_kb()
    )


@dp.callback_query(F.data.startswith("bysub:"))
async def on_subscribe_by(cb: CallbackQuery):
    minutes = int(cb.data.split(":")[1])
    _by_subs[cb.from_user.id] = {
        "interval": minutes,
        "next_run": time.time() + minutes * 60,
    }
    _save_by_subs()
    await cb.message.edit_text(
        f"✅ Подписка на курсы Беларуси: <b>{_INTERVAL_LABEL.get(minutes, f'{minutes} мин')}</b>.\n"
        "Отменить — /unsubscribe_by."
    )
    await cb.answer("Готово")


@dp.callback_query(F.data == "byunsub")
async def on_unsub_by_button(cb: CallbackQuery):
    existed = _by_subs.pop(cb.from_user.id, None)
    _save_by_subs()
    await cb.message.edit_text(
        "🔕 Подписка на Беларусь отменена." if existed else "Активной подписки не было."
    )
    await cb.answer()


@dp.message(Command("unsubscribe_by"))
async def cmd_unsubscribe_by(message: Message):
    existed = _by_subs.pop(message.from_user.id, None)
    _save_by_subs()
    await message.answer(
        "🔕 Подписка на Беларусь отменена." if existed else "У вас нет подписки на Беларусь."
    )


async def _push_belarus(bot: Bot, uid: int) -> None:
    data = await fetch_belarus()
    if not any(data.values()):
        return
    await bot.send_message(uid, "🔔 <b>Курсы Беларуси</b>\n" + format_belarus(data))


# --- подписка ---------------------------------------------------------------
def _sub_kb(currency: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=lbl, callback_data=f"sub:{m}:{currency}")]
            for lbl, m in INTERVALS
        ]
        + [[InlineKeyboardButton(text="❌ Отписаться", callback_data="unsub")]]
    )


@dp.message(Command("subscribe"))
async def cmd_subscribe(message: Message, command: CommandObject):
    """Показываем инлайн-меню выбора периодичности.

    Валюта берётся из аргумента (/subscribe EUR) или из конфига, город — из
    настройки пользователя. Они «замораживаются» в подписке при выборе.
    """
    currency = (command.args or config.CURRENCY).strip().upper()
    city = _user_city.get(message.from_user.id, config.CITY_SLUG)
    await message.answer(
        f"🔔 Как часто присылать курсы <b>{currency}</b> ({city})?",
        reply_markup=_sub_kb(currency),
    )


@dp.callback_query(F.data.startswith("sub:"))
async def on_subscribe(cb: CallbackQuery):
    _, minutes, currency = cb.data.split(":")
    minutes = int(minutes)
    uid = cb.from_user.id
    _subs[uid] = {
        "interval": minutes,
        "currency": currency,
        "city": _user_city.get(uid, config.CITY_SLUG),
        "next_run": time.time() + minutes * 60,
    }
    _save_subs()
    await cb.message.edit_text(
        f"✅ Подписка оформлена: <b>{_INTERVAL_LABEL.get(minutes, f'{minutes} мин')}</b>, "
        f"{currency} ({_subs[uid]['city']}).\nОтменить — /unsubscribe."
    )
    await cb.answer("Готово")


@dp.callback_query(F.data == "unsub")
async def on_unsub_button(cb: CallbackQuery):
    existed = _subs.pop(cb.from_user.id, None)
    _save_subs()
    await cb.message.edit_text(
        "🔕 Подписка отменена." if existed else "У вас не было активной подписки."
    )
    await cb.answer()


@dp.message(Command("unsubscribe"))
async def cmd_unsubscribe(message: Message):
    existed = _subs.pop(message.from_user.id, None)
    _save_subs()
    await message.answer(
        "🔕 Подписка отменена." if existed else "У вас нет активной подписки."
    )


async def _push(bot: Bot, uid: int, sub: dict) -> None:
    """Отправить плановое обновление курсов одному подписчику."""
    bid, ask, meta = await get_top(
        city=sub["city"], currency=sub["currency"],
        top=config.TOP_N, cache_ttl=config.CACHE_TTL,
    )
    if not bid and not ask:
        return
    await bot.send_message(
        uid, "🔔 <b>Плановое обновление курсов</b>\n" + _bid_block(sub["currency"], sub["city"], bid, meta)
    )
    await bot.send_message(uid, _ask_block(sub["currency"], sub["city"], ask, meta))


async def _check_cbr(bot: Bot) -> None:
    """Появились новые курсы ЦБ? Если дата сменилась — рассылаем подписчикам.

    Первую увиденную дату только запоминаем (без пуша), чтобы не слать
    «новые» курсы на старте; шлём только при последующих изменениях.
    """
    try:
        c = await fetch_cbr()
    except Exception:
        logger.exception("Проверка курсов ЦБ")
        return
    prev_date = _state.get("cbr_date")
    if c.date == prev_date:
        return
    _state["cbr_date"] = c.date
    _save_state()
    if not prev_date:
        return  # первичная инициализация — без рассылки

    text = "🆕 <b>Появились новые курсы ЦБ!</b>\n\n" + format_cbr(c)
    for uid in list(_subs):
        try:
            await bot.send_message(uid, text)
        except Exception:
            logger.exception("Авто-пуш ЦБ %s", uid)


@dp.message(Command("news"))
async def cmd_news(message: Message):
    """Свежие финансовые новости — только для админов."""
    if message.from_user.id not in ADMIN_IDS:
        return
    status = await message.answer("⏳ Новости…")
    try:
        items = await fetch_news(limit=8)
    except Exception as e:
        logger.exception("Новости")
        await status.edit_text(f"⚠️ Не удалось получить новости: {e}")
        return
    await status.edit_text(format_news(items) if items else "😕 Новостей нет.")


NEWS_EVERY = 7200  # проверка новостей раз в 2 часа


async def _push_news(bot: Bot) -> None:
    """Рассылка ТОЛЬКО админам (ADMIN_IDS) и ТОЛЬКО новых заголовков."""
    if not ADMIN_IDS:
        return
    try:
        items = await fetch_news(limit=10)
    except Exception:
        logger.exception("Рассылка новостей")
        return
    if not items:
        return
    seen = list(_state.get("news_seen", []))
    seen_set = set(seen)
    fresh = [n for n in items if n.url not in seen_set]
    # обновляем список виденного (последние ~60 ссылок)
    merged = [n.url for n in items] + [u for u in seen if u not in {n.url for n in items}]
    _state["news_seen"] = merged[:60]
    _save_state()
    if not seen:          # первый запуск — только запоминаем, без рассылки
        return
    if not fresh:
        return
    text = format_news(fresh, header="📰 <b>Свежие новости</b>")
    for uid in ADMIN_IDS:
        try:
            await bot.send_message(uid, text)
        except Exception:
            logger.exception("Пуш новостей %s", uid)


async def _push_rapira(bot: Bot) -> None:
    """Ежечасная рассылка курса USDT/RUB (Rapira + ABCEX) всем подписчикам."""
    if not _subs:
        return
    text = await _usdt_combined()
    if not text:
        return
    for uid in list(_subs):
        try:
            await bot.send_message(uid, text)
        except Exception:
            logger.exception("Пуш USDT %s", uid)


RAPIRA_EVERY = 3600   # раз в час


async def scheduler(bot: Bot) -> None:
    """Тикаем раз в полминуты:
    1) плановые рассылки банковских курсов (по интервалу подписки);
    2) курс Rapira USDT — фиксированно раз в час;
    3) курсы ЦБ — только при публикации новой даты (проверка раз в 10 мин).
    """
    last_cbr_poll = 0.0
    last_rapira = _state.get("rapira_ts", 0.0)
    last_news = _state.get("news_ts", 0.0)
    last_inline = 0.0
    while True:
        now = time.time()
        # 0) прогрев кэша для inline-режима — сразу и далее раз в 5 минут
        if now - last_inline > 300:
            last_inline = now
            await refresh_inline_cache()
        # 1) плановые рассылки банковских курсов (Москва/РФ)
        for uid, sub in list(_subs.items()):
            if sub.get("next_run", 0) <= now:
                try:
                    await _push(bot, uid, sub)
                except Exception:
                    logger.exception("Не удалось отправить рассылку %s", uid)
                sub["next_run"] = now + sub["interval"] * 60
                _save_subs()
        # 1b) отдельная рассылка курсов Беларуси
        for uid, sub in list(_by_subs.items()):
            if sub.get("next_run", 0) <= now:
                try:
                    await _push_belarus(bot, uid)
                except Exception:
                    logger.exception("Рассылка Беларуси %s", uid)
                sub["next_run"] = now + sub["interval"] * 60
                _save_by_subs()
        # 2) Rapira USDT — раз в час
        if now - last_rapira >= RAPIRA_EVERY:
            last_rapira = now
            _state["rapira_ts"] = now
            _save_state()
            await _push_rapira(bot)
        # 3) новые курсы ЦБ — проверка не чаще раза в 10 минут
        if now - last_cbr_poll > 600:
            last_cbr_poll = now
            await _check_cbr(bot)
        # 4) новости — раз в 2 часа (только новые заголовки)
        if now - last_news >= NEWS_EVERY:
            last_news = now
            _state["news_ts"] = now
            _save_state()
            await _push_news(bot)
        await asyncio.sleep(30)


# --- обработка нажатий кнопок постоянной клавиатуры --------------------------
@dp.message(F.text == BTN["rates"])
async def btn_rates(message: Message):
    await _do_rates(message, config.CURRENCY)


@dp.message(F.text == BTN["market"])
async def btn_market(message: Message):
    await cmd_market(message)


@dp.message(F.text == BTN["cbr"])
async def btn_cbr(message: Message):
    await cmd_cbr(message)


@dp.message(F.text == BTN["usdt"])
async def btn_usdt(message: Message):
    await cmd_usdt(message)


@dp.message(F.text == BTN["xe"])
async def btn_xe(message: Message):
    status = await message.answer("⏳ xe.com…")
    try:
        x = await fetch_xe()
    except Exception:
        logger.exception("Button xe")
        x = None
    await status.edit_text(format_xe(x) if x else "😕 Нет данных.")


@dp.message(F.text == BTN["by"])
async def btn_by(message: Message):
    await cmd_by(message)


@dp.message(F.text == BTN["subs"])
async def btn_subs(message: Message):
    await message.answer("🔔 Управление рассылками:", reply_markup=SUBS_MENU)


# --- inline-режим (@ИмяБота в любом чате, без добавления бота) ---------------
# Inline-ответ должен быть быстрым, а сбор курсов долгий → держим готовые
# тексты в кэше, который фоново обновляет планировщик.
_inline_cache: dict[str, str] = {}

# (ключ, заголовок, описание) — что показывать в списке inline-результатов
INLINE_ITEMS = [
    ("rates", "🇷🇺 Курсы РФ", "банки: топ покупка/продажа"),
    ("market", "📈 Курс ₽", "биржевой USD/EUR/CNY к рублю (bid/ask)"),
    ("cbr", "🏛 ЦБ РФ", "офиц. курсы USD/EUR/CNY/INR/TRY"),
    ("usdt", "💲 USDT", "USDT/RUB: Rapira + ABCEX"),
    ("by", "🇧🇾 Беларусь", "Минск: USD/RUB, EUR/RUB"),
]


async def refresh_inline_cache() -> None:
    """Фоново пересобираем готовые тексты для inline и авто-рассылок."""

    async def rates_text():
        bid, ask, meta = await get_top(
            config.CITY_SLUG, config.CURRENCY, top=config.TOP_N, cache_ttl=config.CACHE_TTL
        )
        return (
            _bid_block(config.CURRENCY, config.CITY_SLUG, bid, meta)
            + "\n\n"
            + _ask_block(config.CURRENCY, config.CITY_SLUG, ask, meta)
        )

    async def cbr_text():
        return format_cbr(await fetch_cbr())

    async def usdt_text():
        return await _usdt_combined()

    async def market_text():
        return await _market_text()

    async def by_text():
        return format_belarus(await fetch_belarus())

    async def safe(key, coro):
        try:
            txt = await coro()
            if txt:
                _inline_cache[key] = txt
        except Exception:
            logger.exception("inline refresh %s", key)

    await asyncio.gather(
        safe("rates", rates_text),
        safe("cbr", cbr_text),
        safe("usdt", usdt_text),
        safe("market", market_text),
        safe("by", by_text),
    )


@dp.inline_query()
async def on_inline(query: InlineQuery):
    q = query.query.strip().lower()
    results = []
    for key, title, desc in INLINE_ITEMS:
        if q and q not in title.lower() and q not in key and q not in desc.lower():
            continue
        text = _inline_cache.get(key) or "⏳ Курсы ещё обновляются, попробуйте через минуту."
        results.append(
            InlineQueryResultArticle(
                id=key,
                title=title,
                description=desc,
                input_message_content=InputTextMessageContent(
                    message_text=text, parse_mode="HTML"
                ),
            )
        )
    # кнопка-шапка над результатами (ведёт в личку бота) — как у @suanbot
    await query.answer(
        results,
        cache_time=30,
        is_personal=False,
        button=InlineQueryResultsButton(text="💱 Открыть бота / помощь", start_parameter="from_inline"),
    )


# --- упоминание бота в групповом чате ---------------------------------------
# "@ИмяБота" один — топ покупка+продажа; "@ИмяБота цб" / "usdt" / "market" /
# "беларусь" / "купить" / "продать" — соответствующий раздел. Это даёт удобный
# способ дёрнуть курсы в общем чате, просто написав сообщение с упоминанием.
def _mention_query(text: str | None) -> str | None:
    """Если текст содержит @упоминание бота — возвращает остаток текста."""
    if not BOT_USERNAME or not text or "@" + BOT_USERNAME.lower() not in text.lower():
        return None
    pattern = re.compile(rf"@{re.escape(BOT_USERNAME)}", re.IGNORECASE)
    return pattern.sub("", text).strip()


@dp.message(F.text.func(lambda t: _mention_query(t) is not None))
async def on_mention(message: Message):
    query = (_mention_query(message.text) or "").lower()
    if any(k in query for k in ("куп", "buy")):
        await _do_buy(message, config.CURRENCY)
    elif any(k in query for k in ("прода", "sell")):
        await _do_sell(message, config.CURRENCY)
    elif any(k in query for k in ("цб", "cbr")):
        await cmd_cbr(message)
    elif any(k in query for k in ("usdt", "rapira", "рапир")):
        await cmd_usdt(message)
    elif any(k in query for k in ("market", "invest", "рыноч")):
        await cmd_market(message)
    elif any(k in query for k in ("belarus", "беларус", "минск", "by")):
        await cmd_by(message)
    else:
        await _do_rates(message, config.CURRENCY)


# --- инлайн-меню «Подписки» -------------------------------------------------
@dp.callback_query(F.data == "menu:sub")
async def menu_sub(cb: CallbackQuery):
    await cb.message.edit_text(
        f"🔔 Как часто присылать курсы <b>{config.CURRENCY}</b> (РФ)?",
        reply_markup=_sub_kb(config.CURRENCY),
    )
    await cb.answer()


@dp.callback_query(F.data == "menu:sub_by")
async def menu_sub_by(cb: CallbackQuery):
    await cb.message.edit_text(
        "🇧🇾 Как часто присылать курсы Беларуси (Минск)?", reply_markup=_by_sub_kb()
    )
    await cb.answer()


@dp.callback_query(F.data == "menu:status")
async def menu_status(cb: CallbackQuery):
    uid = cb.from_user.id
    rf = _subs.get(uid)
    by = _by_subs.get(uid)
    rf_txt = (
        f"включена ({_INTERVAL_LABEL.get(rf['interval'], str(rf['interval']) + ' мин')}, "
        f"{rf['currency']})"
        if rf else "выключена"
    )
    by_txt = (
        _INTERVAL_LABEL.get(by["interval"], str(by["interval"]) + " мин")
        if by else "выключена"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отписаться РФ", callback_data="unsub")],
            [InlineKeyboardButton(text="❌ Отписаться Беларусь", callback_data="byunsub")],
        ]
    )
    await cb.message.edit_text(
        f"📊 <b>Статус подписок</b>\n• РФ: {rf_txt}\n• Беларусь: {by_txt}",
        reply_markup=kb,
    )
    await cb.answer()


COMMANDS = [
    BotCommand(command="rates", description="Топ банков: покупка и продажа сразу"),
    BotCommand(command="rates_full", description="То же + адреса и метро пунктов"),
    BotCommand(command="buy", description="Где выгоднее купить валюту"),
    BotCommand(command="sell", description="Где выгоднее продать валюту"),
    BotCommand(command="cbr", description="Курсы ЦБ РФ: USD/EUR/CNY/INR/TRY"),
    BotCommand(command="usdt", description="Курс USDT/RUB: Rapira + ABCEX"),
    BotCommand(command="market", description="Курс рубля: profinance + investing"),
    BotCommand(command="profinance", description="Биржевой курс рубля (profinance, bid/ask)"),
    BotCommand(command="investing", description="Рыночный курс рубля (investing.com)"),
    BotCommand(command="forex", description="Пары с долларом: EUR/USD, USD/CNY, USD/JPY, GBP/USD"),
    BotCommand(command="by", description="Курсы в Минске: USD/RUB, EUR/RUB"),
    BotCommand(command="subscribe", description="Подписка на курсы РФ"),
    BotCommand(command="unsubscribe", description="Отменить подписку РФ"),
    BotCommand(command="subscribe_by", description="Подписка на курсы Беларуси"),
    BotCommand(command="unsubscribe_by", description="Отменить подписку Беларуси"),
    BotCommand(command="city", description="Сменить город, напр. /city sankt-peterburg"),
    BotCommand(command="start", description="Помощь по командам"),
]


# --- защита от запуска второй копии ----------------------------------------
# Telegram разрешает только один getUpdates на токен; две копии = вечный
# TelegramConflictError. Держим эксклюзивный flock на файле: вторая копия
# не получит блокировку и сразу выйдет. Блокировка снимается ОС автоматически
# при завершении процесса (в т.ч. kill -9) — устаревших локов не остаётся.
_LOCK_FILE = Path(__file__).with_name("bot.lock")
_lock_fh = None


def _acquire_single_instance_lock() -> None:
    global _lock_fh
    _lock_fh = open(_LOCK_FILE, "w")
    try:
        fcntl.flock(_lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        raise SystemExit(
            "⛔ Бот уже запущен в другом процессе (занят bot.lock).\n"
            "   Останови ту копию или выполни:  pkill -9 -f 'bot.py'"
        )
    _lock_fh.write(str(os.getpid()))
    _lock_fh.flush()


async def main():
    if config.BOT_TOKEN.startswith("ВСТАВЬТЕ"):
        raise SystemExit("Задайте BOT_TOKEN в config.py или переменной окружения.")
    _acquire_single_instance_lock()
    bot = Bot(
        token=config.BOT_TOKEN,
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML, link_preview_is_disabled=True
        ),
    )
    await bot.set_my_commands(COMMANDS)

    global BOT_USERNAME
    me = await bot.get_me()
    BOT_USERNAME = me.username

    asyncio.create_task(scheduler(bot))
    logger.info(
        "Бот @%s запущен (подписок РФ: %d, Беларусь: %d)",
        BOT_USERNAME, len(_subs), len(_by_subs),
    )
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
