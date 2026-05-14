import os
import time
import asyncio
import logging
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand, MenuButtonCommands
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from data_collector import collect_all_data, quick_scan_coin, AI_COINS, get_tao_ticker, get_binance_klines, calculate_support_resistance, detect_trend, detect_bottom_patterns
from news_fetcher import get_all_news
from signal_engine import generate_signal, format_report, quick_signal_score

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID        = os.environ.get("CHAT_ID")

# ─── глобальное состояние ────────────────────────────────────────────────────
alerts_enabled    = True
last_alert_score  = 0
last_alert_ts     = 0.0   # время последнего алерта (антиспам: минимум 20 мин между одинаковыми)
_atl_alert_sent        = 0.0   # цена при ATL-алерте

# Bottom pattern алерты
_bottom_alert_ts       = 0.0   # время последнего bottom алерта
_capitulation_alert_ts = 0.0   # время последнего capitulation алерта
_triple_bottom_level   = 0.0   # уровень последнего triple bottom алерта

# Ценовой трекер
_price_ref        = 0.0
_price_ref_ts     = 0.0
_price_last       = 0.0
_price_alert_sent: dict = {}
_last_support     = 0.0
_last_trend_dir   = ""    # последнее известное направление тренда

# ─── клавиатуры ──────────────────────────────────────────────────────────────
KEYBOARD = ReplyKeyboardMarkup(
    [
        ["📊 Анализ TAO", "📰 Новости"],
        ["🪙 AI Монеты",  "🔔 Алерты: ВКЛ"],
        ["⚙️ Помощь"],
    ],
    resize_keyboard=True
)
KEYBOARD_ALERTS_OFF = ReplyKeyboardMarkup(
    [
        ["📊 Анализ TAO", "📰 Новости"],
        ["🪙 AI Монеты",  "🔔 Алерты: ВЫКЛ"],
        ["⚙️ Помощь"],
    ],
    resize_keyboard=True
)

def get_keyboard():
    return KEYBOARD if alerts_enabled else KEYBOARD_ALERTS_OFF


# ─── анализ ──────────────────────────────────────────────────────────────────

async def run_analysis(context=None, update=None, symbol: str = "TAOUSDT"):
    chat_id = CHAT_ID
    try:
        if update:
            chat_id = str(update.effective_chat.id)
            await context.bot.send_chat_action(chat_id=chat_id, action="typing")

        data   = collect_all_data(symbol)
        news   = get_all_news() if symbol == "TAOUSDT" else {"tao_news": [], "ai_news": []}
        signal = generate_signal(data, news)
        report = format_report(data, news, signal)

        kb = get_keyboard() if update else None
        await context.bot.send_message(chat_id=chat_id, text=report, reply_markup=kb)

    except Exception as e:
        logger.error(f"Analysis error ({symbol}): {e}")
        if update:
            await update.message.reply_text(f"❌ Ошибка: {str(e)[:100]}")


# ─── price_watcher (каждые 2 мин) ────────────────────────────────────────────

