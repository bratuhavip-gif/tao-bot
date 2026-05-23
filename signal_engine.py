"""
Signal Engine v4 — логика профессионального скальпера.

Два режима сигнала:
  BUY  — цена у ДНА, импульс разворачивается вверх
  SELL — цена у ПИКА, импульс разворачивается вниз

Принципы:
  1. СТРУКТУРА рынка важнее всего: EMA ribbon + trend_4h + trend_1h
  2. Покупать только у поддержки, продавать только у сопротивления
  3. Подтверждение разворота: RSI + Stoch RSI + MACD + объём
  4. НИКОГДА не покупать на пике, не продавать на дне
  5. BTC контекст — рынок не ходит против BTC
  6. Простые и понятные сообщения для начинающего трейдера

Скоринг:
  +: поддержка / перепроданность / разворот вверх
  -: сопротивление / перекупленность / разворот вниз

  BUY  сигнал: score >= +4  (несколько подтверждений снизу)
  SELL сигнал: score <= -4  (несколько подтверждений сверху)
  Зона накопления: +2..+3   (присматривайся)
  Зона распределения: -2..-3 (готовься к выходу)
"""


# ─── публичный интерфейс ──────────────────────────────────────────────────────

def generate_signal(data: dict, news: dict = None) -> dict:
    price      = data.get("tao", {}).get("price", 0)
    change_24h = data.get("tao", {}).get("change_24h", 0)
    btc_change = data.get("btc", {}).get("change_24h", 0)
    rsi_1h     = data.get("rsi_1h", 50)
    rsi_4h     = data.get("rsi_4h", 50)
    stoch      = data.get("stoch_rsi_1h", {})
    macd_1h    = data.get("macd_1h", {})
    macd_4h    = data.get("macd_4h", {})
    bb         = data.get("bb_1h", {})
    levels     = data.get("levels", {})
    ema_r      = data.get("ema_ribbon", {})
    vwap       = data.get("vwap", 0)
    momentum   = data.get("momentum", 0)
    vol_prof   = data.get("volume_profile", {})
    trend      = data.get("trend", {})
    trend_4h   = data.get("trend_4h", {})
    ob         = data.get("order_book", {})
    fg         = data.get("fear_greed", {})
    history    = data.get("history", {})
    weekly     = history.get("weekly", {})
    daily      = history.get("daily", {})
    bottom     = data.get("bottom", {})

    support    = levels.get("support", 0)
    resistance = levels.get("resistance", 0)
    fg_value   = fg.get("value", 50)
    ma20       = daily.get("ma20")
    ma50       = daily.get("ma50")
    ema200     = daily.get("ema200")
    pct_ath    = weekly.get("pct_from_ath")

    score   = 0
    reasons = []

    # ── 1. СТРУКТУРА РЫНКА: EMA ribbon + тренды (±4) ─────────────────────────
    s, r = _score_structure(price, ema_r, trend, trend_4h, ma20, ma50, ema200, vwap)
    score += s; reasons += r

    # ── 2. ГДЕ ЦЕНА: поддержка / сопротивление / BB (±3) ─────────────────────
    s, r = _score_location(price, support, resistance, bb)
    score += s; reasons += r

    # ── 3. RSI + STOCH RSI: перепроданность / перекупленность (±3) ───────────
    s, r = _score_momentum_oscillators(rsi_1h, rsi_4h, stoch, momentum)
    score += s; reasons += r

    # ── 4. MACD: разворот импульса (±2) ──────────────────────────────────────
    s, r = _score_macd(macd_1h, macd_4h)
    score += s; reasons += r

    # ── 5. ОБЪЁМ: подтверждение движения (±2) ────────────────────────────────
    s, r = _score_volume(vol_prof, ob, change_24h)
    score += s; reasons += r

    # ── 6. BTC контекст (±1) ─────────────────────────────────────────────────
    s, r = _score_btc(btc_change)
    score += s; reasons += r

    # ── 7. СТРАХ/ЖАДНОСТЬ + история (±1) ─────────────────────────────────────
    s, r = _score_market_context(fg_value, pct_ath)
    score += s; reasons += r

    # ── ЖЁСТКИЕ ПРАВИЛА — нельзя нарушать ────────────────────────────────────
    dir_4h         = trend_4h.get("direction", "")
    consec_down_4h = trend_4h.get("consecutive_down", 0)
    consec_up_4h   = trend_4h.get("consecutive_up", 0)
    consec_down_1h = trend.get("consecutive_down", 0)
    consec_up_1h   = trend.get("consecutive_up", 0)

    hard_block_buy  = False
    hard_block_sell = False

    # Нельзя покупать когда 4H тренд вниз (3+ свечей)
    if dir_4h == "down" and consec_down_4h >= 3:
        if score > 0:
            score = 0
            reasons.append("🚫 БЛОК ПОКУПКИ: 4H нисходящий тренд — не торгуй против рынка")
            hard_block_buy = True

    # Нельзя покупать у сопротивления (это пик — там надо продавать)
    if resistance and price and price >= resistance * 0.988 and score > 0:
        score = min(score, -1)
        reasons.append(f"🚫 БЛОК ПОКУПКИ: цена у сопротивления ${resistance:,.2f} — это зона продажи")
        hard_block_buy = True

    # Нельзя продавать когда 4H тренд вверх (3+ свечей)
    if dir_4h == "up" and consec_up_4h >= 3:
        if score < 0:
            score = 0
            reasons.append("🚫 БЛОК ПРОДАЖИ: 4H восходящий тренд — не шорти против роста")
            hard_block_sell = True

    score = max(-8, min(8, score))

    # ── ОПРЕДЕЛЯЕМ СИГНАЛ ─────────────────────────────────────────────────────
    action, emoji, urgency = _classify_signal(score, price, support, resistance,
                                               rsi_1h, rsi_4h, bottom)

    # ── ПЛАН ВХОДА/ВЫХОДА ────────────────────────────────────────────────────
    plan = _build_plan(score, price, support, resistance, rsi_1h, rsi_4h,
                       bb, ema_r, trend, trend_4h, weekly, daily)

    return {
        "signal":         f"{emoji} {action}",
        "action":         action,
        "emoji":          emoji,
        "score":          score,
        "urgency":        urgency,
        "reasons":        reasons,
        "plan":           plan,
        "hard_block_buy": hard_block_buy,
        "hard_block_sell": hard_block_sell,
    }


