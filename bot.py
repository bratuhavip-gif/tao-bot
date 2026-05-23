"""
TAO Signal Bot v4 — скальпинговые сигналы ВХОД/ВЫХОД.

Расписание:
  price_watcher  — каждую 1 минуту: мгновенные движения цены
  signal_scanner — каждые 10 минут: полный анализ, сигналы ВОЙТИ / ВЫЙТИ
  full_report    — каждые 6 часов + 9:00: развёрнутый отчёт

Логика алертов:
  Сигнал ВОЙТИ  → score >= 4 (несколько подтверждений снизу)
  Сигнал ВЫЙТИ  → score <= -4 (несколько подтверждений сверху)
  Быстрый дроп  → -1.5% за 1 мин → предупреждение
  Быстрый рост  → +1.5% за 1 мин → предупреждение о пике
"""
import os
import time
import asyncio
import logging
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand, MenuButtonCommands
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# Загружаем .env (локальный запуск)
_env_path = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(_env_path):
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and "=" in _line and not _line.startswith("#"):
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())

from data_collector import (
    collect_all_data, quick_scan_coin, AI_COINS,
    get_tao_ticker, get_klines, calculate_support_resistance, detect_trend,
    detect_bottom_patterns, get_binance_klines,
)
from news_fetcher import get_all_news
from signal_engine import generate_signal, format_report, quick_signal_score

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN  = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID         = os.environ.get("CHAT_ID")
ANTHROPIC_KEY   = os.environ.get("ANTHROPIC_API_KEY")

# Claude клиент (lazy init)
_claude = None
def _get_claude():
    global _claude
    if _claude is None and ANTHROPIC_KEY:
        import anthropic
        _claude = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    return _claude

# ─── состояние ────────────────────────────────────────────────────────────────
alerts_enabled   = True

# Скор из прошлого цикла (для отслеживания смены сигнала)
_last_score      = 0
_last_alert_ts   = 0.0
_last_signal_dir = ""   # "buy" | "sell" | ""

# Price watcher
_prev_price      = 0.0
_prev_price_ts   = 0.0
_price_alert_cd  = {}   # cooldown на тип алерта

# Bottom patterns
_bottom_alert_ts = 0.0
_capit_alert_ts  = 0.0

# Downtrend warning
_downtrend_ts    = 0.0

# ATL
_atl_alert_sent  = 0.0


# ─── клавиатура ───────────────────────────────────────────────────────────────
def _kb(alerts_on: bool) -> ReplyKeyboardMarkup:
    status = "ВКЛ 🔔" if alerts_on else "ВЫКЛ 🔕"
    return ReplyKeyboardMarkup(
        [["📊 Анализ TAO", "📰 Новости"],
         ["🪙 AI Монеты",  f"🔔 Алерты: {status}"],
         ["⚙️ Помощь"]],
        resize_keyboard=True,
    )


# ─── отправка анализа ─────────────────────────────────────────────────────────
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

        kb = _kb(alerts_enabled) if update else None
        await context.bot.send_message(chat_id=chat_id, text=report, reply_markup=kb)
    except Exception as e:
        logger.error(f"run_analysis error ({symbol}): {e}")
        if update:
            await update.message.reply_text(f"❌ Ошибка: {str(e)[:120]}")


