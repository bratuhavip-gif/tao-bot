"""
Signal engine — полный технический анализ TAO.

Структура скора (max ±10 → нормируется):
  Тренд / структура рынка  : ±3  (самый важный блок — гасит ложные сигналы)
  RSI 1H + 4H              : ±2
  MACD 1H                  : ±2
  Уровни / BB              : ±2
  BTC направление          : ±1
  Объём                    : ±1
  Страх/жадность           : ±1
  История (ATH distance)   : ±1
  Новости                  : ±1

DOWNTREND LOCK: если тренд сильно медвежий → максимальный сигнал не выше "осторожно".
"""


# ─── публичный интерфейс ────────────────────────────────────────────────────

def generate_signal(data: dict, news: dict) -> dict:
    score, reasons = 0, []

    tao        = data.get("tao", {})
    btc        = data.get("btc", {})
    price      = tao.get("price", 0)
    change_24h = tao.get("change_24h", 0)
    btc_change = btc.get("change_24h", 0)

    rsi_1h  = data.get("rsi_1h", 50)
    rsi_4h  = data.get("rsi_4h", 50)
    stoch   = data.get("stoch_rsi_1h", {})
    macd_1h = data.get("macd_1h", {})
    bb      = data.get("bb_1h", {})
    levels  = data.get("levels", {})
    fg      = data.get("fear_greed", {})
    history = data.get("history", {})
    weekly  = history.get("weekly", {})
    daily   = history.get("daily", {})
    trend   = data.get("trend", {})
    ob      = data.get("order_book", {})

    support    = levels.get("support", 0)
    resistance = levels.get("resistance", 0)
    fg_value   = fg.get("value", 50)
    ma20       = daily.get("ma20")
    ma50       = daily.get("ma50")
    ema200     = daily.get("ema200")
    pct_from_ath = weekly.get("pct_from_ath")

    # ── 1. ТРЕНД И СТРУКТУРА РЫНКА (±3) ─────────────────────────────────────
    trend_score, trend_reasons = _score_trend(price, trend, ma20, ma50, ema200, change_24h, daily)
    score += trend_score
    reasons += trend_reasons

    # ── 2. RSI (±2) ──────────────────────────────────────────────────────────
    s, r = _score_rsi(rsi_1h, rsi_4h, stoch)
    score += s; reasons += r

    # ── 3. MACD (±2) ─────────────────────────────────────────────────────────
    s, r = _score_macd(macd_1h)
    score += s; reasons += r

    # ── 4. УРОВНИ И BOLLINGER BANDS (±2) ─────────────────────────────────────
    s, r = _score_levels_bb(price, support, resistance, bb)
    score += s; reasons += r

    # ── 5. BTC (±1) ──────────────────────────────────────────────────────────
    s, r = _score_btc(btc_change)
    score += s; reasons += r

    # ── 6. ОБЪЁМ И ORDER BOOK (±1) ───────────────────────────────────────────
    s, r = _score_volume(levels, change_24h, ob)
    score += s; reasons += r

    # ── 7. СТРАХ / ЖАДНОСТЬ (±1) ─────────────────────────────────────────────
    s, r = _score_fg(fg_value, fg)
    score += s; reasons += r

    # ── 8. ИСТОРИЯ / ATH (±1) ────────────────────────────────────────────────
    s, r = _score_history(pct_from_ath, weekly)
    score += s; reasons += r

    # ── 9. НОВОСТИ (±1) ──────────────────────────────────────────────────────
    s, r = _score_news(news)
    score += s; reasons += r

    # ── DOWNTREND LOCK ────────────────────────────────────────────────────────
    # Если тренд сильно медвежий — зажимаем максимальный сигнал до +1
    downtrend_locked = False
    if trend_score <= -2:
        if score > 1:
            score = 1
            reasons.append("🔒 Сигнал зажат: тренд нисходящий — не входи против рынка")
            downtrend_locked = True

    score = max(-10, min(10, score))

    plan = _build_action_plan(score, price, support, resistance, rsi_1h, rsi_4h,
                              btc_change, change_24h, fg_value, weekly, daily,
                              bb, trend, downtrend_locked)

    if score >= 5:
        signal = "🟢 СИЛЬНЫЙ ВХОД"
    elif score >= 3:
        signal = "🟢 ВХОДИТЬ"
    elif score >= 1:
        signal = "🟡 МОЖНО ВОЙТИ (осторожно)"
    elif score <= -5:
        signal = "🔴 ВЫХОДИТЬ / НЕ ВХОДИТЬ"
    elif score <= -3:
        signal = "🔴 НЕ ВХОДИТЬ"
    elif score <= -1:
        signal = "🟠 ЖДАТЬ"
    else:
        signal = "⚪ НЕЙТРАЛЬНО"

    return {
        "signal": signal,
        "score": score,
        "reasons": reasons,
        "plan": plan,
        "downtrend_locked": downtrend_locked,
        "trend_score": trend_score,
    }