def quick_signal_score(data: dict) -> int:
    """Быстрый скор для price_watcher. Та же логика, без отчёта."""
    price      = data.get("tao", {}).get("price", 0)
    change_24h = data.get("tao", {}).get("change_24h", 0)
    btc_change = data.get("btc", {}).get("change_24h", 0)
    rsi_1h     = data.get("rsi_1h", 50)
    rsi_4h     = data.get("rsi_4h", 50)
    stoch      = data.get("stoch_rsi_1h", {})
    macd_1h    = data.get("macd_1h", {})
    macd_4h    = data.get("macd_4h", {})
    bb         = data.get("bb_1h", {})
    levels     = data.get("levels", {})
    ema_r      = data.get("ema_ribbon", {})
    vwap       = data.get("vwap", 0)
    momentum   = data.get("momentum", 0)
    vol_prof   = data.get("volume_profile", {})
    trend      = data.get("trend", {})
    trend_4h   = data.get("trend_4h", {})
    ob         = data.get("order_book", {})
    fg         = data.get("fear_greed", {})
    weekly     = data.get("history", {}).get("weekly", {})
    daily      = data.get("history", {}).get("daily", {})
    ma20       = daily.get("ma20")
    ma50       = daily.get("ma50")
    ema200     = daily.get("ema200")

    support    = levels.get("support", 0)
    resistance = levels.get("resistance", 0)
    fg_value   = fg.get("value", 50)
    pct_ath    = weekly.get("pct_from_ath")

    score = 0
    score += _score_structure(price, ema_r, trend, trend_4h, ma20, ma50, ema200, vwap)[0]
    score += _score_location(price, support, resistance, bb)[0]
    score += _score_momentum_oscillators(rsi_1h, rsi_4h, stoch, momentum)[0]
    score += _score_macd(macd_1h, macd_4h)[0]
    score += _score_volume(vol_prof, ob, change_24h)[0]
    score += _score_btc(btc_change)[0]
    score += _score_market_context(fg_value, pct_ath)[0]

    dir_4h         = trend_4h.get("direction", "")
    consec_down_4h = trend_4h.get("consecutive_down", 0)
    consec_up_4h   = trend_4h.get("consecutive_up", 0)

    if dir_4h == "down" and consec_down_4h >= 3 and score > 0:
        score = 0
    if resistance and price and price >= resistance * 0.988 and score > 0:
        score = min(score, -1)
    if dir_4h == "up" and consec_up_4h >= 3 and score < 0:
        score = 0

    return max(-8, min(8, score))


# ─── форматирование отчёта ────────────────────────────────────────────────────