# ─── price_watcher (каждую 1 мин) ────────────────────────────────────────────
async def price_watcher(context):
    """
    Мгновенные алерты:
    - Резкий дроп -1.5% за 1 мин → предупреждение
    - Резкий рост +1.5% за 1 мин → предупреждение о пике
    - Цена вошла в зону поддержки → рассмотри вход
    - Цена вошла в зону сопротивления → готовься к выходу
    """
    global _prev_price, _prev_price_ts, _price_alert_cd, alerts_enabled

    if not alerts_enabled:
        return

    ticker = get_tao_ticker()
    if ticker.get("error") or not ticker.get("price"):
        return

    price = ticker["price"]
    now   = time.time()

    if _prev_price == 0:
        _prev_price    = price
        _prev_price_ts = now
        # Загружаем уровни при старте
        try:
            c1h = get_klines("TAOUSDT", "1h", 60)
            c4h = get_klines("TAOUSDT", "4h", 40)
            lvl = calculate_support_resistance(c1h, c4h)
            context.bot_data["support"]    = lvl.get("support", 0)
            context.bot_data["resistance"] = lvl.get("resistance", 0)
            logger.info(f"price_watcher init: ${price:.2f} sup=${lvl.get('support'):.2f} res={lvl.get('resistance'):.2f}")
        except Exception:
            pass
        return

    # Сброс кулдаунов каждые 20 минут
    if now - _prev_price_ts >= 1200:
        _price_alert_cd = {}
        _prev_price     = price
        _prev_price_ts  = now
        # Обновляем уровни
        try:
            c1h = get_klines("TAOUSDT", "1h", 60)
            c4h = get_klines("TAOUSDT", "4h", 40)
            lvl = calculate_support_resistance(c1h, c4h)
            context.bot_data["support"]    = lvl.get("support", 0)
            context.bot_data["resistance"] = lvl.get("resistance", 0)
        except Exception:
            pass
        return

    change_pct = (price - _prev_price) / _prev_price * 100 if _prev_price else 0
    support    = context.bot_data.get("support", 0)
    resistance = context.bot_data.get("resistance", 0)

    msgs = []

    # Резкий дроп за 1 минуту
    if change_pct <= -1.5 and "fast_drop" not in _price_alert_cd:
        msgs.append(
            f"⚡ TAO РЕЗКИЙ ДРОП\n"
            f"💰 ${price:,.2f}  ({change_pct:.1f}% за 1 мин)\n"
            f"⚠️ Не входи сразу — жди стабилизации\n"
            f"Если RSI < 35 и цена у поддержки — можно смотреть вход"
        )
        _price_alert_cd["fast_drop"] = now

    # Резкий рост за 1 минуту
    elif change_pct >= 1.5 and "fast_pump" not in _price_alert_cd:
        msgs.append(
            f"🚀 TAO РЕЗКИЙ РОСТ\n"
            f"💰 ${price:,.2f}  (+{change_pct:.1f}% за 1 мин)\n"
            f"⚠️ Если ты в позиции — подумай о фиксации части прибыли\n"
            f"Не входи на пике — жди отката"
        )
        _price_alert_cd["fast_pump"] = now

    # Цена вошла в зону поддержки
    if support and "near_support" not in _price_alert_cd:
        dist = (price - support) / support * 100
        if 0 <= dist <= 1.5:
            msgs.append(
                f"🎯 TAO У ПОДДЕРЖКИ\n"
                f"💰 ${price:,.2f}  |  Поддержка: ${support:,.2f}\n"
                f"📊 Нажми «Анализ TAO» для подтверждения входа"
            )
            _price_alert_cd["near_support"] = now

    # Цена вошла в зону сопротивления
    if resistance and "near_resistance" not in _price_alert_cd:
        dist = (resistance - price) / resistance * 100
        if 0 <= dist <= 1.5:
            msgs.append(
                f"🚧 TAO У СОПРОТИВЛЕНИЯ\n"
                f"💰 ${price:,.2f}  |  Сопр: ${resistance:,.2f}\n"
                f"⚠️ Зона продажи — если в позиции, рассмотри выход\n"
                f"Не открывай новый лонг здесь"
            )
            _price_alert_cd["near_resistance"] = now

    for m in msgs:
        logger.info(f"price_watcher: {m[:60]}")
        await context.bot.send_message(chat_id=CHAT_ID, text=m, reply_markup=_kb(alerts_enabled))

    _prev_price = price