def quick_signal_score(data: dict) -> int:
    """Быстрый скор для alert_scanner. Включает trend lock."""
    score = 0
    tao        = data.get("tao", {})
    btc        = data.get("btc", {})
    price      = tao.get("price", 0)
    btc_change = btc.get("change_24h", 0)
    rsi_1h     = data.get("rsi_1h", 50)
    levels     = data.get("levels", {})
    macd       = data.get("macd_1h", {})
    bb         = data.get("bb_1h", {})
    trend      = data.get("trend", {})
    daily      = data.get("history", {}).get("daily", {})
    support    = levels.get("support", 0)
    resistance = levels.get("resistance", 0)
    ma20       = daily.get("ma20")
    ma50       = daily.get("ma50")
    ema200     = daily.get("ema200")

    # BTC
    if btc_change > 2:   score += 1
    elif btc_change < -3: score -= 2
    elif btc_change < 0:  score -= 1

    # RSI
    if rsi_1h < 30:   score += 2
    elif rsi_1h < 42: score += 1
    elif rsi_1h > 70: score -= 2
    elif rsi_1h > 60: score -= 1

    # MACD
    cross = macd.get("cross", "none")
    if cross == "bullish":  score += 2
    elif cross == "bearish": score -= 2

    # Уровни
    if support and price and price <= support * 1.02:  score += 2
    if resistance and price and price >= resistance * 0.98: score -= 2

    # BB
    bb_lower = bb.get("lower", 0)
    bb_upper = bb.get("upper", 0)
    if bb_lower and price and price <= bb_lower * 1.01: score += 1
    if bb_upper and price and price >= bb_upper * 0.99: score -= 1

    # Тренд (краткосрочный: серия lower lows)
    trend_score = _quick_trend_score(trend, price, ma20, ma50, ema200)
    score += trend_score

    # Downtrend lock
    if trend_score <= -2 and score > 1:
        score = 1

    return max(-10, min(10, score))