def format_report(data: dict, news: dict, signal_data: dict) -> str:
    tao       = data.get("tao", {})
    btc       = data.get("btc", {})
    price     = tao.get("price", 0)
    chg24     = tao.get("change_24h", 0)
    vol24     = tao.get("volume_24h", 0)
    high24    = tao.get("high_24h", 0)
    low24     = tao.get("low_24h", 0)
    btc_p     = btc.get("price", 0)
    btc_c     = btc.get("change_24h", 0)
    rsi_1h    = data.get("rsi_1h", 50)
    rsi_4h    = data.get("rsi_4h", 50)
    stoch     = data.get("stoch_rsi_1h", {})
    macd_1h   = data.get("macd_1h", {})
    macd_4h   = data.get("macd_4h", {})
    bb        = data.get("bb_1h", {})
    levels    = data.get("levels", {})
    ema_r     = data.get("ema_ribbon", {})
    vwap      = data.get("vwap", 0)
    momentum  = data.get("momentum", 0)
    vol_prof  = data.get("volume_profile", {})
    trend     = data.get("trend", {})
    trend_4h  = data.get("trend_4h", {})
    ob        = data.get("order_book", {})
    fg        = data.get("fear_greed", {})
    history   = data.get("history", {})
    weekly    = history.get("weekly", {})
    daily     = history.get("daily", {})
    ts        = data.get("timestamp", "")
    plan      = signal_data.get("plan", {})
    score     = signal_data.get("score", 0)
    signal    = signal_data.get("signal", "")
    reasons   = signal_data.get("reasons", [])
    support   = levels.get("support", 0)
    resistance = levels.get("resistance", 0)
    ma20      = daily.get("ma20")
    ma50      = daily.get("ma50")
    ema200    = daily.get("ema200")

    sym = data.get("symbol", "TAOUSDT").replace("USDT", "")
    chg_icon = "📈" if chg24 > 0 else "📉"
    btc_icon = "📈" if btc_c > 0 else "📉"

    lines = [
        f"━━━━━━━━━━━━━━━━━━━━",
        f"📊 {sym}/USDT  •  {ts}",
        f"━━━━━━━━━━━━━━━━━━━━",
        f"",
        f"💰 Цена: ${price:,.4g}  {chg_icon} {chg24:+.1f}% за 24ч",
        f"📊 Диапазон 24ч: ${low24:,.4g} — ${high24:,.4g}",
        f"💵 Объём: ${vol24/1_000_000:.1f}M",
        f"{btc_icon} BTC: ${btc_p:,.0f} ({btc_c:+.1f}%)",
    ]

    # ── Уровни ───────────────────────────────────────────────────────────────
    lines += ["", "── УРОВНИ ──────────────────────────"]
    if support:
        dist_s = (price - support) / price * 100
        lines.append(f"🟢 Поддержка: ${support:,.4g}  ({dist_s:.1f}% вниз)")
    if resistance:
        dist_r = (resistance - price) / price * 100
        lines.append(f"🔴 Сопротивление: ${resistance:,.4g}  ({dist_r:.1f}% вверх)")
    if vwap:
        vwap_pos = "выше VWAP ✅" if price > vwap else "ниже VWAP 🔴"
        lines.append(f"📏 VWAP: ${vwap:,.4g}  ({vwap_pos})")

    # ── Тренд ────────────────────────────────────────────────────────────────
    lines += ["", "── ТРЕНД ───────────────────────────"]
    d1h = trend.get("direction", "")
    d4h = trend_4h.get("direction", "")
    cd1 = trend.get("consecutive_down", 0)
    cu1 = trend.get("consecutive_up", 0)
    cd4 = trend_4h.get("consecutive_down", 0)
    cu4 = trend_4h.get("consecutive_up", 0)

    tr1_icon = "📈" if d1h == "up" else ("📉" if d1h == "down" else "↔️")
    tr4_icon = "📈" if d4h == "up" else ("📉" if d4h == "down" else "↔️")
    tr1_detail = f"({cu1}↑)" if d1h == "up" else (f"({cd1}↓)" if d1h == "down" else "")
    tr4_detail = f"({cu4}↑)" if d4h == "up" else (f"({cd4}↓)" if d4h == "down" else "")
    lines.append(f"{tr1_icon} 1H: {d1h.upper()} {tr1_detail}  |  {tr4_icon} 4H: {d4h.upper()} {tr4_detail}")

    if ema_r:
        e9, e21, e50 = ema_r.get("ema9", 0), ema_r.get("ema21", 0), ema_r.get("ema50", 0)
        ema_trend = "🔼 БЫЧЬЯ" if e9 > e21 > e50 else ("🔽 МЕДВЕЖЬЯ" if e9 < e21 < e50 else "↔️ СМЕШАННАЯ")
        lines.append(f"📉 EMA лента: {ema_trend}  (9:{e9:,.2f} / 21:{e21:,.2f} / 50:{e50:,.2f})")

    if ma20 and ma50:
        ma_icon = "✅" if price > ma20 > ma50 else ("🔴" if price < ma20 < ma50 else "⚠️")
        lines.append(f"{ma_icon} MA20: ${ma20:,.2f}  |  MA50: ${ma50:,.2f}")
    if ema200:
        ema200_icon = "✅" if price > ema200 else "🔴"
        lines.append(f"{ema200_icon} EMA200д: ${ema200:,.2f}")

    # ── Индикаторы ───────────────────────────────────────────────────────────
    lines += ["", "── ИНДИКАТОРЫ ──────────────────────"]

    rsi1_zone = "🔥 ПЕРЕПРОДАН" if rsi_1h < 30 else ("❄️ ПЕРЕКУПЛЕН" if rsi_1h > 70 else "⚪ норма")
    rsi4_zone = "🔥 ПЕРЕПРОДАН" if rsi_4h < 30 else ("❄️ ПЕРЕКУПЛЕН" if rsi_4h > 70 else "⚪ норма")
    lines.append(f"📉 RSI 1H: {rsi_1h}  {rsi1_zone}")
    lines.append(f"📉 RSI 4H: {rsi_4h}  {rsi4_zone}")

    sk, sd = stoch.get("k", 50), stoch.get("d", 50)
    stoch_zone = "🔥 перепродан" if sk < 20 else ("❄️ перекуплен" if sk > 80 else "норма")
    lines.append(f"🎲 Stoch RSI: K={sk:.0f} D={sd:.0f}  ({stoch_zone})")

    macd_cross = macd_1h.get("cross", "none")
    macd_4h_cross = macd_4h.get("cross", "none")
    macd_str = {"bullish": "✅ бычий кросс", "bearish": "🔴 медвежий кросс"}.get(macd_cross, "⚪ нейтрал")
    macd_4h_str = {"bullish": " + 4H ✅", "bearish": " + 4H 🔴"}.get(macd_4h_cross, "")
    lines.append(f"⚡ MACD 1H: {macd_str}{macd_4h_str}")

    if momentum != 0:
        mom_icon = "⬆️" if momentum > 0 else "⬇️"
        lines.append(f"{mom_icon} Momentum (ROC10): {momentum:+.2f}%")

    bb_l = bb.get("lower", 0)
    bb_u = bb.get("upper", 0)
    if bb_l and bb_u:
        if price <= bb_l * 1.005:
            bb_pos = "▼ у нижней BB — перепродан"
        elif price >= bb_u * 0.995:
            bb_pos = "▲ у верхней BB — перекуплен"
        else:
            bb_pos = f"середина ({(price - bb_l) / (bb_u - bb_l) * 100:.0f}%)"
        lines.append(f"📐 BB: ${bb_l:,.2f}–${bb_u:,.2f}  ({bb_pos})")

    vt = vol_prof.get("type", "normal")
    vr = vol_prof.get("ratio", 1.0)
    vol_icons = {
        "buying_climax":   "🔥 ОБЪЁМ КЛАЙМАКС — покупатели",
        "selling_climax":  "🔥 ОБЪЁМ КЛАЙМАКС — продавцы",
        "buying_pressure": "📈 Объём на росте",
        "selling_pressure":"📉 Объём на падении",
        "normal":          "⚪ Объём обычный",
    }
    lines.append(f"📊 {vol_icons.get(vt, '⚪')} (×{vr:.1f})")

    bid_ask = ob.get("bid_ask_ratio", 0)
    if bid_ask:
        ob_str = "покупатели доминируют" if bid_ask > 1.2 else ("продавцы доминируют" if bid_ask < 0.8 else "баланс")
        lines.append(f"📖 Стакан: {ob_str} ({bid_ask:.2f})")

    fg_val   = fg.get("value", 50)
    fg_label = fg.get("label", "")
    fg_icon  = "😱" if fg_val < 30 else ("🤑" if fg_val > 70 else "😐")
    lines.append(f"{fg_icon} Страх/жадность: {fg_val} ({fg_label})")

    # ── История ──────────────────────────────────────────────────────────────
    if weekly:
        ath = weekly.get("ath", 0)
        pct = weekly.get("pct_from_ath", 0)
        cycle = weekly.get("cycle_phase", "")
        lines += ["", "── ИСТОРИЯ ─────────────────────────",
                  f"🏆 ATH: ${ath:,.2f}  ({pct:+.1f}%)",
                  f"🔄 Фаза: {cycle}"]

    # ── СИГНАЛ ───────────────────────────────────────────────────────────────
    lines += [
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        f"🎯 {signal}  (счёт {score:+d}/8)",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        "📋 ПРИЧИНЫ:",
    ]
    for r in reasons:
        lines.append(f"  {r}")

    # ── ПЛАН ─────────────────────────────────────────────────────────────────
    if plan:
        lines += [
            "",
            "── ПЛАН ДЕЙСТВИЙ ───────────────────",
            f"🟢 Вход:          {plan.get('entry', '—')}",
            f"🎯 Тейк-профит:   {plan.get('take_profit', '—')}",
            f"🛑 Стоп-лосс:     {plan.get('stop_loss', '—')}",
            f"⏰ Перепроверь:   {plan.get('recheck', '—')}",
            f"⚠️  Риск:          {plan.get('risk', '—')}",
            f"💡 Совет:         {plan.get('advice', '—')}",
        ]

    # ── Новости ──────────────────────────────────────────────────────────────
    if news:
        tao_news = news.get("tao_news", [])
        if tao_news:
            lines += ["", "── НОВОСТИ ─────────────────────────"]
            for item in tao_news[:2]:
                icon = {"positive": "🟢", "negative": "🔴", "neutral": "⚪"}.get(item.get("sentiment", ""), "⚪")
                lines.append(f"  {icon} {item['title'][:80]}")

    lines += ["", "━━━━━━━━━━━━━━━━━━━━",
              "⚠️ Не финансовый совет. Торгуй осознанно."]
    return "\n".join(lines)