async def price_watcher(context):
    """Быстрые ценовые алерты: дроп, поддержка, отскок, тренд-смена."""
    global _price_ref, _price_ref_ts, _price_last, _price_alert_sent, _last_support, _last_trend_dir, alerts_enabled

    if not alerts_enabled:
        return

    ticker = get_tao_ticker()
    if ticker.get("error") or not ticker.get("price"):
        logger.warning(f"price_watcher: ticker error: {ticker.get('error')}")
        return

    price = ticker["price"]
    now   = time.time()

    # Первый запуск — инициализация
    if _price_ref == 0:
        _price_ref        = price
        _price_ref_ts     = now
        _price_last       = price
        _price_alert_sent = {}
        candles = get_binance_klines("TAOUSDT", "1h", 22)
        lvl     = calculate_support_resistance(candles)
        trend   = detect_trend(candles)
        _last_support   = lvl.get("support", 0)
        _last_trend_dir = trend.get("direction", "")
        logger.info(f"price_watcher init: ref=${price:.2f} support=${_last_support:.2f} trend={_last_trend_dir}")
        return

    # Сброс референса каждые 30 мин
    if now - _price_ref_ts >= 1800:
        candles = get_binance_klines("TAOUSDT", "1h", 22)
        lvl     = calculate_support_resistance(candles)
        trend   = detect_trend(candles)
        new_trend_dir = trend.get("direction", "")

        # Смена тренда — важное событие
        if _last_trend_dir and new_trend_dir and new_trend_dir != _last_trend_dir:
            if new_trend_dir == "down" and _last_trend_dir in ("up", "sideways"):
                msg = (
                    f"⚠️ TAO: ТРЕНД РАЗВЕРНУЛСЯ ВНИЗ\n"
                    f"💰 ${price:,.2f}\n"
                    f"📉 Серия нисходящих свечей началась. Осторожно — не входи против тренда.\n"
                    f"Жди стабилизации."
                )
                await context.bot.send_message(chat_id=CHAT_ID, text=msg, reply_markup=get_keyboard())
                logger.info(f"price_watcher: trend reversal down sent")
            elif new_trend_dir == "up" and _last_trend_dir in ("down", "sideways"):
                msg = (
                    f"✅ TAO: ТРЕНД РАЗВЕРНУЛСЯ ВВЕРХ\n"
                    f"💰 ${price:,.2f}\n"
                    f"📈 Серия восходящих свечей. Смотри RSI и объём — возможен вход."
                )
                await context.bot.send_message(chat_id=CHAT_ID, text=msg, reply_markup=get_keyboard())
                logger.info(f"price_watcher: trend reversal up sent")

        _last_support   = lvl.get("support", 0)
        _last_trend_dir = new_trend_dir
        _price_ref      = price
        _price_ref_ts   = now
        _price_alert_sent = {}
        _price_last     = price
        logger.info(f"price_watcher reset: ref=${price:.2f} support=${_last_support:.2f} trend={_last_trend_dir}")
        return

    drop_pct        = (_price_ref - price) / _price_ref * 100 if _price_ref else 0
    drop_pct_fast   = (_price_last - price) / _price_last * 100 if _price_last else 0  # дроп с прошлого тика (2 мин)
    bounce_pct      = (price - _price_last) / _price_last * 100 if _price_last else 0
    support         = _last_support

    msgs = []

    # Резкое падение за 2 мин (между тиками) — самый важный алерт
    if drop_pct_fast >= 1.5 and "fast_drop" not in _price_alert_sent:
        msgs.append(
            f"⚡ TAO РЕЗКИЙ ДРОП -{drop_pct_fast:.1f}% за 2 мин\n"
            f"💰 ${price:,.2f}  (был ${_price_last:,.2f})\n"
            f"🔴 НЕ ВХОДИ — жди стабилизации и разворота"
        )
        _price_alert_sent["fast_drop"] = price

    # Падение -2% от 30-минутного референса
    if drop_pct >= 2.0 and "drop2" not in _price_alert_sent:
        msgs.append(
            f"📉 TAO -{drop_pct:.1f}% за 30 мин\n"
            f"💰 ${price:,.2f}  (был ${_price_ref:,.2f})\n"
            f"👀 Следи — возможна точка входа после стабилизации"
        )
        _price_alert_sent["drop2"] = price

    # Падение -5% — серьёзный сигнал
    if drop_pct >= 5.0 and "drop5" not in _price_alert_sent:
        msgs.append(
            f"🔻 TAO -{drop_pct:.1f}% — СИЛЬНЫЙ ДРОП\n"
            f"💰 ${price:,.2f}\n"
            f"⚡ Проверь RSI и тренд — НЕ ВХОДИ пока нет разворота"
        )
        _price_alert_sent["drop5"] = price

    # Цена у поддержки (±1.5%) — только если тренд не нисходящий
    if support and "support" not in _price_alert_sent and _last_trend_dir != "down":
        dist_pct = abs(price - support) / support * 100
        if dist_pct <= 1.5:
            msgs.append(
                f"🎯 TAO У ПОДДЕРЖКИ — РАССМОТРИ ВХОД\n"
                f"💰 ${price:,.2f}  |  Поддержка: ${support:,.2f}\n"
                f"🟢 Отскок отсюда исторически высокий (проверь RSI)"
            )
            _price_alert_sent["support"] = price

    # Отскок после падения — тренд начал разворачиваться
    if "drop2" in _price_alert_sent and "bounce" not in _price_alert_sent:
        if bounce_pct >= 1.5:
            msgs.append(
                f"🔄 TAO отскочил +{bounce_pct:.1f}% — ИМПУЛЬС ВВЕРХ\n"
                f"💰 ${price:,.2f}\n"
                f"Смотри объём — если растёт, можно заходить"
            )
            _price_alert_sent["bounce"] = price

    for m in msgs:
        logger.info(f"price alert: {m[:50]}")
        await context.bot.send_message(chat_id=CHAT_ID, text=m, reply_markup=get_keyboard())

    _price_last = price