# ─── signal_scanner (каждые 10 мин) ──────────────────────────────────────────
async def signal_scanner(context):
    """
    Главный сканер: полный анализ каждые 10 минут.
    Шлёт алерт при смене направления сигнала:
    - score >= 4  → ВОЙТИ
    - score <= -4 → ВЫЙТИ / НЕ ВХОДИТЬ
    - Нисходящий тренд 4H → предупреждение
    """
    global _last_score, _last_alert_ts, _last_signal_dir
    global _bottom_alert_ts, _capit_alert_ts, _downtrend_ts, _atl_alert_sent
    global alerts_enabled

    if not alerts_enabled:
        return

    try:
        data = collect_all_data("TAOUSDT")
    except Exception as e:
        logger.error(f"signal_scanner collect_all_data: {e}")
        return

    if data.get("tao", {}).get("error"):
        logger.warning("signal_scanner: data error")
        return

    score    = quick_signal_score(data)
    price    = data.get("tao", {}).get("price", 0)
    rsi_1h   = data.get("rsi_1h", 50)
    rsi_4h   = data.get("rsi_4h", 50)
    stoch    = data.get("stoch_rsi_1h", {})
    macd_1h  = data.get("macd_1h", {})
    levels   = data.get("levels", {})
    trend    = data.get("trend", {})
    trend_4h = data.get("trend_4h", {})
    bb       = data.get("bb_1h", {})
    bottom   = data.get("bottom", {})
    ema_r    = data.get("ema_ribbon", {})
    weekly   = data.get("history", {}).get("weekly", {})
    support  = levels.get("support", 0)
    resistance = levels.get("resistance", 0)
    now      = time.time()

    cd4 = trend_4h.get("consecutive_down", 0)
    cu4 = trend_4h.get("consecutive_up",   0)
    d4  = trend_4h.get("direction", "")
    cd1 = trend.get("consecutive_down", 0)
    cu1 = trend.get("consecutive_up",   0)

    e9  = ema_r.get("ema9",  0)
    e21 = ema_r.get("ema21", 0)

    logger.info(
        f"signal_scanner: score={score:+d} price={price:.2f} "
        f"RSI1h={rsi_1h} RSI4h={rsi_4h} "
        f"1H={trend.get('direction')}({cd1}↓/{cu1}↑) "
        f"4H={d4}({cd4}↓/{cu4}↑)"
    )

    # Обновляем уровни для price_watcher
    context.bot_data["support"]    = support
    context.bot_data["resistance"] = resistance

    # ── ATL алерт ────────────────────────────────────────────────────────────
    atl = weekly.get("atl", 0)
    pct_atl = weekly.get("pct_from_atl", 999)
    if atl and price:
        if _atl_alert_sent and price > atl * 1.15:
            _atl_alert_sent = 0.0
        if not _atl_alert_sent:
            if price <= atl:
                msg = (
                    f"🔥 НОВЫЙ ИСТОРИЧЕСКИЙ МИНИМУМ — TAO\n"
                    f"━━━━━━━━━━━━━━━━━━━\n"
                    f"💰 ${price:,.2f} — НИЖЕ ВСЕХ ATL!\n"
                    f"📉 RSI 1H: {rsi_1h}\n\n"
                    f"🟢 Исторически — лучшая точка входа\n"
                    f"Заходи частями, не всей суммой сразу"
                )
                await context.bot.send_message(chat_id=CHAT_ID, text=msg, reply_markup=_kb(alerts_enabled))
                _atl_alert_sent = price
            elif pct_atl <= 8:
                msg = (
                    f"🚨 TAO У ИСТОРИЧЕСКОГО МИНИМУМА\n"
                    f"━━━━━━━━━━━━━━━━━━━\n"
                    f"💰 ${price:,.2f}  |  ATL: ${atl:,.2f}  (+{pct_atl:.1f}%)\n"
                    f"RSI 1H: {rsi_1h}  |  RSI 4H: {rsi_4h}\n\n"
                    f"🟢 ЗОНА КАПИТУЛЯЦИИ — исторически выгодно\n"
                    f"Нажми «Анализ TAO» для полного плана"
                )
                await context.bot.send_message(chat_id=CHAT_ID, text=msg, reply_markup=_kb(alerts_enabled))
                _atl_alert_sent = price

    # ── Bottom patterns ───────────────────────────────────────────────────────
    bs        = bottom.get("bottom_score", 0)
    vol_clim  = bottom.get("volume_climax", False)
    vol_ratio = bottom.get("volume_climax_ratio", 0.0)
    triple    = bottom.get("multiple_bottom", False)
    bl        = bottom.get("bottom_level", 0.0)
    bt        = bottom.get("bottom_touches", 0)
    capit     = bottom.get("capitulation_drop", False)
    drop_pct  = bottom.get("drop_pct", 0.0)
    rsi_div   = bottom.get("rsi_divergence", False)
    hammer    = bottom.get("hammer_reversal", False)

    if triple and bl > 0 and now - _bottom_alert_ts >= 3600:
        extras = []
        if vol_clim:  extras.append(f"📊 Объём {vol_ratio:.1f}x — продавцы выдохлись")
        if rsi_div:   extras.append("📈 RSI дивергенция — разворот подтверждён")
        if hammer:    extras.append("🔨 Молот — покупатели защищают дно")
        extras_str = ("\n" + "\n".join(extras)) if extras else ""
        msg = (
            f"🔁 ТРОЙНОЕ ДНО — СИГНАЛ ВХОДА — TAO\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 ${price:,.2f}  |  Уровень: ${bl:,.2f}  ({bt} касания)\n"
            f"📉 RSI 1H: {rsi_1h}  |  RSI 4H: {rsi_4h}\n"
            f"Сила паттерна: {bs}/10{extras_str}\n\n"
            f"📌 Рынок {bt} раза защитил уровень ${bl:,.2f}\n"
            f"💡 Вход: у ${bl:,.2f}  |  Стоп: ${bl*0.97:,.2f}\n"
            f"Цель 1: ${bl*1.03:,.2f} (+3%)  |  Цель 2: ${bl*1.08:,.2f} (+8%)"
        )
        await context.bot.send_message(chat_id=CHAT_ID, text=msg, reply_markup=_kb(alerts_enabled))
        _bottom_alert_ts = now
        logger.info(f"TRIPLE BOTTOM: level={bl:.2f} score={bs}")

    elif capit and not triple and now - _capit_alert_ts >= 1800:
        extras = []
        if vol_clim:  extras.append(f"📊 Объём {vol_ratio:.1f}x — паническая продажа")
        if rsi_div:   extras.append("📈 RSI дивергенция — ослабление импульса")
        if rsi_1h < 30: extras.append(f"💎 RSI {rsi_1h} — перепродан")
        extras_str = ("\n" + "\n".join(extras)) if extras else ""
        msg = (
            f"💥 КАПИТУЛЯЦИЯ — TAO\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"💰 ${price:,.2f}  ({drop_pct:.1f}% за 3 свечи)\n"
            f"RSI 1H: {rsi_1h}  |  RSI 4H: {rsi_4h}{extras_str}\n\n"
            f"⚠️ НЕ входи сразу — жди 2 зелёных свечи подряд\n"
            f"Поддержка: ${support:,.2f}"
        )
        await context.bot.send_message(chat_id=CHAT_ID, text=msg, reply_markup=_kb(alerts_enabled))
        _capit_alert_ts = now
        logger.info(f"CAPITULATION: drop={drop_pct:.1f}%")

    # ── Нисходящий тренд на 4H — блок входа ──────────────────────────────────
    is_downtrend = (d4 == "down" and cd4 >= 3)
    if is_downtrend and score <= 1:
        cd_ok = now - _downtrend_ts >= 3600
        score_dropped = _last_score >= 2
        if cd_ok or score_dropped:
            msg = (
                f"⛔ TAO: НЕ ВХОДИТЬ — НИСХОДЯЩИЙ ТРЕНД\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"💰 ${price:,.2f}  |  Счёт: {score:+d}/8\n"
                f"📉 4H: {cd4} свечей вниз подряд\n"
                f"RSI 1H: {rsi_1h}  |  RSI 4H: {rsi_4h}\n\n"
                f"🔒 Торговля заблокирована\n"
                f"Жди: разворот на 4H (2 зелёных свечи) + RSI > 40"
            )
            await context.bot.send_message(chat_id=CHAT_ID, text=msg, reply_markup=_kb(alerts_enabled))
            _downtrend_ts = now
            logger.info(f"DOWNTREND BLOCK: 4H {cd4}↓")
        _last_score = score
        return

    # ── ОСНОВНЫЕ СИГНАЛЫ: ВОЙТИ / ВЫЙТИ ──────────────────────────────────────
    cooldown_ok = now - _last_alert_ts >= 600   # минимум 10 мин между одинаковыми алертами

    # ── СИГНАЛ ВОЙТИ ─────────────────────────────────────────────────────────
    if score >= 4 and _last_signal_dir != "buy":
        if not cooldown_ok and _last_score >= 4:
            _last_score = score
            return

        stoch_k = stoch.get("k", 50)
        macd_cross = macd_1h.get("cross", "none")
        bb_l = bb.get("lower", 0)

        # Строим детали
        details = []
        if rsi_1h < 35:  details.append(f"RSI 1H = {rsi_1h} — перепродан ✅")
        if rsi_4h < 40:  details.append(f"RSI 4H = {rsi_4h} — перепродан на 4H ✅")
        if stoch_k < 25: details.append(f"Stoch RSI = {stoch_k:.0f} — экстремальная перепроданность ✅")
        if macd_cross == "bullish": details.append("MACD бычий кросс ✅")
        if support and price <= support * 1.015: details.append(f"Цена у поддержки ${support:,.2f} ✅")
        if bb_l and price <= bb_l * 1.01: details.append(f"Цена у нижней BB ✅")
        if cu4 >= 2: details.append(f"4H: {cu4} зелёных свечи подряд ✅")

        tp1 = price * 1.015
        tp2 = resistance * 0.99 if resistance and resistance > price else price * 1.04
        sl  = support * 0.985 if support else price * 0.97
        urgency = "🚀 СИЛЬНЫЙ" if score >= 6 else "🟢"

        msg = (
            f"{urgency} СИГНАЛ ВХОДА — TAO\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"💰 Цена: ${price:,.2f}  |  Счёт: {score:+d}/8\n"
            f"📉 RSI 1H: {rsi_1h}  |  RSI 4H: {rsi_4h}\n"
            f"📈 1H: {trend.get('direction')} | 4H: {d4}\n\n"
        )
        if details:
            msg += "✅ Подтверждения:\n" + "\n".join(f"  • {d}" for d in details) + "\n\n"
        msg += (
            f"💡 ЧТО ДЕЛАТЬ:\n"
            f"  Вход:  ~${price:,.2f}\n"
            f"  TP1:   ${tp1:,.2f} (+1.5%)\n"
            f"  TP2:   ${tp2:,.2f}\n"
            f"  Стоп:  ${sl:,.2f}\n\n"
            f"👉 «📊 Анализ TAO» — полный план"
        )
        await context.bot.send_message(chat_id=CHAT_ID, text=msg, reply_markup=_kb(alerts_enabled))
        _last_alert_ts   = now
        _last_signal_dir = "buy"
        _last_score      = score
        logger.info(f"BUY SIGNAL: score={score} price={price:.2f} rsi={rsi_1h}")

    # ── СИГНАЛ ВЫЙТИ ─────────────────────────────────────────────────────────
    elif score <= -4 and _last_signal_dir != "sell":
        if not cooldown_ok and _last_score <= -4:
            _last_score = score
            return

        details = []
        if rsi_1h > 65: details.append(f"RSI 1H = {rsi_1h} — перекуплен 🔴")
        if rsi_4h > 60: details.append(f"RSI 4H = {rsi_4h} — перекуплен на 4H 🔴")
        if stoch.get("k", 50) > 75: details.append(f"Stoch RSI = {stoch.get('k',50):.0f} — перекуплен 🔴")
        if macd_1h.get("cross") == "bearish": details.append("MACD медвежий кросс 🔴")
        if resistance and price >= resistance * 0.99: details.append(f"Цена у сопротивления ${resistance:,.2f} 🔴")
        if cd4 >= 2: details.append(f"4H: {cd4} красных свечи подряд 🔴")

        msg = (
            f"🔴 СИГНАЛ ВЫХОДА / НЕ ВХОДИТЬ — TAO\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"💰 Цена: ${price:,.2f}  |  Счёт: {score:+d}/8\n"
            f"RSI 1H: {rsi_1h}  |  RSI 4H: {rsi_4h}\n"
            f"4H тренд: {d4}\n\n"
        )
        if details:
            msg += "🔴 Причины:\n" + "\n".join(f"  • {d}" for d in details) + "\n\n"
        msg += (
            f"💡 ЧТО ДЕЛАТЬ:\n"
            f"  • Если в позиции — зафиксируй прибыль\n"
            f"  • Не открывай новые лонги\n"
            f"  • Жди следующего дна для входа\n\n"
            f"👉 «📊 Анализ TAO» — подробнее"
        )
        await context.bot.send_message(chat_id=CHAT_ID, text=msg, reply_markup=_kb(alerts_enabled))
        _last_alert_ts   = now
        _last_signal_dir = "sell"
        _last_score      = score
        logger.info(f"SELL SIGNAL: score={score} price={price:.2f} rsi={rsi_1h}")

    # Сброс направления когда сигнал нейтральный
    elif -3 < score < 3:
        if _last_signal_dir:
            _last_signal_dir = ""
        _last_score = score
    else:
        _last_score = score