# ─── блоки скоринга ───────────────────────────────────────────────────────────

def _score_structure(price, ema_r, trend_1h, trend_4h, ma20, ma50, ema200, vwap):
    """
    Структура рынка: EMA ribbon + тренды + дневные MA + VWAP.
    Это ФИЛЬТР — если структура медвежья, не покупаем.
    Максимум ±4.
    """
    score, r = 0, []

    cd4 = trend_4h.get("consecutive_down", 0)
    cu4 = trend_4h.get("consecutive_up", 0)
    d4  = trend_4h.get("direction", "")
    ll4 = trend_4h.get("lower_lows", False)
    hh4 = trend_4h.get("higher_highs", False)

    cd1 = trend_1h.get("consecutive_down", 0)
    cu1 = trend_1h.get("consecutive_up", 0)
    ll1 = trend_1h.get("lower_lows", False)

    # 4H тренд — главный
    if cd4 >= 4:
        score -= 3; r.append(f"🔴 4H: {cd4} свечей вниз подряд — сильный нисходящий тренд")
    elif cd4 >= 2:
        score -= 2; r.append(f"🟠 4H: {cd4} свечи вниз — давление продавцов")
    elif cu4 >= 3:
        score += 2; r.append(f"✅ 4H: {cu4} свечи вверх — восходящий импульс")
    elif cu4 >= 2:
        score += 1; r.append(f"🟡 4H: {cu4} свечи вверх — слабый рост")

    if ll4:
        score -= 1; r.append("🔴 4H: lower lows — рынок делает новые минимумы")
    elif hh4:
        score += 1; r.append("✅ 4H: higher highs — рынок растёт")

    # EMA ribbon на 1H
    if ema_r:
        e9, e21, e50 = ema_r.get("ema9", 0), ema_r.get("ema21", 0), ema_r.get("ema50", 0)
        if e9 and e21 and e50:
            if e9 > e21 > e50 and price > e9:
                score += 1; r.append("✅ EMA лента бычья (9>21>50) + цена выше — тренд вверх")
            elif e9 < e21 < e50 and price < e9:
                score -= 1; r.append("🔴 EMA лента медвежья (9<21<50) + цена ниже — тренд вниз")
            elif e9 > e21 and price > e21:
                r.append("🟡 EMA 9 > 21, краткосрочный бычий сигнал")
            elif e9 < e21 and price < e21:
                r.append("🟠 EMA 9 < 21, краткосрочный медвежий сигнал")

    # VWAP
    if vwap and price:
        if price > vwap * 1.005:
            r.append(f"✅ Цена выше VWAP (${vwap:,.2f}) — бычий контекст")
        elif price < vwap * 0.995:
            r.append(f"🔴 Цена ниже VWAP (${vwap:,.2f}) — медвежий контекст")

    # Дневные MA
    if price and ma20 and ma50:
        if price < ma20 < ma50:
            score -= 1; r.append("🔴 Цена ниже MA20 и MA50 — дневной медвежий тренд")
        elif price > ma20 > ma50:
            score += 1; r.append("✅ Цена выше MA20 и MA50 — дневной бычий тренд")

    return max(-4, min(4, score)), r