# ─── alert_scanner (каждые 15 мин) ───────────────────────────────────────────

async def alert_scanner(context):
    """Комплексный сигнал: RSI + MACD + тренд + BTC + уровни. С downtrend lock."""
    global last_alert_score, last_alert_ts, alerts_enabled, _atl_alert_sent
    global _bottom_alert_ts, _capitulation_alert_ts, _triple_bottom_level

    if not alerts_enabled:
        return

    data = collect_all_data("TAOUSDT")
    if data.get("tao", {}).get("error"):
        logger.warning("alert_scanner: data error")
        return

    score     = quick_signal_score(data)
    price     = data.get("tao", {}).get("price", 0)
    rsi       = data.get("rsi_1h", 50)
    macd      = data.get("macd_1h", {})
    macd_cross = macd.get("cross", "none")
    support   = data.get("levels", {}).get("support", 0)
    trend     = data.get("trend", {})
    stoch     = data.get("stoch_rsi_1h", {})
    bb        = data.get("bb_1h", {})

    consec_down = trend.get("consecutive_down", 0)
    consec_up   = trend.get("consecutive_up", 0)
    direction   = trend.get("direction", "")

    now = time.time()

    logger.info(f"alert_scanner: score={score} price={price:.2f} rsi={rsi} macd={macd_cross} "
                f"trend={direction}({consec_down}↓/{consec_up}↑)")

    # ── ATL-алерт ────────────────────────────────────────────────────────────
    weekly      = data.get("history", {}).get("weekly", {})
    atl         = weekly.get("atl", 0)
    pct_from_atl = weekly.get("pct_from_atl", 999)

    if atl and price:
        if _atl_alert_sent and price > atl * 1.15:
            _atl_alert_sent = 0.0

        if not _atl_alert_sent:
            if price <= atl:
                msg = (
                    f"🔥 НОВЫЙ ИСТОРИЧЕСКИЙ МИНИМУМ — TAO\n"
                    f"━━━━━━━━━━━━━━━━━━━\n"
                    f"💰 Цена: ${price:,.2f}  ← НИЖЕ ATL!\n"
                    f"📉 Предыдущий ATL: ${atl:,.2f}\n"
                    f"📉 RSI 1H: {rsi}\n\n"
                    f"🟢 По оценке бота — ЛУЧШАЯ ТОЧКА ВХОДА\n"
                    f"Такого не было никогда. Заходи частями или жди подтверждения.\n\n"
                    f"👉 «📊 Анализ TAO» — полный план"
                )
                await context.bot.send_message(chat_id=CHAT_ID, text=msg, reply_markup=get_keyboard())
                _atl_alert_sent = price
                logger.info(f"alert_scanner: NEW ATL price={price:.2f} atl={atl:.2f}")
            elif pct_from_atl <= 10:
                msg = (
                    f"🚨 TAO У ИСТОРИЧЕСКОГО МИНИМУМА\n"
                    f"━━━━━━━━━━━━━━━━━━━\n"
                    f"💰 ${price:,.2f}  |  ATL: ${atl:,.2f}  (+{pct_from_atl:.1f}% от дна)\n"
                    f"📉 RSI 1H: {rsi}  |  Счёт: {score:+d}/10\n\n"
                    f"🟢 ЗОНА КАПИТУЛЯЦИИ — исторически лучшие точки входа\n"
                    f"Рассмотри вход частями\n\n"
                    f"👉 «📊 Анализ TAO» — полный план"
                )
                await context.bot.send_message(chat_id=CHAT_ID, text=msg, reply_markup=get_keyboard())
                _atl_alert_sent = price
                logger.info(f"alert_scanner: ATL zone price={price:.2f} pct={pct_from_atl:.1f}%")

    # ── BOTTOM PATTERNS — главный блок новых алертов ─────────────────────────
    bottom = data.get("bottom", {})
    bottom_score    = bottom.get("bottom_score", 0)
    bottom_desc     = bottom.get("description", [])
    triple_bottom   = bottom.get("multiple_bottom", False)
    bottom_level    = bottom.get("bottom_level", 0.0)
    bottom_touches  = bottom.get("bottom_touches", 0)
    capit_drop      = bottom.get("capitulation_drop", False)
    drop_pct        = bottom.get("drop_pct", 0.0)
    vol_climax      = bottom.get("volume_climax", False)
    vol_ratio       = bottom.get("volume_climax_ratio", 0.0)
    rsi_div         = bottom.get("rsi_divergence", False)
    hammer          = bottom.get("hammer_reversal", False)

    # 1. Тройное/четвертное дно — сильнейший сигнал
    if triple_bottom and bottom_level > 0:
        level_changed = abs(bottom_level - _triple_bottom_level) > bottom_level * 0.03
        cooldown_ok   = now - _bottom_alert_ts >= 3600  # не чаще 1 раза в час

        if cooldown_ok and level_changed:
            extra_signals = []
            if vol_climax:    extra_signals.append(f"📊 Объём {vol_ratio:.1f}x — продавцы выдохлись")
            if rsi_div:       extra_signals.append("📈 RSI дивергенция — разворот подтверждён")
            if hammer:        extra_signals.append("🔨 Молот на уровне — покупатели защищают дно")
            if capit_drop:    extra_signals.append(f"💥 Перед этим был резкий дамп {abs(drop_pct):.1f}% — паника выкуплена")

            extra_str = ("\n" + "\n".join(extra_signals)) if extra_signals else ""
            confidence = "🔥 ОЧЕНЬ ВЫСОКАЯ" if bottom_score >= 6 else "⚡ ВЫСОКАЯ" if bottom_score >= 4 else "🟡 СРЕДНЯЯ"

            msg = (
                f"🔁 ТРОЙНОЕ ДНО — TAO | ТОЧКА ВХОДА\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"💰 Цена: ${price:,.2f}\n"
                f"🎯 Уровень поддержки: ${bottom_level:,.2f}  ({bottom_touches} касания)\n"
                f"📊 Сила сигнала: {confidence} ({bottom_score}/10)\n"
                f"📉 RSI 1H: {rsi}  |  RSI 4H: {data.get('rsi_4h', 50)}\n"
                f"{extra_str}\n\n"
                f"📌 ЛОГИКА: рынок 3+ раз отбился от одного уровня — продавцы истощены, покупатели держат.\n"
                f"Чем больше касаний = тем сильнее поддержка.\n\n"
                f"💡 Тактика: вход частями у ${bottom_level:,.2f}, стоп ниже ${bottom_level * 0.97:,.2f}\n"
                f"Цель 1: ${bottom_level * 1.1:,.2f} (+10%)  |  Цель 2: ${bottom_level * 1.2:,.2f} (+20%)"
            )
            await context.bot.send_message(chat_id=CHAT_ID, text=msg, reply_markup=get_keyboard())
            _bottom_alert_ts     = now
            _triple_bottom_level = bottom_level
            logger.info(f"TRIPLE BOTTOM alert: level={bottom_level:.2f} touches={bottom_touches} score={bottom_score}")

    # 2. Капитуляция — резкое падение без причины
    if capit_drop and not triple_bottom:
        cooldown_ok = now - _capitulation_alert_ts >= 1800  # не чаще раза в 30 мин
        if cooldown_ok and abs(drop_pct) >= 5.0:
            extra_signals = []
            if vol_climax: extra_signals.append(f"📊 Объём взлетел в {vol_ratio:.1f}x — паническая продажа")
            if rsi_div:    extra_signals.append("📈 RSI дивергенция — импульс падения слабеет")
            if hammer:     extra_signals.append("🔨 Молот на последней свече — первый признак разворота")
            if rsi < 30:   extra_signals.append(f"💎 RSI {rsi} — перепродан, отскок вероятен")
            extra_str = ("\n" + "\n".join(extra_signals)) if extra_signals else ""

            intensity = "🔴 СИЛЬНАЯ" if abs(drop_pct) >= 8 else "🟠 УМЕРЕННАЯ"
            msg = (
                f"💥 КАПИТУЛЯЦИЯ — TAO УПАЛ ИЗ НИОТКУДА\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"💰 Цена: ${price:,.2f}  ({drop_pct:.1f}% за 3 свечи)\n"
                f"⚡ Интенсивность: {intensity}\n"
                f"📉 RSI 1H: {rsi}  |  Страх/жадность: {data.get('fear_greed',{}).get('value',50)}\n"
                f"{extra_str}\n\n"
                f"📌 ЛОГИКА: резкое падение без тренда = паническая продажа.\n"
                f"После капитуляций статистически часто идёт быстрый отскок +5–15%.\n\n"
                f"⚠️ НЕ входи сразу — жди стабилизации цены (2 свечи без новых лоу).\n"
                f"Уровень поддержки: ${support:,.2f}"
            )
            await context.bot.send_message(chat_id=CHAT_ID, text=msg, reply_markup=get_keyboard())
            _capitulation_alert_ts = now
            logger.info(f"CAPITULATION alert: drop={drop_pct:.1f}% score={bottom_score}")

    # 3. Комбо-сигнал: несколько bottom-паттернов одновременно (без тройного дна)
    elif not triple_bottom and not capit_drop and bottom_score >= 4:
        cooldown_ok = now - _bottom_alert_ts >= 2700  # 45 мин
        if cooldown_ok:
            signals_str = "\n".join(f"• {d}" for d in bottom_desc)
            msg = (
                f"🟢 ЗОНА НАКОПЛЕНИЯ — TAO\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"💰 Цена: ${price:,.2f}  |  Сила сигнала: {bottom_score}/10\n"
                f"📉 RSI 1H: {rsi}  |  RSI 4H: {data.get('rsi_4h', 50)}\n\n"
                f"Зафиксированы паттерны дна:\n{signals_str}\n\n"
                f"💡 Рассмотри вход небольшой частью позиции"
            )
            await context.bot.send_message(chat_id=CHAT_ID, text=msg, reply_markup=get_keyboard())
            _bottom_alert_ts = now
            logger.info(f"COMBO bottom alert: score={bottom_score} patterns={len(bottom_desc)}")

    # ── Нисходящий тренд — предупреждение ────────────────────────────────────
    # Если тренд нисходящий и скор всё равно ≥ 0 — напомнить что не время входить
    if direction == "down" and consec_down >= 3 and last_alert_score >= 0 and score <= 1:
        if now - last_alert_ts >= 1800:  # не чаще раза в 30 мин
            stoch_k = stoch.get("k", 50)
            msg = (
                f"⚠️ TAO: АКТИВНЫЙ НИСХОДЯЩИЙ ТРЕНД\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"💰 ${price:,.2f}  |  Счёт: {score:+d}/10\n"
                f"📉 {consec_down} свечей подряд вниз\n"
                f"RSI 1H: {rsi}  |  Stoch RSI K: {stoch_k:.0f}\n\n"
                f"🔒 Бот заблокировал сигнал входа — не торгуй против тренда\n"
                f"Жди: 2 зелёных свечи подряд + RSI разворот"
            )
            await context.bot.send_message(chat_id=CHAT_ID, text=msg, reply_markup=get_keyboard())
            last_alert_ts = now
            logger.info(f"alert_scanner: downtrend warning sent consec={consec_down}")
        last_alert_score = score
        return

    # ── Позитивные сигналы — антиспам: 20 мин между отправками ──────────────
    if score >= 5 and last_alert_score < 5:
        if now - last_alert_ts < 1200 and last_alert_score >= 4:
            last_alert_score = score
            return
        macd_str = "\n⚡ MACD бычий кросс — импульс пошёл" if macd_cross == "bullish" else ""
        sup_str  = f"\n🎯 Поддержка: ${support:,.2f}" if support else ""
        stoch_k  = stoch.get("k", 50)
        stoch_str = f"\n🎲 Stoch RSI: {stoch_k:.0f} (перепродан)" if stoch_k < 25 else ""
        bb_lower = bb.get("lower", 0)
        bb_str   = f"\n📐 У нижней BB (${bb_lower:,.2f})" if bb_lower and price <= bb_lower * 1.01 else ""
        msg = (
            f"🚨 СИЛЬНЫЙ СИГНАЛ — ВХОДИ — TAO\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"🟢 Счёт: {score:+d}/10  |  Тренд: ↑\n"
            f"💰 Цена: ${price:,.2f}\n"
            f"📉 RSI 1H: {rsi}{stoch_str}{macd_str}{sup_str}{bb_str}\n\n"
            f"👉 «📊 Анализ TAO» — полный план"
        )
        await context.bot.send_message(chat_id=CHAT_ID, text=msg, reply_markup=get_keyboard())
        last_alert_ts = now
        logger.info(f"alert_scanner: STRONG ENTRY score={score}")

    elif score >= 3 and last_alert_score < 3:
        if now - last_alert_ts < 1200:
            last_alert_score = score
            return
        msg = (
            f"🟢 TAO — ВХОДИТЬ\n"
            f"💰 ${price:,.2f}  |  RSI {rsi}  |  Счёт {score:+d}/10\n"
            f"Условия хорошие. Смотри полный анализ для точки входа."
        )
        await context.bot.send_message(chat_id=CHAT_ID, text=msg, reply_markup=get_keyboard())
        last_alert_ts = now

    elif score >= 1 and last_alert_score < 1:
        if now - last_alert_ts < 2400:
            last_alert_score = score
            return
        msg = (
            f"🟡 TAO — МОЖНО ВОЙТИ МАЛОЙ ЧАСТЬЮ\n"
            f"💰 ${price:,.2f}  |  RSI {rsi}  |  Счёт {score:+d}/10\n"
            f"Неплохо, но осторожно. Жди подтверждения."
        )
        await context.bot.send_message(chat_id=CHAT_ID, text=msg, reply_markup=get_keyboard())
        last_alert_ts = now

    elif score <= -4 and last_alert_score > -4:
        msg = (
            f"🔴 НЕ ВХОДИ — TAO\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"Счёт: {score:+d}/10 — высокий риск\n"
            f"💰 ${price:,.2f}  |  RSI {rsi}\n"
            f"Жди разворота."
        )
        await context.bot.send_message(chat_id=CHAT_ID, text=msg, reply_markup=get_keyboard())
        last_alert_ts = now

    last_alert_score = score