# ─── команды ─────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📊 TAO Signal Bot v4 — Скальпинг\n"
        "━━━━━━━━━━━━━━━━━━━\n\n"
        "Я слежу за TAO 24/7 и сигналю:\n"
        "🟢 ВОЙТИ — когда цена у дна с подтверждениями\n"
        "🔴 ВЫЙТИ — когда цена у пика, риск разворота\n"
        "⚡ Быстрые алерты — резкие движения за 1 мин\n\n"
        "📊 Анализ TAO — полный отчёт прямо сейчас\n"
        "🪙 AI Монеты — сканер 7 монет\n"
        "📰 Новости — свежие новости TAO\n"
        "🔔 Алерты — вкл/выкл уведомления\n\n"
        "⚠️ Не финансовый совет. Торгуй осознанно.",
        reply_markup=_kb(alerts_enabled),
    )


async def cmd_analysis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Анализирую...")
    await run_analysis(context=context, update=update, symbol="TAOUSDT")


async def cmd_news(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Ищу новости...")
    try:
        news = get_all_news()
        lines = ["📰 НОВОСТИ TAO\n"]
        for item in news.get("tao_news", [])[:4]:
            icon = {"positive": "🟢", "negative": "🔴", "neutral": "⚪"}.get(item.get("sentiment", ""), "⚪")
            lines.append(f"{icon} {item['title'][:100]}")
            lines.append(f"  [{item['source']}]\n")
        for item in news.get("ai_news", [])[:2]:
            lines.append(f"🤖 {item['title'][:100]}")
        if not news.get("tao_news") and not news.get("ai_news"):
            lines.append("Новостей нет прямо сейчас.")
        await update.message.reply_text("\n".join(lines), reply_markup=_kb(alerts_enabled))
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")


async def cmd_ai_coins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⏳ Сканирую AI монеты...")
    buttons = []
    for symbol, name, emoji in AI_COINS:
        try:
            s = quick_scan_coin(symbol)
            price  = s.get("price", 0)
            change = s.get("change_24h", 0)
            rsi    = s.get("rsi_1h", 50)
            macd_x = s.get("macd", {}).get("cross", "none")
            d4     = s.get("trend_4h", {}).get("direction", "")
            cd4    = s.get("trend_4h", {}).get("consecutive_down", 0)

            sc = 0
            if rsi < 30:   sc += 2
            elif rsi < 42: sc += 1
            elif rsi > 70: sc -= 2
            elif rsi > 60: sc -= 1
            if macd_x == "bullish":  sc += 2
            elif macd_x == "bearish": sc -= 2
            if change > 3:  sc += 1
            elif change < -5: sc -= 1
            if d4 == "down": sc -= 2
            elif d4 == "up": sc += 1

            st = "🟢" if sc >= 3 else ("🟡" if sc >= 1 else ("🔴" if sc <= -3 else ("🟠" if sc <= -1 else "⚪")))
            cs = "+" if change > 0 else ""
            macd_s  = " ⚡" if macd_x == "bullish" else ""
            trend_s = " ↓↓" if (d4 == "down" and cd4 >= 3) else (" ↓" if d4 == "down" else "")
            label = f"{st} {emoji} {name}   ${price:,.4g}   {cs}{change:.1f}%   RSI {rsi}{macd_s}{trend_s}"
            buttons.append([InlineKeyboardButton(label, callback_data=f"coin_{symbol}")])
        except Exception:
            buttons.append([InlineKeyboardButton(f"⚪ {emoji} {name}   —", callback_data=f"coin_{symbol}")])

    await msg.edit_text(
        "🪙 AI МОНЕТЫ\nНажми — полный анализ\n\n"
        "🟢 войти  🟡 осторожно  🟠 ждать  🔴 не входить  ⚡ MACD кросс",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def cb_coin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    symbol = q.data.replace("coin_", "")
    name   = symbol.replace("USDT", "")
    await q.message.reply_text(f"⏳ Анализирую {name}...")
    try:
        data   = collect_all_data(symbol)
        signal = generate_signal(data, {})
        report = format_report(data, {}, signal)
        await q.message.reply_text(report, reply_markup=_kb(alerts_enabled))
    except Exception as e:
        await q.message.reply_text(f"❌ Ошибка: {str(e)[:100]}")


async def cmd_toggle_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global alerts_enabled
    alerts_enabled = not alerts_enabled
    s = "включены 🔔" if alerts_enabled else "выключены 🔕"
    await update.message.reply_text(f"Алерты {s}", reply_markup=_kb(alerts_enabled))


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 КАК ПОЛЬЗОВАТЬСЯ\n"
        "━━━━━━━━━━━━━━━━━━━\n\n"
        "🟢 СИГНАЛ ВХОДА (score ≥ +4)\n"
        "   Несколько индикаторов подтверждают дно:\n"
        "   RSI перепродан + у поддержки + MACD кросс\n"
        "   → Можно входить с чётким стопом\n\n"
        "🔴 СИГНАЛ ВЫХОДА (score ≤ -4)\n"
        "   Несколько индикаторов подтверждают пик:\n"
        "   RSI перекуплен + у сопротивления\n"
        "   → Фиксируй прибыль, не открывай лонги\n\n"
        "⚡ БЫСТРЫЕ АЛЕРТЫ (каждую 1 мин)\n"
        "   • Резкий дроп -1.5% — предупреждение\n"
        "   • Резкий рост +1.5% — предупреждение о пике\n"
        "   • Цена у поддержки/сопротивления\n\n"
        "📊 Полный анализ — каждые 6 часов + 9:00\n\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "СЧЁТ:\n"
        "+6..+8  🚀 СИЛЬНЫЙ ВХОД\n"
        "+4..+5  🟢 ВОЙТИ\n"
        "+2..+3  🟡 Присматривайся\n"
        "0..±1   ⚪ Ждать\n"
        "-2..-3  🟠 Готовься к выходу\n"
        "-4..-5  🔴 ВЫЙТИ / НЕ ВХОДИТЬ\n"
        "-6..-8  🔴 СИЛЬНЫЙ ВЫХОД\n\n"
        "⚠️ Не финансовый совет!",
        reply_markup=_kb(alerts_enabled),
    )


async def _ask_claude(question: str, market_data: dict = None) -> str:
    """Отправляет вопрос в Claude с контекстом рынка. Возвращает ответ."""
    claude = _get_claude()
    if not claude:
        return "⚠️ Claude API не подключён. Добавь ANTHROPIC_API_KEY в .env"

    # Считаем скор сигнала — главный факт для Claude
    signal_score = 0
    signal_label = "НЕЙТРАЛЬНЫЙ (ждать)"
    if market_data:
        try:
            signal_score = quick_signal_score(market_data)
            if signal_score >= 6:
                signal_label = f"СИЛЬНЫЙ СИГНАЛ ВХОДА (счёт {signal_score:+d}/8)"
            elif signal_score >= 4:
                signal_label = f"СИГНАЛ ВХОДА (счёт {signal_score:+d}/8)"
            elif signal_score >= 2:
                signal_label = f"СЛАБЫЙ СИГНАЛ ВХОДА (счёт {signal_score:+d}/8) — осторожно"
            elif signal_score <= -6:
                signal_label = f"СИЛЬНЫЙ СИГНАЛ ВЫХОДА (счёт {signal_score:+d}/8)"
            elif signal_score <= -4:
                signal_label = f"СИГНАЛ ВЫХОДА / НЕ ВХОДИТЬ (счёт {signal_score:+d}/8)"
            elif signal_score <= -2:
                signal_label = f"СЛАБЫЙ СИГНАЛ ВЫХОДА (счёт {signal_score:+d}/8) — осторожно"
            else:
                signal_label = f"НЕЙТРАЛЬНЫЙ, ждать (счёт {signal_score:+d}/8)"
        except Exception:
            pass

    # Собираем контекст рынка для Claude
    ctx = ""
    if market_data:
        tao   = market_data.get("tao", {})
        rsi1  = market_data.get("rsi_1h", 50)
        rsi4  = market_data.get("rsi_4h", 50)
        tr4   = market_data.get("trend_4h", {})
        tr1   = market_data.get("trend", {})
        lvl   = market_data.get("levels", {})
        ema_r = market_data.get("ema_ribbon", {})
        macd  = market_data.get("macd_1h", {})
        bb    = market_data.get("bb_1h", {})
        fg    = market_data.get("fear_greed", {})
        price = tao.get("price", 0)
        sup   = lvl.get("support", 0)
        res   = lvl.get("resistance", 0)

        # Контекст положения цены
        loc_note = ""
        if sup and res and price:
            rng = res - sup
            if rng > 0:
                pct_in_range = (price - sup) / rng * 100
                if pct_in_range >= 80:
                    loc_note = "⚠️ Цена в верхних 20% диапазона — зона продаж!"
                elif pct_in_range <= 20:
                    loc_note = "✅ Цена в нижних 20% диапазона — зона покупок"
                else:
                    loc_note = f"Цена в середине диапазона ({pct_in_range:.0f}%)"
            if res and price >= res * 0.99:
                loc_note = "🚫 Цена У СОПРОТИВЛЕНИЯ — не входить!"
            elif sup and price <= sup * 1.01:
                loc_note = "✅ Цена У ПОДДЕРЖКИ — потенциальный вход"

        ctx = (
            f"\n=== ДАННЫЕ РЫНКА TAO/USDT (РЕАЛЬНЫЕ, СЕЙЧАС) ===\n"
            f"Цена: ${price:,.2f}  |  Изменение 24ч: {tao.get('change_24h', 0):+.1f}%\n"
            f"RSI 1H: {rsi1}  |  RSI 4H: {rsi4}\n"
            f"Тренд 1H: {tr1.get('direction','?')} ({tr1.get('consecutive_down',0)}↓/{tr1.get('consecutive_up',0)}↑)\n"
            f"Тренд 4H: {tr4.get('direction','?')} ({tr4.get('consecutive_down',0)}↓/{tr4.get('consecutive_up',0)}↑)\n"
            f"Поддержка: ${sup:,.2f}  |  Сопротивление: ${res:,.2f}\n"
            f"Положение цены: {loc_note}\n"
            f"EMA 9/21/50: {ema_r.get('ema9',0):.2f}/{ema_r.get('ema21',0):.2f}/{ema_r.get('ema50',0):.2f}\n"
            f"MACD кросс: {macd.get('cross','none')}\n"
            f"BB нижняя: ${bb.get('lower',0):,.2f}  |  BB верхняя: ${bb.get('upper',0):,.2f}\n"
            f"Страх/жадность: {fg.get('value',50)} ({fg.get('label','')})\n"
            f"\n=== СИГНАЛ АЛГОРИТМА (НЕ МЕНЯТЬ) ===\n"
            f"ИТОГОВЫЙ ВЫВОД СИСТЕМЫ: {signal_label}\n"
            f"=== КОНЕЦ ДАННЫХ ===\n"
        )

    system = (
        "Ты — торговый ассистент по криптовалюте TAO/USDT (Bittensor). "
        "Тебе передаются РЕАЛЬНЫЕ рыночные данные и ИТОГОВЫЙ СИГНАЛ алгоритма. "
        "ВАЖНО: ты НИКОГДА не противоречишь итоговому сигналу алгоритма — он рассчитан по данным, ты его объясняешь. "
        "Если сигнал 'ВХОДА' — объясняй почему это вход. Если 'ВЫХОДА' — объясняй почему выход. "
        "Если пользователь переспрашивает или сомневается — держи позицию алгоритма, не меняй вывод. "
        "Отвечаешь коротко (3-5 предложений), чётко, по делу — как опытный трейдер другу. "
        "Не пишешь длинных объяснений — только суть и конкретные цифры из данных. "
        "Отвечаешь на русском. "
        "В конце одна строка: '⚠️ Не финансовый совет.'"
    )

    try:
        resp = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: claude.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=500,
                system=system,
                messages=[{"role": "user", "content": ctx + "\nВопрос пользователя: " + question}],
            )
        )
        return resp.content[0].text
    except Exception as e:
        logger.error(f"Claude API error: {e}")
        return f"⚠️ Ошибка Claude: {str(e)[:100]}"


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = update.message.text.strip()

    # ── кнопки меню ──────────────────────────────────────────────────────────
    if t == "📊 Анализ TAO":
        await update.message.reply_text("⏳ Анализирую...")
        await run_analysis(context=context, update=update, symbol="TAOUSDT")
        return
    elif t == "📰 Новости":
        await cmd_news(update, context)
        return
    elif t == "🪙 AI Монеты":
        await cmd_ai_coins(update, context)
        return
    elif "Алерты" in t:
        await cmd_toggle_alerts(update, context)
        return
    elif t == "⚙️ Помощь":
        await cmd_help(update, context)
        return

    # ── быстрые команды без AI ────────────────────────────────────────────────
    tl = t.lower()
    if any(w in tl for w in ("цена", "прайс", "price", "сколько стоит", "курс")):
        ticker = get_tao_ticker()
        p = ticker.get("price", 0)
        c = ticker.get("change_24h", 0)
        icon = "📈" if c > 0 else "📉"
        await update.message.reply_text(
            f"{icon} TAO: ${p:,.2f}  ({c:+.1f}% за 24ч)",
            reply_markup=_kb(alerts_enabled),
        )
        return

    if any(w in tl for w in ("анализ", "полный анализ", "сигнал сейчас")):
        await update.message.reply_text("⏳ Анализирую...")
        await run_analysis(context=context, update=update, symbol="TAOUSDT")
        return

    # ── всё остальное → Claude с контекстом рынка ────────────────────────────
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    # Собираем свежие данные для контекста (быстро — только тикер + кэш)
    market_data = None
    try:
        market_data = collect_all_data("TAOUSDT")
    except Exception:
        pass

    answer = await _ask_claude(t, market_data)
    await update.message.reply_text(answer, reply_markup=_kb(alerts_enabled))