def _score_location(price, support, resistance, bb):
    """
    Где цена относительно уровней и BB.
    Суть: покупаем у дна, продаём у потолка.
    Максимум ±3.
    """
    score, r = 0, []

    if support and resistance and price:
        range_size = resistance - support
        if range_size > 0:
            pct_in_range = (price - support) / range_size

            if price <= support * 1.01:
                score += 3
                r.append(f"✅ ЦЕНА У ПОДДЕРЖКИ ${support:,.2f} — отличная точка для покупки")
            elif pct_in_range < 0.25:
                score += 2
                r.append(f"🟢 Цена в нижней четверти диапазона — зона покупки")
            elif pct_in_range < 0.4:
                score += 1
                r.append(f"🟡 Цена ближе к поддержке ({pct_in_range*100:.0f}% диапазона)")
            elif price >= resistance * 0.99:
                score -= 3
                r.append(f"🔴 ЦЕНА У СОПРОТИВЛЕНИЯ ${resistance:,.2f} — зона продажи, не покупать")
            elif pct_in_range > 0.75:
                score -= 2
                r.append(f"🟠 Цена в верхней четверти диапазона — зона продажи")
            elif pct_in_range > 0.6:
                score -= 1
                r.append(f"🟡 Цена ближе к сопротивлению ({pct_in_range*100:.0f}% диапазона)")
            else:
                r.append(f"⚪ Цена в середине диапазона ({pct_in_range*100:.0f}%)")

    # Bollinger Bands
    bb_l = bb.get("lower", 0)
    bb_u = bb.get("upper", 0)
    if bb_l and bb_u and price:
        if price < bb_l:
            score += 1; r.append(f"✅ Цена НИЖЕ нижней BB ${bb_l:,.2f} — экстремальная перепроданность")
        elif price <= bb_l * 1.005:
            score += 1; r.append(f"✅ Цена у нижней BB ${bb_l:,.2f} — зона покупки")
        elif price > bb_u:
            score -= 1; r.append(f"🔴 Цена ВЫШЕ верхней BB ${bb_u:,.2f} — экстремальная перекупленность")
        elif price >= bb_u * 0.995:
            score -= 1; r.append(f"🔴 Цена у верхней BB ${bb_u:,.2f} — зона продажи")

        if bb.get("width_pct", 5) < 2.5:
            r.append(f"⚡ BB сжались ({bb.get('width_pct', 0):.1f}%) — готовится сильное движение")

    return max(-3, min(3, score)), r