def format_report(data: dict, news: dict, signal_data: dict) -> str:
    tao        = data.get("tao", {})
    btc        = data.get("btc", {})
    levels     = data.get("levels", {})
    history    = data.get("history", {})
    weekly     = history.get("weekly", {})
    daily      = history.get("daily", {})
    rsi_1h     = data.get("rsi_1h", 50)
    rsi_4h     = data.get("rsi_4h", 50)
    stoch      = data.get("stoch_rsi_1h", {})
    macd_1h    = data.get("macd_1h", {})
    bb         = data.get("bb_1h", {})
    trend      = data.get("trend", {})
    ob         = data.get("order_book", {})
    timestamp  = data.get("timestamp", "")
    price      = tao.get("price", 0)
    change_24h = tao.get("change_24h", 0)
    volume     = tao.get("volume_24h", 0)
    high       = tao.get("high_24h", 0)
    low        = tao.get("low_24h", 0)
    btc_price  = btc.get("price", 0)
    btc_change = btc.get("change_24h", 0)
    support    = levels.get("support", 0)
    resistance = levels.get("resistance", 0)
    plan       = signal_data.get("plan", {})
    ma20       = daily.get("ma20")
    ma50       = daily.get("ma50")
    ema200     = daily.get("ema200")

    change_emoji = "📈" if change_24h > 0 else "📉"
    btc_emoji    = "📈" if btc_change > 0 else "📉"
    symbol_name  = data.get("symbol", "TAOUSDT").replace("USDT", "")

    macd_cross     = macd_1h.get("cross", "none")
    macd_cross_str = {"bullish": "✅ бычий кросс", "bearish": "🔴 медвежий кросс"}.get(macd_cross, "⚪ нейтрал")

    lines = [
        f"━━━━━━━━━━━━━━━━━━━",
        f"📊 {symbol_name}/USDT | {timestamp}",
        f"━━━━━━━━━━━━━━━━━━━",
        f"",
        f"💰 {symbol_name}: ${price:,.4g}  {change_emoji} {change_24h:+.1f}% за 24ч",
        f"📊 High: ${high:,.4g}  |  Low: ${low:,.4g}",
        f"💵 Объём: ${volume/1_000_000:.1f}M",
        f"",
        f"{btc_emoji} BTC: ${btc_price:,.0f} ({btc_change:+.1f}%)",
        f"",
        f"── ИНДИКАТОРЫ ──────────────────",
        f"📉 RSI 1H: {rsi_1h}  |  RSI 4H: {rsi_4h}",
    ]

    stoch_k = stoch.get("k", 0)
    stoch_d = stoch.get("d", 0)
    stoch_zone = "перепродан" if stoch_k < 20 else ("перекуплен" if stoch_k > 80 else "норма")
    lines.append(f"🎲 Stoch RSI: K={stoch_k:.0f} D={stoch_d:.0f} ({stoch_zone})")

    lines.append(f"⚡ MACD 1H: {macd_cross_str}")
    lines.append(f"🎯 Поддержка: ${support:,.4g}  |  Сопр: ${resistance:,.4g}")

    bb_lower = bb.get("lower", 0)
    bb_mid   = bb.get("mid", 0)
    bb_upper = bb.get("upper", 0)
    if bb_lower and bb_upper:
        if price <= bb_lower * 1.01:
            bb_pos = "▼ у нижней полосы (перепродан)"
        elif price >= bb_upper * 0.99:
            bb_pos = "▲ у верхней полосы (перекуплен)"
        else:
            bb_pos = f"середина диапазона"
        lines.append(f"📐 BB: ${bb_lower:,.2f} — ${bb_upper:,.2f}  ({bb_pos})")

    if ma20 and ma50:
        ma20_e = "✅" if price > ma20 else "🔴"
        ma50_e = "✅" if price > ma50 else "🔴"
        lines.append(f"{ma20_e} MA20: ${ma20:,.2f}  |  {ma50_e} MA50: ${ma50:,.2f}")
    if ema200:
        ema_e = "✅" if price > ema200 else "🔴"
        lines.append(f"{ema_e} EMA200d: ${ema200:,.2f}")

    # Тренд
    trend_dir   = trend.get("direction", "")
    trend_str   = trend.get("desc", "")
    consec_down = trend.get("consecutive_down", 0)
    consec_up   = trend.get("consecutive_up", 0)
    if trend_dir == "down":
        lines.append(f"📉 Тренд: НИСХОДЯЩИЙ ↓  ({consec_down} свечей подряд вниз)")
    elif trend_dir == "up":
        lines.append(f"📈 Тренд: ВОСХОДЯЩИЙ ↑  ({consec_up} свечей подряд вверх)")
    else:
        lines.append(f"↔️ Тренд: боковик")

    # Order book
    bid_ask = ob.get("bid_ask_ratio", 0)
    if bid_ask:
        ob_str = "покупатели доминируют" if bid_ask > 1.2 else ("продавцы доминируют" if bid_ask < 0.8 else "баланс")
        lines.append(f"📖 Стакан: {ob_str} (bid/ask {bid_ask:.2f})")

    # История
    if weekly:
        ath       = weekly.get("ath", 0)
        pct_ath   = weekly.get("pct_from_ath", 0)
        pct_atl   = weekly.get("pct_from_atl", 0)
        atl       = weekly.get("atl", 0)
        trend_4w  = weekly.get("trend_4w", 0)
        trend_12w = weekly.get("trend_12w", 0)
        cycle     = weekly.get("cycle_phase", "")
        lines += [
            f"",
            f"── ИСТОРИЯ ─────────────────────",
            f"🏆 ATH: ${ath:,.2f}  ({pct_ath:+.1f}% от пика)",
            f"📉 ATL: ${atl:,.2f}  (+{pct_atl:.1f}% от дна)",
            f"📅 Тренд 4 нед: {trend_4w:+.1f}%  |  12 нед: {trend_12w:+.1f}%",
            f"🔄 Фаза: {cycle}",
        ]
        key_sup = weekly.get("key_supports", [])
        key_res = weekly.get("key_resistances", [])
        if key_sup:
            lines.append(f"🟢 Историч. поддержки: {'  |  '.join([f'${s:,.0f}' for s in key_sup[:3]])}")
        if key_res:
            lines.append(f"🔴 Историч. сопротивл: {'  |  '.join([f'${r:,.0f}' for r in key_res[:3]])}")

    lines += [
        f"",
        f"━━━━━━━━━━━━━━━━━━━",
        f"🎯 СИГНАЛ: {signal_data['signal']}  (счёт {signal_data['score']:+d}/10)",
        f"━━━━━━━━━━━━━━━━━━━",
        f"",
        f"📋 ФАКТОРЫ:",
    ]
    for r in signal_data["reasons"]:
        lines.append(f"  {r}")

    if plan:
        lines += [
            f"",
            f"── ПЛАН ДЕЙСТВИЙ ───────────────",
            f"🟢 Вход:        {plan.get('entry', '—')}",
            f"🎯 Тейк-профит: {plan.get('take_profit', '—')}",
            f"🛑 Стоп-лосс:   {plan.get('stop_loss', '—')}",
            f"⏰ Перепроверь: {plan.get('recheck', '—')}",
            f"⚠️ Риск:        {plan.get('risk', '—')}",
        ]
        hctx = plan.get("history_context", "")
        if hctx and hctx != "—":
            lines.append(f"📜 Контекст:    {hctx}")

    tao_news = news.get("tao_news", [])
    ai_news  = news.get("ai_news", [])
    if tao_news or ai_news:
        lines += [f"", f"── НОВОСТИ ─────────────────────"]
        for item in tao_news[:2]:
            sent = item.get("sentiment", "")
            sent_icon = {"positive": "🟢", "negative": "🔴", "neutral": "⚪"}.get(sent, "")
            lines.append(f"  {sent_icon} 🔵 {item['title'][:85]}")
        for item in ai_news[:2]:
            lines.append(f"  🤖 {item['title'][:85]}")

    lines += [
        f"",
        f"━━━━━━━━━━━━━━━━━━━",
        f"⚠️ Не финансовый совет. Торгуй осознанно.",
    ]
    return "\n".join(lines)