# ─── команды ─────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📊 TAO Signal Bot v2\n"
        "━━━━━━━━━━━━━━━━━━━\n\n"
        "📊 Анализ TAO — RSI, Stoch RSI, MACD, BB,\n"
        "   тренд, MA/EMA, история, план входа\n"
        "🪙 AI Монеты — RENDER, FET, WLD, INJ,\n"
        "   OCEAN, AR, GRT → тапни → полный анализ\n"
        "🔔 Алерты — каждые 2 мин (цена) + 15 мин (сигнал)\n"
        "   ⚠️ Бот блокирует входы при нисходящем тренде\n\n"
        "⚙️ Помощь — полная инструкция\n\n"
        "Поехали!",
        reply_markup=get_keyboard()
    )


async def cmd_analysis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Анализирую TAO...")
    await run_analysis(context=context, update=update, symbol="TAOUSDT")


async def cmd_news(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Ищу новости...")
    try:
        news = get_all_news()
        lines = ["📰 СВЕЖИЕ НОВОСТИ\n"]

        tao_news = news.get("tao_news", [])
        if tao_news:
            lines.append("🔵 TAO / BITTENSOR:")
            for item in tao_news:
                icon = {"positive": "🟢", "negative": "🔴", "neutral": "⚪"}.get(item.get("sentiment", ""), "⚪")
                lines.append(f"{icon} {item['title'][:100]}")
                lines.append(f"  [{item['source']}]")
                lines.append("")
        else:
            lines.append("По TAO новостей нет прямо сейчас.\n")

        ai_news = news.get("ai_news", [])
        if ai_news:
            lines.append("🤖 AI НОВОСТИ:")
            for item in ai_news[:3]:
                lines.append(f"• {item['title'][:100]}")
                lines.append(f"  [{item['source']}]")
                lines.append("")

        overall = news.get("overall_sentiment", "neutral")
        sent_icon = {"positive": "🟢", "negative": "🔴", "neutral": "⚪"}.get(overall, "⚪")
        lines.append(f"\n{sent_icon} Общий тон новостей по TAO: {overall.upper()}")

        cg = news.get("coingecko", {})
        if cg.get("market_cap_rank"):
            lines.append(f"📊 CoinGecko rank: #{cg['market_cap_rank']}")
        if cg.get("sentiment_votes_up"):
            lines.append(f"👍 Sentiment votes up: {cg['sentiment_votes_up']:.0f}%")

        await update.message.reply_text("\n".join(lines), reply_markup=get_keyboard())
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")


async def cmd_ai_coins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⏳ Сканирую AI-монеты...")
    buttons = []

    for symbol, name, emoji in AI_COINS:
        try:
            scan       = quick_scan_coin(symbol)
            price      = scan.get("price", 0)
            change     = scan.get("change_24h", 0)
            rsi        = scan.get("rsi_1h", 50)
            macd_cross = scan.get("macd", {}).get("cross", "none")
            trend_dir  = scan.get("trend", {}).get("direction", "")
            consec_down = scan.get("trend", {}).get("consecutive_down", 0)

            score = 0
            if rsi < 30:      score += 2
            elif rsi < 42:    score += 1
            elif rsi > 75:    score -= 2
            elif rsi > 65:    score -= 1
            if macd_cross == "bullish":  score += 2
            elif macd_cross == "bearish": score -= 2
            if change > 3:    score += 1
            elif change < -5: score -= 1
            if trend_dir == "down": score -= 2
            elif trend_dir == "up": score += 1

            if score >= 3:    status = "🟢"
            elif score >= 1:  status = "🟡"
            elif score <= -3: status = "🔴"
            elif score <= -1: status = "🟠"
            else:             status = "⚪"

            change_sign = "+" if change > 0 else ""
            macd_str    = " ⚡" if macd_cross == "bullish" else ""
            trend_str   = " ↓↓" if (trend_dir == "down" and consec_down >= 3) else (" ↓" if trend_dir == "down" else "")
            label = f"{status} {emoji} {name}   ${price:,.4g}   {change_sign}{change:.1f}%   RSI {rsi}{macd_str}{trend_str}"
            buttons.append([InlineKeyboardButton(label, callback_data=f"coin_{symbol}")])
        except Exception:
            buttons.append([InlineKeyboardButton(f"⚪ {emoji} {name}   —", callback_data=f"coin_{symbol}")])

    kb = InlineKeyboardMarkup(buttons)
    await msg.edit_text(
        "🪙 AI МОНЕТЫ ДЛЯ СКАЛЬПИНГА\n"
        "Нажми на монету — полный анализ\n\n"
        "🟢 хороший вход  🟡 осторожно  🟠 ждать\n"
        "🔴 не входить  ⚡ MACD кросс  ↓↓ тренд вниз",
        reply_markup=kb
    )


async def cb_coin_analysis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    symbol = query.data.replace("coin_", "")
    name   = symbol.replace("USDT", "")
    await query.message.reply_text(f"⏳ Анализирую {name}...")
    try:
        data   = collect_all_data(symbol)
        signal = generate_signal(data, {"tao_news": [], "ai_news": []})
        report = format_report(data, {"tao_news": [], "ai_news": []}, signal)
        await query.message.reply_text(report, reply_markup=get_keyboard())
    except Exception as e:
        await query.message.reply_text(f"❌ Ошибка анализа {name}: {str(e)[:100]}")


async def cmd_toggle_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global alerts_enabled
    alerts_enabled = not alerts_enabled
    status = "ВКЛ 🔔" if alerts_enabled else "ВЫКЛ 🔕"
    await update.message.reply_text(
        f"Алерты: {status}\n\n"
        f"{'Буду присылать уведомления, блокируя ложные сигналы при нисходящем тренде.' if alerts_enabled else 'Алерты отключены.'}",
        reply_markup=get_keyboard()
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 КАК ПОЛЬЗОВАТЬСЯ\n"
        "━━━━━━━━━━━━━━━━━━━\n\n"
        "📊 Анализ TAO — полный разбор:\n"
        "  RSI 1H/4H, Stoch RSI, MACD, Bollinger Bands,\n"
        "  MA20/MA50/EMA200, тренд, стакан, история\n\n"
        "🪙 AI Монеты — 7 монет с трендом и скором\n\n"
        "📰 Новости — с sentiment: 🟢🔴⚪\n\n"
        "🔔 Алерты:\n"
        "  • 2 мин — цена: дроп, поддержка, отскок, смена тренда\n"
        "  • 15 мин — сигнал: RSI+MACD+BB+тренд+BTC\n"
        "  ⚠️ При нисходящем тренде (3+ свечей вниз)\n"
        "     сигналы блокируются — бот защищает от ложных входов\n\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "СИГНАЛЫ:\n"
        "🟢 СИЛЬНЫЙ ВХОД (≥5) — идеальные условия\n"
        "🟢 ВХОДИТЬ (3-4) — хорошие условия\n"
        "🟡 ОСТОРОЖНО (1-2) — малой частью\n"
        "⚪ НЕЙТРАЛЬНО (0) — нет сигнала\n"
        "🟠 ЖДАТЬ (-1 до -2)\n"
        "🔴 НЕ ВХОДИТЬ (-3 и ниже)\n"
        "🔴 ВЫХОДИТЬ (-5 и ниже)\n\n"
        "⚠️ Не финансовый совет!",
        reply_markup=get_keyboard()
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "📊 Анализ TAO":
        await update.message.reply_text("⏳ Анализирую TAO...")
        await run_analysis(context=context, update=update, symbol="TAOUSDT")
    elif text == "📰 Новости":
        await cmd_news(update, context)
    elif text == "🪙 AI Монеты":
        await cmd_ai_coins(update, context)
    elif text in ("🔔 Алерты: ВКЛ", "🔔 Алерты: ВЫКЛ"):
        await cmd_toggle_alerts(update, context)
    elif text == "⚙️ Помощь":
        await cmd_help(update, context)


# ─── запуск ──────────────────────────────────────────────────────────────────

async def post_init(application: Application):
    await application.bot.set_my_commands([
        BotCommand("start",    "Запустить бота"),
        BotCommand("analysis", "Анализ TAO прямо сейчас"),
        BotCommand("coins",    "Список AI-монет"),
        BotCommand("news",     "Свежие новости TAO и AI"),
        BotCommand("alerts",   "Вкл/выкл алерты"),
        BotCommand("help",     "Справка по сигналам"),
    ])
    await application.bot.set_chat_menu_button(menu_button=MenuButtonCommands())

    scheduler = AsyncIOScheduler()
    ctx = type("ctx", (), {"bot": application.bot})()

    scheduler.add_job(price_watcher,  "interval", minutes=2,  kwargs={"context": ctx})
    scheduler.add_job(alert_scanner,  "interval", minutes=15, kwargs={"context": ctx})
    scheduler.add_job(run_analysis,   "interval", hours=4,    kwargs={"context": ctx})
    scheduler.add_job(run_analysis,   "cron", hour=9, minute=0, kwargs={"context": ctx})

    scheduler.start()
    logger.info("Scheduler started: price_watcher 2min / alert_scanner 15min / analysis 4h + 9:00")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    from telegram.error import Conflict
    if isinstance(context.error, Conflict):
        logger.warning("Conflict в error_handler: ждём 40с и продолжаем")
        await asyncio.sleep(40)
    else:
        logger.error("Unhandled error: %s", context.error)


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start",    start))
    app.add_handler(CommandHandler("analysis", cmd_analysis))
    app.add_handler(CommandHandler("coins",    cmd_ai_coins))
    app.add_handler(CommandHandler("news",     cmd_news))
    app.add_handler(CommandHandler("alerts",   cmd_toggle_alerts))
    app.add_handler(CommandHandler("help",     cmd_help))
    app.add_handler(CallbackQueryHandler(cb_coin_analysis, pattern=r"^coin_"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_error_handler(error_handler)
    logger.info("TAO Signal Bot v2 started")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