# ─── запуск ───────────────────────────────────────────────────────────────────

async def post_init(application: Application):
    await application.bot.set_my_commands([
        BotCommand("start",    "Запустить"),
        BotCommand("analysis", "Анализ TAO"),
        BotCommand("coins",    "AI монеты"),
        BotCommand("news",     "Новости"),
        BotCommand("alerts",   "Вкл/выкл алерты"),
        BotCommand("help",     "Помощь"),
    ])
    await application.bot.set_chat_menu_button(menu_button=MenuButtonCommands())

    application.bot_data.setdefault("support", 0)
    application.bot_data.setdefault("resistance", 0)

    scheduler = AsyncIOScheduler()
    ctx = type("ctx", (), {"bot": application.bot, "bot_data": application.bot_data})()

    scheduler.add_job(price_watcher,  "interval", minutes=1,  kwargs={"context": ctx})
    scheduler.add_job(signal_scanner, "interval", minutes=10, kwargs={"context": ctx})
    scheduler.add_job(run_analysis,   "interval", hours=6,    kwargs={"context": ctx})
    scheduler.add_job(run_analysis,   "cron", hour=9, minute=0, kwargs={"context": ctx})

    scheduler.start()
    logger.info("Scheduler: price_watcher 1min / signal_scanner 10min / report 6h + 9:00")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    from telegram.error import Conflict, NetworkError, TimedOut
    err = context.error
    if isinstance(err, Conflict):
        logger.warning("Conflict (двойной запуск?) — ждём 45с и продолжаем")
        await asyncio.sleep(45)
    elif isinstance(err, (NetworkError, TimedOut)):
        logger.warning(f"Network error: {err} — ждём 10с")
        await asyncio.sleep(10)
    else:
        logger.error(f"Unhandled error: {err}")


def main():
    if not TELEGRAM_TOKEN:
        raise RuntimeError("TELEGRAM_TOKEN не задан — проверь .env")

    app = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .connect_timeout(20)
        .read_timeout(20)
        .write_timeout(20)
        .post_init(post_init)
        .build()
    )
    app.add_handler(CommandHandler("start",    cmd_start))
    app.add_handler(CommandHandler("analysis", cmd_analysis))
    app.add_handler(CommandHandler("coins",    cmd_ai_coins))
    app.add_handler(CommandHandler("news",     cmd_news))
    app.add_handler(CommandHandler("alerts",   cmd_toggle_alerts))
    app.add_handler(CommandHandler("help",     cmd_help))
    app.add_handler(CallbackQueryHandler(cb_coin, pattern=r"^coin_"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_error_handler(error_handler)

    logger.info("TAO Signal Bot v4 started")
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