# ─── блоки скоринга ─────────────────────────────────────────────────────────

def _score_trend(price, trend, ma20, ma50, ema200, change_24h, daily):
    score, reasons = 0, []

    direction    = trend.get("direction", "")
    consec_down  = trend.get("consecutive_down", 0)
    consec_up    = trend.get("consecutive_up", 0)
    lower_lows   = trend.get("lower_lows", False)
    higher_highs = trend.get("higher_highs", False)
    slope_1h     = trend.get("slope_1h", 0)

    # Серия нисходящих свечей подряд — главный фильтр
    if consec_down >= 4:
        score -= 3
        reasons.append(f"🔴 {consec_down} свечей подряд вниз — сильный нисходящий импульс, не входи")
    elif consec_down >= 2:
        score -= 2
        reasons.append(f"🟠 {consec_down} свечи подряд вниз — тренд против тебя")
    elif consec_up >= 3:
        score += 2
        reasons.append(f"✅ {consec_up} свечи подряд вверх — восходящий импульс")
    elif consec_up >= 2:
        score += 1
        reasons.append(f"🟡 {consec_up} свечи подряд вверх — слабый бычий импульс")

    # Lower lows / Higher highs (структура рынка)
    if lower_lows:
        score -= 1
        reasons.append("🔴 Структура рынка: lower lows — рынок делает новые минимумы")
    if higher_highs:
        score += 1
        reasons.append("✅ Структура рынка: higher highs — рынок делает новые максимумы")

    # MA-фильтр
    if price and ma20 and ma50:
        if price < ma20 < ma50:
            score -= 1
            reasons.append(f"🔴 Цена ниже MA20 и MA50 — медвежий тренд подтверждён")
        elif price > ma20 > ma50:
            score += 1
            reasons.append(f"✅ Цена выше MA20 и MA50 — бычий тренд")

    if price and ema200:
        if price < ema200 * 0.98:
            score -= 1
            reasons.append(f"🔴 Цена ниже EMA200 — долгосрочный медвежий тренд")
        elif price > ema200 * 1.02:
            score += 1
            reasons.append(f"✅ Цена выше EMA200 — долгосрочный бычий тренд")

    score = max(-3, min(3, score))
    return score, reasons