def _score_momentum_oscillators(rsi_1h, rsi_4h, stoch, momentum):
    """
    RSI + Stoch RSI + Momentum: подтверждение разворота.
    Максимум ±3.
    """
    score, r = 0, []

    # RSI 1H — основной осциллятор
    if rsi_1h < 20:
        score += 3; r.append(f"🔥 RSI 1H = {rsi_1h} — ЭКСТРЕМАЛЬНАЯ перепроданность, разворот неизбежен")
    elif rsi_1h < 30:
        score += 2; r.append(f"✅ RSI 1H = {rsi_1h} — перепродан, отскок очень вероятен")
    elif rsi_1h < 40:
        score += 1; r.append(f"🟡 RSI 1H = {rsi_1h} — слабая перепроданность")
    elif rsi_1h > 80:
        score -= 3; r.append(f"❄️ RSI 1H = {rsi_1h} — ЭКСТРЕМАЛЬНАЯ перекупленность, разворот неизбежен")
    elif rsi_1h > 70:
        score -= 2; r.append(f"🔴 RSI 1H = {rsi_1h} — перекуплен, риск разворота вниз")
    elif rsi_1h > 60:
        score -= 1; r.append(f"🟠 RSI 1H = {rsi_1h} — немного перегрет")
    else:
        r.append(f"⚪ RSI 1H = {rsi_1h} — нейтральная зона")

    # RSI 4H — подтверждение
    if rsi_4h < 35:
        score += 1; r.append(f"✅ RSI 4H = {rsi_4h} — перепродан на старшем ТФ (сильный сигнал)")
    elif rsi_4h > 65:
        score -= 1; r.append(f"🔴 RSI 4H = {rsi_4h} — перекуплен на 4H")
    else:
        r.append(f"⚪ RSI 4H = {rsi_4h}")

    # Stoch RSI — разворотный сигнал
    k, d = stoch.get("k", 50), stoch.get("d", 50)
    if k < 20 and d < 20:
        score += 1; r.append(f"✅ Stoch RSI K={k:.0f} D={d:.0f} — перепродан, кросс вверх ожидается")
    elif k > d and k < 25:
        score += 1; r.append(f"✅ Stoch RSI кросс вверх из перепроданности — разворот начинается")
    elif k > 80 and d > 80:
        score -= 1; r.append(f"🔴 Stoch RSI K={k:.0f} D={d:.0f} — перекуплен")
    elif k < d and k > 75:
        score -= 1; r.append(f"🔴 Stoch RSI кросс вниз из перекупленности — разворот вниз")

    # Momentum
    if momentum < -3:
        score += 1; r.append(f"📉 Momentum {momentum:+.1f}% — сильная перепроданность, отскок близко")
    elif momentum > 3:
        score -= 1; r.append(f"📈 Momentum {momentum:+.1f}% — сильная перегретость, откат близко")

    return max(-3, min(3, score)), r