def _quick_trend_score(trend, price, ma20, ma50, ema200):
    score = 0
    consec_down = trend.get("consecutive_down", 0)
    consec_up   = trend.get("consecutive_up", 0)
    lower_lows  = trend.get("lower_lows", False)

    if consec_down >= 4:   score -= 3
    elif consec_down >= 2: score -= 2
    elif consec_up >= 3:   score += 2
    elif consec_up >= 2:   score += 1

    if lower_lows: score -= 1

    if price and ma20 and ma50:
        if price < ma20 < ma50: score -= 1
        elif price > ma20 > ma50: score += 1

    return max(-3, min(3, score))


def _score_rsi(rsi_1h, rsi_4h, stoch):
    score, reasons = 0, []

    if rsi_1h < 25:
        score += 2
        reasons.append(f"✅ RSI 1H = {rsi_1h} — экстремальная перепроданность, разворот очень вероятен")
    elif rsi_1h < 35:
        score += 2
        reasons.append(f"✅ RSI 1H = {rsi_1h} — перепродан, отскок вероятен")
    elif rsi_1h < 45:
        score += 1
        reasons.append(f"🟡 RSI 1H = {rsi_1h} — зона покупки")
    elif rsi_1h > 80:
        score -= 2
        reasons.append(f"🔴 RSI 1H = {rsi_1h} — экстремальная перекупленность")
    elif rsi_1h > 70:
        score -= 2
        reasons.append(f"🔴 RSI 1H = {rsi_1h} — перекуплен, не входи в лонг")
    elif rsi_1h > 60:
        score -= 1
        reasons.append(f"🟡 RSI 1H = {rsi_1h} — немного перегрет")
    else:
        reasons.append(f"⚪ RSI 1H = {rsi_1h} — нейтральная зона")

    if rsi_4h < 35:
        score += 1
        reasons.append(f"✅ RSI 4H = {rsi_4h} — на 4H тоже перепродан (двойное подтверждение)")
    elif rsi_4h > 65:
        score -= 1
        reasons.append(f"🔴 RSI 4H = {rsi_4h} — на 4H перегрет")

    # Stochastic RSI
    k = stoch.get("k", 50)
    d = stoch.get("d", 50)
    if k < 20 and d < 20:
        score += 1
        reasons.append(f"✅ Stoch RSI: K={k:.0f} D={d:.0f} — перепродан, кросс вверх возможен")
    elif k > 80 and d > 80:
        score -= 1
        reasons.append(f"🔴 Stoch RSI: K={k:.0f} D={d:.0f} — перекуплен")
    elif k > d and k < 30:
        score += 1
        reasons.append(f"✅ Stoch RSI кросс вверх в зоне перепроданности")

    return max(-2, min(2, score)), reasons