def _score_macd(macd_1h, macd_4h):
    """MACD с подтверждением 4H. Максимум ±2."""
    score, r = 0, []
    cross_1h   = macd_1h.get("cross", "none")
    m1h, s1h   = macd_1h.get("macd", 0), macd_1h.get("signal", 0)
    hdir_1h    = macd_1h.get("histogram_dir", "flat")
    cross_4h   = (macd_4h or {}).get("cross", "none")
    m4h, s4h   = (macd_4h or {}).get("macd", 0), (macd_4h or {}).get("signal", 0)

    if cross_1h == "bullish":
        if cross_4h == "bullish" or m4h > s4h:
            score += 2; r.append("✅ MACD бычий кросс на 1H + 4H подтверждает — сильный разворот вверх")
        else:
            score += 1; r.append("🟡 MACD бычий кросс на 1H (4H ещё не подтвердил)")
    elif cross_1h == "bearish":
        if cross_4h == "bearish" or m4h < s4h:
            score -= 2; r.append("🔴 MACD медвежий кросс на 1H + 4H подтверждает — разворот вниз")
        else:
            score -= 1; r.append("🟠 MACD медвежий кросс на 1H (4H ещё не подтвердил)")
    elif m1h > s1h and hdir_1h == "growing" and m4h > s4h:
        score += 1; r.append("🟡 MACD 1H и 4H растут — бычий импульс")
    elif m1h < s1h and hdir_1h == "shrinking" and m4h < s4h:
        score -= 1; r.append("🟠 MACD 1H и 4H падают — медвежий импульс")
    else:
        r.append("⚪ MACD нейтрален")

    return max(-2, min(2, score)), r


def _score_volume(vol_prof, ob, change_24h):
    """Объём — подтверждение движения. Максимум ±2."""
    score, r = 0, []
    vtype = vol_prof.get("type", "normal")
    ratio = vol_prof.get("ratio", 1.0)
    bid_ask = ob.get("bid_ask_ratio", 1.0)

    if vtype == "selling_climax":
        score += 2; r.append(f"🔥 Volume climax на падении (×{ratio:.1f}) — продавцы исчерпаны, разворот!")
    elif vtype == "buying_climax":
        score -= 1; r.append(f"🔥 Volume climax на росте (×{ratio:.1f}) — покупатели исчерпаны, осторожно")
    elif vtype == "buying_pressure":
        score += 1; r.append(f"✅ Объём на росте (×{ratio:.1f}) — движение подтверждено покупателями")
    elif vtype == "selling_pressure":
        score -= 1; r.append(f"🔴 Объём на падении (×{ratio:.1f}) — движение подтверждено продавцами")
    else:
        r.append("⚪ Объём обычный")

    if bid_ask > 1.3:
        score += 1; r.append(f"✅ Стакан: покупатели доминируют (bid/ask {bid_ask:.2f})")
    elif bid_ask < 0.7:
        score -= 1; r.append(f"🔴 Стакан: продавцы доминируют (bid/ask {bid_ask:.2f})")

    return max(-2, min(2, score)), r


def _score_btc(btc_change):
    """BTC направление. Максимум ±1."""
    score, r = 0, []
    if btc_change > 3:
        score += 1; r.append(f"✅ BTC +{btc_change:.1f}% — крипторынок растёт")
    elif btc_change < -4:
        score -= 1; r.append(f"🔴 BTC {btc_change:.1f}% — рынок под давлением")
    elif btc_change < -2:
        r.append(f"🟠 BTC {btc_change:.1f}% — осторожно")
    else:
        r.append(f"⚪ BTC {btc_change:+.1f}%")
    return max(-1, min(1, score)), r


def _score_market_context(fg_value, pct_from_ath):
    """Fear/Greed + дистанция от ATH. Максимум ±1."""
    score, r = 0, []
    if fg_value < 20:
        score += 1; r.append(f"😱 Extreme Fear ({fg_value}) — рынок капитулирует, лучшие точки входа")
    elif fg_value > 80:
        score -= 1; r.append(f"🤑 Extreme Greed ({fg_value}) — рынок перегрет, близко к пику")
    else:
        r.append(f"😐 Страх/жадность: {fg_value}")

    if pct_from_ath is not None:
        if pct_from_ath > -10:
            score -= 1; r.append(f"🔴 В {abs(pct_from_ath):.0f}% от ATH — исторически зона распределения")
        elif pct_from_ath < -60:
            score += 1; r.append(f"✅ В {abs(pct_from_ath):.0f}% от ATH — исторически выгодная зона")

    return max(-1, min(1, score)), r


# ─── классификация сигнала ────────────────────────────────────────────────────

def _classify_signal(score, price, support, resistance, rsi_1h, rsi_4h, bottom):
    """Переводит числовой скор в понятное действие."""
    bottom_score = bottom.get("bottom_score", 0) if bottom else 0

    if score >= 6:
        return "СИЛЬНЫЙ СИГНАЛ — ВХОДИТЬ СЕЙЧАС", "🚀", "high"
    elif score >= 4:
        return "СИГНАЛ НА ВХОД", "🟢", "high"
    elif score >= 2:
        return "ПРИСМАТРИВАЙСЯ К ВХОДУ", "🟡", "medium"
    elif score <= -6:
        return "ВЫХОДИТЬ / НЕ ВХОДИТЬ", "🔴", "high"
    elif score <= -4:
        return "СИГНАЛ НА ВЫХОД / ПРОДАЖУ", "🔴", "high"
    elif score <= -2:
        return "ГОТОВЬСЯ К ВЫХОДУ", "🟠", "medium"
    else:
        return "ЖДАТЬ — нет чёткого сигнала", "⚪", "low"


# ─── план действий ────────────────────────────────────────────────────────────

def _build_plan(score, price, support, resistance, rsi_1h, rsi_4h,
                bb, ema_r, trend, trend_4h, weekly, daily):
    cd4 = trend_4h.get("consecutive_down", 0)
    d4  = trend_4h.get("direction", "")
    bb_l = bb.get("lower", 0)
    bb_u = bb.get("upper", 0)
    ma50 = daily.get("ma50")
    pct_ath = weekly.get("pct_from_ath")

    sup_valid = support and support > 0 and support < price * 0.998
    res_valid = resistance and resistance > 0 and resistance > price * 1.002

    # Точка входа
    if score >= 4:
        if sup_valid and price <= support * 1.015:
            entry = f"Сейчас у поддержки ${price:,.2f} — оптимально"
        else:
            entry = f"Сейчас (~${price:,.2f}) — условия сформировались"
    elif score >= 2:
        if sup_valid:
            entry = f"Лучше дождаться ${support*1.005:,.2f} (у поддержки)"
        else:
            entry = f"Малой частью сейчас, основную позицию — при откате"
    elif score <= -4:
        entry = f"НЕ ВХОДИТЬ — это зона продажи. Жди следующего дна"
    elif d4 == "down" and cd4 >= 3:
        entry = f"НЕ ВХОДИТЬ — 4H тренд вниз. Жди разворота на 4H"
    else:
        entry = f"Ждать — нет чёткой точки входа"

    # Тейк-профит
    if score >= 2:
        if res_valid:
            tp1 = price * 1.015
            take_profit = f"TP1: ${tp1:,.2f} (+1.5%)  →  TP2: ${resistance*0.99:,.2f} (у сопр.)"
        else:
            tp1 = price * 1.015
            tp2 = price * 1.04
            take_profit = f"TP1: ${tp1:,.2f} (+1.5%)  →  TP2: ${tp2:,.2f} (+4%)"
    elif score <= -2:
        take_profit = "— (сигнал на выход)"
    else:
        take_profit = "Ждать формирования позиции"

    # Стоп-лосс
    if sup_valid and score >= 2:
        stop_loss = f"${support * 0.985:,.2f} (ниже поддержки, -1.5%)"
    elif bb_l and price > bb_l and score >= 2:
        stop_loss = f"${bb_l * 0.99:,.2f} (ниже нижней BB)"
    elif score >= 2:
        stop_loss = f"${price * 0.97:,.2f} (-3% от входа)"
    else:
        stop_loss = "—"

    # Перепроверить
    if score >= 4:
        recheck = "Через 15-30 мин — следи за TP"
    elif score <= -4:
        recheck = "Через 30 мин — следи за разворотом"
    elif d4 == "down" and cd4 >= 3:
        recheck = "Каждые 30 мин — ждёшь разворота на 4H"
    elif rsi_1h < 35 or rsi_4h < 35:
        recheck = "Через 30-60 мин — RSI должен оттолкнуться"
    else:
        recheck = "Через 1-2 часа"

    # Риск
    if d4 == "down" and cd4 >= 3:
        risk = "ВЫСОКИЙ — активный нисходящий тренд на 4H"
    elif res_valid and price >= resistance * 0.99:
        risk = "ВЫСОКИЙ — цена у сопротивления"
    elif rsi_1h > 75:
        risk = "ВЫСОКИЙ — перекупленность"
    elif score >= 4 and rsi_1h < 35:
        risk = "НИЗКИЙ — перепроданность + хорошие условия"
    elif score >= 2:
        risk = "СРЕДНИЙ"
    else:
        risk = "СРЕДНИЙ-ВЫСОКИЙ — нет чёткого сигнала"

    # Простой совет для начинающего
    if score >= 5:
        advice = "Войди, поставь стоп и не паникуй — условия сильные"
    elif score >= 3:
        advice = "Можно войти малой частью и добавить при подтверждении"
    elif score <= -5:
        advice = "Зафиксируй прибыль если в позиции — это пик"
    elif score <= -3:
        advice = "Не покупай сейчас — подожди дна"
    elif d4 == "down":
        advice = "Тренд вниз — даже хорошие сигналы на 1H могут быть ловушкой"
    else:
        advice = "Нет чёткого сигнала — лучше подождать"

    return {
        "entry": entry, "take_profit": take_profit,
        "stop_loss": stop_loss, "recheck": recheck,
        "risk": risk, "advice": advice,
    }