def _score_macd(macd_1h):
    score, reasons = 0, []
    cross    = macd_1h.get("cross", "none")
    macd_val = macd_1h.get("macd", 0)
    sig_val  = macd_1h.get("signal", 0)
    hist     = macd_1h.get("histogram", 0)
    hist_dir = macd_1h.get("histogram_dir", "")  # growing / shrinking

    if cross == "bullish":
        score += 2
        reasons.append("✅ MACD 1H бычий кросс — разворот импульса вверх")
    elif cross == "bearish":
        score -= 2
        reasons.append("🔴 MACD 1H медвежий кросс — разворот импульса вниз")
    elif macd_val > sig_val and macd_val > 0:
        if hist_dir == "growing":
            score += 1
            reasons.append("🟡 MACD растёт выше нуля — бычий импульс усиливается")
        else:
            reasons.append("⚪ MACD выше нуля, импульс ослабевает")
    elif macd_val < sig_val and macd_val < 0:
        if hist_dir == "shrinking":
            score -= 1
            reasons.append("🔴 MACD падает ниже нуля — медвежий импульс нарастает")
        else:
            reasons.append("⚪ MACD ниже нуля, но импульс замедляется")
    else:
        reasons.append("⚪ MACD нейтрален")

    return max(-2, min(2, score)), reasons


def _score_levels_bb(price, support, resistance, bb):
    score, reasons = 0, []

    if support and resistance and price:
        zone_pct = ((price - support) / (resistance - support) * 100) if (resistance - support) > 0 else 50
        if price <= support * 1.015:
            score += 2
            reasons.append(f"✅ Цена у поддержки ${support:,.2f} — хорошая точка входа")
        elif price >= resistance * 0.985:
            score -= 2
            reasons.append(f"🔴 Цена у сопротивления ${resistance:,.2f} — не входи")
        else:
            reasons.append(f"⚪ Цена в диапазоне ${support:,.2f}–${resistance:,.2f} ({zone_pct:.0f}% от низа)")

    # Bollinger Bands
    bb_lower = bb.get("lower", 0)
    bb_upper = bb.get("upper", 0)
    bb_mid   = bb.get("mid", 0)
    bb_width = bb.get("width_pct", 0)

    if bb_lower and bb_upper and price:
        if price <= bb_lower:
            score += 1
            reasons.append(f"✅ Цена НИЖЕ нижней BB (${bb_lower:,.2f}) — экстремальная перепроданность")
        elif price <= bb_lower * 1.01:
            score += 1
            reasons.append(f"✅ Цена у нижней полосы BB — зона покупки")
        elif price >= bb_upper:
            score -= 1
            reasons.append(f"🔴 Цена ВЫШЕ верхней BB (${bb_upper:,.2f}) — перекуплена")
        elif price >= bb_upper * 0.99:
            score -= 1
            reasons.append(f"🔴 Цена у верхней полосы BB — зона продажи")

        if bb_width and bb_width < 3:
            reasons.append(f"⚡ BB сжались (ширина {bb_width:.1f}%) — ожидается сильное движение")

    return max(-2, min(2, score)), reasons


def _score_btc(btc_change):
    score, reasons = 0, []
    if btc_change > 3:
        score += 1
        reasons.append(f"✅ BTC +{btc_change:.1f}% — рынок растёт")
    elif btc_change > 0:
        reasons.append(f"⚪ BTC +{btc_change:.1f}% — слабый рост")
    elif btc_change < -4:
        score -= 1
        reasons.append(f"🔴 BTC {btc_change:.1f}% — крипторынок под давлением")
    elif btc_change < -2:
        score -= 1
        reasons.append(f"🟠 BTC {btc_change:.1f}% — осторожно")
    else:
        reasons.append(f"🟡 BTC {btc_change:.1f}% — лёгкое снижение")
    return max(-1, min(1, score)), reasons


def _score_volume(levels, change_24h, ob):
    score, reasons = 0, []
    vol_spike = levels.get("volume_spike", False)
    vol_ratio = levels.get("volume_ratio", 1.0)

    if vol_spike:
        if change_24h > 0:
            score += 1
            reasons.append(f"✅ Объём выше среднего ×{vol_ratio:.1f} на росте — движение подтверждено")
        else:
            score -= 1
            reasons.append(f"🔴 Объём выше среднего ×{vol_ratio:.1f} на падении — давление продавцов")
    else:
        reasons.append(f"⚪ Объём обычный")

    bid_ask = ob.get("bid_ask_ratio", 0)
    if bid_ask > 1.3:
        score += 0  # не добавляем, но показываем в репорте
    elif bid_ask and bid_ask < 0.7:
        score -= 0

    return max(-1, min(1, score)), reasons


def _score_fg(fg_value, fg):
    score, reasons = 0, []
    if fg_value < 20:
        score += 1
        reasons.append(f"✅ Индекс страха: {fg_value} (Extreme Fear) — рынок капитулирует")
    elif fg_value < 30:
        score += 1
        reasons.append(f"✅ Индекс страха: {fg_value} (Fear) — хорошее время для входа")
    elif fg_value > 80:
        score -= 1
        reasons.append(f"🔴 Индекс жадности: {fg_value} (Extreme Greed) — рынок перегрет")
    elif fg_value > 70:
        score -= 1
        reasons.append(f"🟠 Жадность: {fg_value} — рынок горячий, осторожно")
    else:
        reasons.append(f"⚪ Страх/жадность: {fg_value} ({fg.get('label', '')})")
    return max(-1, min(1, score)), reasons


def _score_history(pct_from_ath, weekly):
    score, reasons = 0, []
    if pct_from_ath is None:
        return 0, reasons
    if pct_from_ath > -15:
        score -= 1
        reasons.append(f"🔴 В {abs(pct_from_ath):.0f}% от ATH — зона распределения")
    elif pct_from_ath > -35:
        reasons.append(f"🟡 В {abs(pct_from_ath):.0f}% от ATH — коррекция")
    elif pct_from_ath > -60:
        score += 1
        reasons.append(f"✅ В {abs(pct_from_ath):.0f}% от ATH — исторически выгодная зона")
    else:
        score += 1
        reasons.append(f"✅ В {abs(pct_from_ath):.0f}% от ATH — зона капитуляции")
    return max(-1, min(1, score)), reasons


def _score_news(news):
    score, reasons = 0, []
    tao_news = news.get("tao_news", [])
    ai_news  = news.get("ai_news", [])

    pos = sum(1 for n in tao_news if n.get("sentiment") == "positive")
    neg = sum(1 for n in tao_news if n.get("sentiment") == "negative")

    if pos > neg and tao_news:
        score += 1
        reasons.append(f"✅ Позитивные новости TAO ({pos} из {len(tao_news)})")
    elif neg > pos and tao_news:
        score -= 1
        reasons.append(f"🔴 Негативные новости TAO ({neg} из {len(tao_news)})")
    elif tao_news:
        reasons.append(f"⚪ Нейтральные новости TAO")

    if ai_news:
        reasons.append(f"⚪ AI-новости есть ({len(ai_news)}) — косвенный позитив")

    return max(-1, min(1, score)), reasons


# ─── план действий ──────────────────────────────────────────────────────────

def _build_action_plan(score, price, support, resistance, rsi_1h, rsi_4h,
                       btc_change, change_24h, fg_value, weekly, daily,
                       bb, trend, downtrend_locked):
    pct_from_ath   = weekly.get("pct_from_ath")
    trend_4w       = weekly.get("trend_4w", 0)
    trend_30d      = daily.get("trend_30d", 0)
    ma20           = daily.get("ma20")
    ma50           = daily.get("ma50")
    ema200         = daily.get("ema200")
    ath            = weekly.get("ath", 0)
    key_resistances = weekly.get("key_resistances", [])
    key_supports   = weekly.get("key_supports", [])
    consec_down    = trend.get("consecutive_down", 0)
    bb_lower       = bb.get("lower", 0)
    bb_upper       = bb.get("upper", 0)

    # Точка входа
    if downtrend_locked or consec_down >= 3:
        entry = f"НЕ ВХОДИТЬ — тренд нисходящий ({consec_down} свечей вниз). Жди разворота: две зелёных свечи подряд + RSI < 40"
    elif score >= 5:
        entry = f"Сейчас (~${price:,.1f}) — условия оптимальные"
    elif score >= 3:
        # Откат к поддержке имеет смысл только если поддержка НИЖЕ текущей цены
        if support and support < price * 0.99:
            entry = f"Сейчас малой частью, основную — при откате до ${support*1.01:,.1f}"
        else:
            entry = f"Малой частью сейчас (~${price:,.1f}), жди подтверждения"
    elif support and support < price * 0.97:
        # Поддержка заметно ниже — имеет смысл ждать
        entry = f"Жди отката до ${support*1.01:,.1f}–${support*1.02:,.1f}"
    elif bb_lower and bb_lower < price * 0.97:
        entry = f"Жди отката к нижней BB (${bb_lower:,.1f})"
    elif ma50 and ma50 < price * 0.95:
        entry = f"Жди отката к MA50 (${ma50:,.1f})"
    else:
        entry = f"Сейчас (~${price:,.1f}) или не входить — жди разворота с объёмом"

    # Тейк-профит
    if resistance and price < resistance * 0.95:
        tp1 = round(price * 1.025, 1)
        tp2 = round(resistance * 0.98, 1)
        take_profit = f"TP1: ${tp1:,.1f} (+2.5%) → TP2: ${tp2:,.1f} (сопр)"
    else:
        next_res = next((r for r in sorted(key_resistances) if r > price * 1.03), None)
        tp1 = round(price * 1.025, 1)
        if next_res:
            take_profit = f"TP1: ${tp1:,.1f} (+2.5%) → TP2: ${next_res:,.1f} (историч.)"
        else:
            take_profit = f"TP1: ${tp1:,.1f} (+2.5%) → TP2: ${price*1.05:,.1f} (+5%)"

    # Стоп-лосс
    if support and price > support:
        stop_loss = f"${support*0.985:,.1f} (ниже поддержки)"
    elif bb_lower and price > bb_lower:
        stop_loss = f"${bb_lower*0.99:,.1f} (ниже нижней BB)"
    elif ma50 and price > ma50:
        stop_loss = f"${ma50*0.98:,.1f} (ниже MA50)"
    else:
        stop_loss = f"${price*0.97:,.1f} (-3%)"

    # Когда перепроверить
    if consec_down >= 3:
        recheck = "Каждые 30 мин — смотри на разворот свечей"
    elif score >= 5:
        recheck = "Каждые 30–60 мин — активная фаза"
    elif score >= 3:
        recheck = "Через 1–2 часа"
    elif rsi_1h > 65:
        recheck = "Через 2–3 часа — ждёшь RSI < 55"
    elif btc_change < -2:
        recheck = "Когда BTC стабилизируется"
    else:
        recheck = "Через 2–4 часа"

    # Контекст
    parts = []
    if pct_from_ath is not None:
        parts.append(f"От ATH {abs(pct_from_ath):.0f}% вниз (ATH: ${ath:,.0f})")
    if trend_4w:
        parts.append(f"Тренд 4 нед: {trend_4w:+.1f}%")
    if trend_30d:
        parts.append(f"30 дн: {trend_30d:+.1f}%")
    if weekly.get("cycle_phase"):
        parts.append(weekly["cycle_phase"])
    history_context = " | ".join(parts) if parts else "—"

    # Риск
    if consec_down >= 3 or downtrend_locked:
        risk = "ВЫСОКИЙ — активный нисходящий тренд"
    elif rsi_1h > 70 and change_24h > 5:
        risk = "ВЫСОКИЙ — RSI перегрет + сильный рост за день"
    elif pct_from_ath and pct_from_ath > -15:
        risk = "ВЫСОКИЙ — цена у исторических максимумов"
    elif score >= 5 and fg_value < 40:
        risk = "НИЗКИЙ — все факторы за рост"
    elif btc_change < -3:
        risk = "ВЫСОКИЙ — BTC падает"
    elif score >= 3:
        risk = "СРЕДНИЙ — условия неплохие"
    else:
        risk = "СРЕДНИЙ-ВЫСОКИЙ — нет чёткого направления"

    return {
        "entry": entry,
        "take_profit": take_profit,
        "stop_loss": stop_loss,
        "recheck": recheck,
        "risk": risk,
        "history_context": history_context,
    }
