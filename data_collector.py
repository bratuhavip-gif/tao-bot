"""
Data collector — сбор всех рыночных данных для TAO Signal Bot.

Новое:
  - Stochastic RSI (14,3,3)
  - Bollinger Bands (20, 2σ)
  - EMA200 (дневная)
  - Trend detector: consecutive candles, lower lows / higher highs, slope
  - Order book bid/ask ratio (лёгкий индикатор давления)
  - volume_ratio в levels
"""
import httpx
from datetime import datetime

BINANCE_BASE = "https://api.binance.com/api/v3"


# ─── тикеры ──────────────────────────────────────────────────────────────────

def get_tao_ticker() -> dict:
    return _get_generic_ticker("TAOUSDT")


def get_btc_ticker() -> dict:
    try:
        with httpx.Client(timeout=10) as c:
            d = c.get(f"{BINANCE_BASE}/ticker/24hr", params={"symbol": "BTCUSDT"}).json()
            price      = float(d["lastPrice"])
            open_price = float(d["openPrice"])
            return {
                "price":     price,
                "change_24h": round(((price - open_price) / open_price) * 100, 2),
            }
    except Exception as e:
        return {"error": str(e)}


def _get_generic_ticker(symbol: str) -> dict:
    try:
        with httpx.Client(timeout=10) as c:
            d = c.get(f"{BINANCE_BASE}/ticker/24hr", params={"symbol": symbol}).json()
            price      = float(d["lastPrice"])
            open_price = float(d["openPrice"])
            return {
                "price":      price,
                "change_24h": round(((price - open_price) / open_price) * 100, 2),
                "volume_24h": float(d["quoteVolume"]),
                "high_24h":   float(d["highPrice"]),
                "low_24h":    float(d["lowPrice"]),
                "open_24h":   open_price,
            }
    except Exception as e:
        return {"error": str(e)}


# ─── свечи ───────────────────────────────────────────────────────────────────

def get_binance_klines(symbol="TAOUSDT", interval="1h", limit=50) -> list:
    try:
        with httpx.Client(timeout=10) as c:
            candles = c.get(
                f"{BINANCE_BASE}/klines",
                params={"symbol": symbol, "interval": interval, "limit": limit}
            ).json()
            return [
                {
                    "open":   float(x[1]),
                    "high":   float(x[2]),
                    "low":    float(x[3]),
                    "close":  float(x[4]),
                    "volume": float(x[5]),
                    "time":   datetime.fromtimestamp(x[0] / 1000).strftime("%d.%m %H:%M"),
                }
                for x in candles
            ]
    except Exception:
        return []


# ─── технические индикаторы ───────────────────────────────────────────────────

def calculate_rsi(candles: list, period=14) -> float:
    if len(candles) < period + 1:
        return 50.0
    closes = [c["close"] for c in candles]
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    return round(100 - (100 / (1 + avg_gain / avg_loss)), 1)


def calculate_stoch_rsi(candles: list, rsi_period=14, stoch_period=14, k_period=3, d_period=3) -> dict:
    """Stochastic RSI (14,14,3,3)."""
    if len(candles) < rsi_period + stoch_period + k_period + d_period:
        return {"k": 50.0, "d": 50.0}

    closes = [c["close"] for c in candles]

    # Посчитать RSI для каждой точки
    rsi_series = []
    for i in range(rsi_period, len(closes)):
        window = closes[i - rsi_period:i + 1]
        gains  = [max(window[j] - window[j-1], 0) for j in range(1, len(window))]
        losses = [max(window[j-1] - window[j], 0) for j in range(1, len(window))]
        ag = sum(gains) / rsi_period
        al = sum(losses) / rsi_period
        if al == 0:
            rsi_series.append(100.0)
        else:
            rsi_series.append(100 - 100 / (1 + ag / al))

    if len(rsi_series) < stoch_period:
        return {"k": 50.0, "d": 50.0}

    # Stochastic по RSI
    stoch_k_raw = []
    for i in range(stoch_period - 1, len(rsi_series)):
        window  = rsi_series[i - stoch_period + 1:i + 1]
        lo, hi  = min(window), max(window)
        if hi - lo == 0:
            stoch_k_raw.append(50.0)
        else:
            stoch_k_raw.append((rsi_series[i] - lo) / (hi - lo) * 100)

    if len(stoch_k_raw) < k_period:
        return {"k": 50.0, "d": 50.0}

    # Smooth K
    k_smoothed = []
    for i in range(k_period - 1, len(stoch_k_raw)):
        k_smoothed.append(sum(stoch_k_raw[i - k_period + 1:i + 1]) / k_period)

    if len(k_smoothed) < d_period:
        return {"k": round(k_smoothed[-1], 1) if k_smoothed else 50.0, "d": 50.0}

    d_smoothed = []
    for i in range(d_period - 1, len(k_smoothed)):
        d_smoothed.append(sum(k_smoothed[i - d_period + 1:i + 1]) / d_period)

    return {
        "k": round(k_smoothed[-1], 1),
        "d": round(d_smoothed[-1], 1),
    }


def calculate_macd(candles: list) -> dict:
    """MACD(12,26,9) + histogram direction."""
    if len(candles) < 35:
        return {"macd": 0, "signal": 0, "cross": "none", "histogram": 0, "histogram_dir": ""}

    closes = [c["close"] for c in candles]

    def ema(values, period):
        k = 2 / (period + 1)
        result = [values[0]]
        for v in values[1:]:
            result.append(v * k + result[-1] * (1 - k))
        return result

    ema12      = ema(closes, 12)
    ema26      = ema(closes, 26)
    macd_line  = [ema12[i] - ema26[i] for i in range(len(closes))]
    signal_line = ema(macd_line, 9)

    last_macd   = macd_line[-1]
    last_signal = signal_line[-1]
    prev_macd   = macd_line[-2]
    prev_signal = signal_line[-2]

    hist      = last_macd - last_signal
    prev_hist = prev_macd - prev_signal

    if prev_macd <= prev_signal and last_macd > last_signal:
        cross = "bullish"
    elif prev_macd >= prev_signal and last_macd < last_signal:
        cross = "bearish"
    else:
        cross = "none"

    return {
        "macd":          round(last_macd, 4),
        "signal":        round(last_signal, 4),
        "cross":         cross,
        "histogram":     round(hist, 4),
        "histogram_dir": "growing" if abs(hist) > abs(prev_hist) else "shrinking",
    }


def calculate_bollinger_bands(candles: list, period=20, std_dev=2) -> dict:
    """Bollinger Bands (20, 2σ)."""
    if len(candles) < period:
        return {}
    closes = [c["close"] for c in candles[-period:]]
    mid    = sum(closes) / period
    variance = sum((x - mid) ** 2 for x in closes) / period
    sigma  = variance ** 0.5
    upper  = mid + std_dev * sigma
    lower  = mid - std_dev * sigma
    width_pct = (upper - lower) / mid * 100 if mid else 0
    return {
        "upper":     round(upper, 2),
        "mid":       round(mid, 2),
        "lower":     round(lower, 2),
        "width_pct": round(width_pct, 2),
    }


def calculate_support_resistance(candles: list) -> dict:
    if not candles:
        return {}
    window  = candles[-20:]
    highs   = [c["high"] for c in window]
    lows    = [c["low"] for c in window]
    volumes = [c["volume"] for c in window]
    avg_vol = sum(volumes) / len(volumes)
    cur_vol = volumes[-1] if volumes else 0
    vol_ratio = cur_vol / avg_vol if avg_vol else 1.0
    return {
        "resistance":   round(max(highs), 2),
        "support":      round(min(lows), 2),
        "avg_volume":   round(avg_vol, 0),
        "current_volume": round(cur_vol, 0),
        "volume_spike": cur_vol > avg_vol * 1.5,
        "volume_ratio": round(vol_ratio, 2),
    }


def calculate_ema(values: list, period: int) -> float:
    if len(values) < period:
        return values[-1] if values else 0
    k = 2 / (period + 1)
    ema = values[0]
    for v in values[1:]:
        ema = v * k + ema * (1 - k)
    return ema


def detect_trend(candles: list) -> dict:
    """
    Определяет краткосрочный тренд по свечам 1H.
    - consecutive_down/up: серия закрытых свечей в одном направлении подряд
    - lower_lows: последние 3 минимума снижаются
    - higher_highs: последние 3 максимума растут
    - slope_1h: наклон линейной регрессии по последним 10 closes (нормализован)
    """
    if len(candles) < 5:
        return {"direction": "unknown", "consecutive_down": 0, "consecutive_up": 0,
                "lower_lows": False, "higher_highs": False, "slope_1h": 0, "desc": ""}

    closes = [c["close"] for c in candles]
    lows   = [c["low"] for c in candles]
    highs  = [c["high"] for c in candles]

    # Серия подряд идущих свечей
    consecutive_down = 0
    consecutive_up   = 0
    for i in range(len(closes) - 1, 0, -1):
        if closes[i] < closes[i - 1]:
            if consecutive_up == 0:
                consecutive_down += 1
            else:
                break
        elif closes[i] > closes[i - 1]:
            if consecutive_down == 0:
                consecutive_up += 1
            else:
                break
        else:
            break

    # Структура минимумов/максимумов (последние 3 из последних 10 свечей)
    recent = candles[-10:] if len(candles) >= 10 else candles
    swing_lows  = [c["low"] for c in recent]
    swing_highs = [c["high"] for c in recent]
    lower_lows   = swing_lows[-1] < swing_lows[-3] < swing_lows[0] if len(swing_lows) >= 4 else False
    higher_highs = swing_highs[-1] > swing_highs[-3] > swing_highs[0] if len(swing_highs) >= 4 else False

    # Наклон (линейная регрессия по 10 свечам)
    n = min(10, len(closes))
    xs = list(range(n))
    ys = closes[-n:]
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    num    = sum((xs[i] - mean_x) * (ys[i] - mean_y) for i in range(n))
    den    = sum((xs[i] - mean_x) ** 2 for i in range(n))
    slope  = (num / den) if den != 0 else 0
    slope_pct = slope / mean_y * 100 if mean_y else 0

    if consecutive_down >= 3 or (lower_lows and slope_pct < -0.3):
        direction = "down"
    elif consecutive_up >= 3 or (higher_highs and slope_pct > 0.3):
        direction = "up"
    elif consecutive_down >= 2:
        direction = "down"
    elif consecutive_up >= 2:
        direction = "up"
    else:
        direction = "sideways"

    return {
        "direction":       direction,
        "consecutive_down": consecutive_down,
        "consecutive_up":   consecutive_up,
        "lower_lows":       lower_lows,
        "higher_highs":     higher_highs,
        "slope_1h":         round(slope_pct, 3),
        "desc":             f"{consecutive_down}↓ / {consecutive_up}↑ | slope {slope_pct:+.2f}%/свеча",
    }


def detect_bottom_patterns(candles_1h: list, candles_4h: list, candles_daily: list) -> dict:
    """
    Детектирует паттерны дна и капитуляции:

    1. multiple_bottom — 3-4 касания одного уровня поддержки (±2%) на разных ТФ
    2. capitulation_drop — резкое падение -5%+ за 1-3 свечи без предпосылок (BTC рос или боком)
    3. volume_climax — огромный объём на падении (капитуляция продавцов)
    4. rsi_divergence — цена обновила лоу, RSI нет (бычья дивергенция)
    5. hammer_reversal — молот/пин-бар на уровне поддержки (тень вниз > 2x тела)
    """
    result = {
        "multiple_bottom":   False,
        "bottom_level":      0.0,
        "bottom_touches":    0,
        "capitulation_drop": False,
        "drop_pct":          0.0,
        "drop_candles":      0,
        "volume_climax":     False,
        "volume_climax_ratio": 0.0,
        "rsi_divergence":    False,
        "hammer_reversal":   False,
        "bottom_score":      0,   # суммарная сила сигнала 0-10
        "description":       [],
    }

    desc = []

    # ── 1. Multiple bottom (тройное/четвертное дно) ───────────────────────────
    # Ищем кластеры локальных минимумов на 4H свечах
    if len(candles_4h) >= 20:
        lows_4h = [c["low"] for c in candles_4h[-40:]]
        # Находим локальные минимумы (свеча ниже соседей)
        local_lows = []
        for i in range(1, len(lows_4h) - 1):
            if lows_4h[i] < lows_4h[i-1] and lows_4h[i] < lows_4h[i+1]:
                local_lows.append(lows_4h[i])

        if len(local_lows) >= 3:
            # Проверяем есть ли кластер (3+ минимума в пределах 2% друг от друга)
            local_lows_sorted = sorted(local_lows)
            base = local_lows_sorted[0]
            cluster = [l for l in local_lows_sorted if l <= base * 1.02]
            if len(cluster) >= 3:
                result["multiple_bottom"] = True
                result["bottom_level"]    = round(base, 2)
                result["bottom_touches"]  = len(cluster)
                result["bottom_score"]   += 3
                desc.append(f"🔁 {len(cluster)}x дно на уровне ${base:.2f} (±2%)")

    # ── 2. Capitulation drop — резкое падение «из ниоткуда» ─────────────────
    # Падение -5%+ за последние 3 свечи 1H при нейтральном/боковом начале
    if len(candles_1h) >= 6:
        recent = candles_1h[-6:]
        # Первые 3 свечи — контекст (не было сильного тренда)
        pre_drop = recent[:3]
        drop_candles = recent[3:]
        pre_change = (pre_drop[-1]["close"] - pre_drop[0]["open"]) / pre_drop[0]["open"] * 100
        drop_change = (drop_candles[-1]["close"] - drop_candles[0]["open"]) / drop_candles[0]["open"] * 100

        if drop_change <= -5.0 and abs(pre_change) < 3.0:
            result["capitulation_drop"] = True
            result["drop_pct"]          = round(drop_change, 2)
            result["drop_candles"]      = 3
            result["bottom_score"]     += 2
            desc.append(f"💥 Капитуляция: -{abs(drop_change):.1f}% за 3 свечи из бокового рынка")
        elif drop_change <= -8.0:
            result["capitulation_drop"] = True
            result["drop_pct"]          = round(drop_change, 2)
            result["drop_candles"]      = 3
            result["bottom_score"]     += 3
            desc.append(f"💥 Резкое падение: -{abs(drop_change):.1f}% за 3 свечи — капитуляция")

    # ── 3. Volume climax — огромный объём на свече падения ───────────────────
    if len(candles_1h) >= 20:
        volumes = [c["volume"] for c in candles_1h]
        avg_vol = sum(volumes[:-3]) / max(len(volumes[:-3]), 1)
        # Смотрим максимальный объём в последних 3 свечах
        recent_max_vol = max(c["volume"] for c in candles_1h[-3:])
        vol_ratio = recent_max_vol / avg_vol if avg_vol else 1.0

        if vol_ratio >= 3.0:
            # Проверяем что это была свеча падения
            biggest_vol_candle = max(candles_1h[-3:], key=lambda c: c["volume"])
            if biggest_vol_candle["close"] < biggest_vol_candle["open"]:
                result["volume_climax"]       = True
                result["volume_climax_ratio"] = round(vol_ratio, 1)
                result["bottom_score"]       += 2
                desc.append(f"📊 Volume climax: объём {vol_ratio:.1f}x от среднего на медвежьей свече")
        elif vol_ratio >= 2.0:
            biggest_vol_candle = max(candles_1h[-3:], key=lambda c: c["volume"])
            if biggest_vol_candle["close"] < biggest_vol_candle["open"]:
                result["volume_climax"]       = True
                result["volume_climax_ratio"] = round(vol_ratio, 1)
                result["bottom_score"]       += 1
                desc.append(f"📊 Повышенный объём: {vol_ratio:.1f}x от среднего на падении")

    # ── 4. RSI бычья дивергенция (цена ниже, RSI выше предыдущего лоу) ───────
    if len(candles_1h) >= 30:
        # Сравниваем два последних локальных лоу по цене и RSI
        closes = [c["close"] for c in candles_1h]
        # Находим предпоследний локальный минимум
        min_idx_last = len(closes) - 1
        min_idx_prev = None
        for i in range(len(closes) - 2, 5, -1):
            if closes[i] < closes[i-1] and closes[i] < closes[i+1]:
                if min_idx_prev is None:
                    min_idx_prev = i
                    break

        if min_idx_prev is not None:
            price_last = closes[min_idx_last]
            price_prev = closes[min_idx_prev]
            # RSI на этих точках
            rsi_last = calculate_rsi(candles_1h[:min_idx_last+1])
            rsi_prev = calculate_rsi(candles_1h[:min_idx_prev+1])

            if price_last < price_prev and rsi_last > rsi_prev + 5:
                result["rsi_divergence"] = True
                result["bottom_score"] += 2
                desc.append(f"📈 RSI дивергенция: цена ниже (${price_last:.2f} < ${price_prev:.2f}), RSI выше ({rsi_last:.0f} > {rsi_prev:.0f})")

    # ── 5. Hammer / Pin bar на уровне поддержки ──────────────────────────────
    if candles_1h:
        last = candles_1h[-1]
        body  = abs(last["close"] - last["open"])
        lower = min(last["close"], last["open"]) - last["low"]
        upper = last["high"] - max(last["close"], last["open"])
        if body > 0 and lower >= body * 2 and upper <= body * 0.5:
            result["hammer_reversal"] = True
            result["bottom_score"]   += 1
            desc.append(f"🔨 Молот/пин-бар: нижняя тень {lower/body:.1f}x от тела")

    result["description"] = desc
    result["bottom_score"] = min(result["bottom_score"], 10)
    return result


def get_order_book_ratio(symbol="TAOUSDT", depth=20) -> dict:
    """Соотношение bid/ask объёмов в стакане — быстрый индикатор давления."""
    try:
        with httpx.Client(timeout=6) as c:
            ob = c.get(f"{BINANCE_BASE}/depth", params={"symbol": symbol, "limit": depth}).json()
            bid_vol = sum(float(x[1]) for x in ob.get("bids", []))
            ask_vol = sum(float(x[1]) for x in ob.get("asks", []))
            ratio   = round(bid_vol / ask_vol, 2) if ask_vol else 1.0
            return {"bid_vol": round(bid_vol, 2), "ask_vol": round(ask_vol, 2), "bid_ask_ratio": ratio}
    except Exception:
        return {}


# ─── история ──────────────────────────────────────────────────────────────────

def get_tao_history() -> dict:
    result = {}

    weekly = get_binance_klines("TAOUSDT", "1w", 200)
    if weekly:
        all_highs  = [c["high"] for c in weekly]
        all_lows   = [c["low"] for c in weekly]
        all_closes = [c["close"] for c in weekly]
        all_volumes = [c["volume"] for c in weekly]

        ath      = max(all_highs)
        atl      = min(all_lows)
        ath_week = weekly[all_highs.index(ath)]["time"]
        atl_week = weekly[all_lows.index(atl)]["time"]

        trend_4w  = ((sum(all_closes[-4:]) / 4 - sum(all_closes[-8:-4]) / 4) / (sum(all_closes[-8:-4]) / 4) * 100) if len(all_closes) >= 8 else 0
        trend_12w = ((all_closes[-1] - all_closes[-12]) / all_closes[-12] * 100) if len(all_closes) >= 12 else 0

        avg_vol_all = sum(all_volumes) / len(all_volumes)
        recent_vol  = sum(all_volumes[-4:]) / 4

        current      = all_closes[-1]
        pct_from_ath = ((current - ath) / ath) * 100
        pct_from_atl = ((current - atl) / atl) * 100

        sorted_highs = sorted(set([round(h, 0) for h in all_highs]), reverse=True)
        sorted_lows  = sorted(set([round(l, 0) for l in all_lows]))

        if pct_from_ath > -20:
            cycle_phase = "ЭЙФОРИЯ / РАСПРЕДЕЛЕНИЕ — цена у ATH"
        elif pct_from_ath > -40:
            cycle_phase = "КОРРЕКЦИЯ — откат от ATH"
        elif pct_from_ath > -60:
            cycle_phase = "МЕДВЕЖИЙ РЫНОК — середина падения"
        elif pct_from_ath > -80:
            cycle_phase = "КАПИТУЛЯЦИЯ — дно близко"
        else:
            cycle_phase = "ГЛУБОКОЕ ДНО — экстремальный страх"

        result["weekly"] = {
            "ath":             round(ath, 2),
            "ath_date":        ath_week,
            "atl":             round(atl, 2),
            "atl_date":        atl_week,
            "pct_from_ath":    round(pct_from_ath, 1),
            "pct_from_atl":    round(pct_from_atl, 1),
            "trend_4w":        round(trend_4w, 1),
            "trend_12w":       round(trend_12w, 1),
            "key_resistances": sorted_highs[:5],
            "key_supports":    sorted_lows[:5],
            "avg_vol_all_time": round(avg_vol_all, 0),
            "recent_vol_4w":   round(recent_vol, 0),
            "volume_vs_history": round((recent_vol / avg_vol_all) * 100, 0),
            "cycle_phase":     cycle_phase,
            "total_weeks":     len(weekly),
        }

    daily = get_binance_klines("TAOUSDT", "1d", 200)
    if daily:
        all_closes_d = [c["close"] for c in daily]
        closes_30d   = all_closes_d[-30:]
        closes_7d    = all_closes_d[-7:]
        highs_30d    = [c["high"] for c in daily[-30:]]
        lows_30d     = [c["low"] for c in daily[-30:]]

        avg_30d       = sum(closes_30d) / len(closes_30d)
        high_30d      = max(highs_30d)
        low_30d       = min(lows_30d)
        range_30d_pct = ((high_30d - low_30d) / low_30d) * 100

        trend_7d  = ((closes_7d[-1] - closes_7d[0]) / closes_7d[0] * 100) if len(closes_7d) > 1 else 0
        trend_30d = ((closes_30d[-1] - closes_30d[0]) / closes_30d[0] * 100) if len(closes_30d) > 1 else 0

        ma20   = sum(all_closes_d[-20:]) / 20 if len(all_closes_d) >= 20 else None
        ma50   = sum(all_closes_d[-50:]) / 50 if len(all_closes_d) >= 50 else None
        ema200 = calculate_ema(all_closes_d, 200) if len(all_closes_d) >= 200 else None

        result["daily"] = {
            "avg_30d":      round(avg_30d, 2),
            "high_30d":     round(high_30d, 2),
            "low_30d":      round(low_30d, 2),
            "range_30d_pct": round(range_30d_pct, 1),
            "trend_7d":     round(trend_7d, 1),
            "trend_30d":    round(trend_30d, 1),
            "ma20":         round(ma20, 2) if ma20 else None,
            "ma50":         round(ma50, 2) if ma50 else None,
            "ema200":       round(ema200, 2) if ema200 else None,
        }

    return result


def get_fear_greed() -> dict:
    try:
        with httpx.Client(timeout=10) as c:
            data = c.get("https://api.alternative.me/fng/?limit=1").json()["data"][0]
            return {
                "value": int(data["value"]),
                "label": data["value_classification"],
            }
    except Exception:
        return {"value": 50, "label": "Unknown"}


# ─── главная функция ──────────────────────────────────────────────────────────

def collect_all_data(symbol: str = "TAOUSDT") -> dict:
    ticker_fn = get_tao_ticker if symbol == "TAOUSDT" else lambda: _get_generic_ticker(symbol)
    coin      = ticker_fn()
    btc       = get_btc_ticker()
    fg        = get_fear_greed()

    candles_1h    = get_binance_klines(symbol, "1h", 60)
    candles_4h    = get_binance_klines(symbol, "4h", 40)
    candles_daily = get_binance_klines(symbol, "1d", 30)

    history = get_tao_history() if symbol == "TAOUSDT" else _get_coin_history(symbol)

    rsi_1h     = calculate_rsi(candles_1h)
    rsi_4h     = calculate_rsi(candles_4h)
    stoch_rsi  = calculate_stoch_rsi(candles_1h)
    macd_1h    = calculate_macd(candles_1h)
    bb_1h      = calculate_bollinger_bands(candles_1h)
    levels     = calculate_support_resistance(candles_1h)
    trend      = detect_trend(candles_1h)
    order_book = get_order_book_ratio(symbol)
    bottom     = detect_bottom_patterns(candles_1h, candles_4h, candles_daily)

    return {
        "tao":           coin,
        "btc":           btc,
        "fear_greed":    fg,
        "rsi_1h":        rsi_1h,
        "rsi_4h":        rsi_4h,
        "stoch_rsi_1h":  stoch_rsi,
        "macd_1h":       macd_1h,
        "bb_1h":         bb_1h,
        "levels":        levels,
        "trend":         trend,
        "order_book":    order_book,
        "history":       history,
        "bottom":        bottom,
        "symbol":        symbol,
        "timestamp":     datetime.now().strftime("%d.%m.%Y %H:%M:%S"),
    }


# ─── быстрый скан для списка монет ────────────────────────────────────────────

AI_COINS = [
    ("RENDERUSDT", "RENDER", "🎨"),
    ("FETUSDT",    "FET",    "🤖"),
    ("WLDUSDT",    "WLD",    "🌐"),
    ("INJUSDT",    "INJ",    "💉"),
    ("OCEANUSDT",  "OCEAN",  "🌊"),
    ("ARUSDT",     "AR",     "💾"),
    ("GRTUSDT",    "GRT",    "📡"),
]


def quick_scan_coin(symbol: str) -> dict:
    try:
        ticker   = _get_generic_ticker(symbol)
        candles  = get_binance_klines(symbol, "1h", 60)
        rsi      = calculate_rsi(candles)
        macd     = calculate_macd(candles)
        bb       = calculate_bollinger_bands(candles)
        levels   = calculate_support_resistance(candles)
        trend    = detect_trend(candles)
        return {
            "price":      ticker.get("price", 0),
            "change_24h": ticker.get("change_24h", 0),
            "rsi_1h":     rsi,
            "macd":       macd,
            "bb":         bb,
            "support":    levels.get("support", 0),
            "resistance": levels.get("resistance", 0),
            "trend":      trend,
        }
    except Exception:
        return {}


# ─── история для других монет ─────────────────────────────────────────────────

def _get_coin_history(symbol: str) -> dict:
    result = {}
    weekly = get_binance_klines(symbol, "1w", 200)
    if weekly:
        all_highs  = [c["high"] for c in weekly]
        all_lows   = [c["low"] for c in weekly]
        all_closes = [c["close"] for c in weekly]
        ath      = max(all_highs)
        atl      = min(all_lows)
        current  = all_closes[-1]
        trend_4w  = ((sum(all_closes[-4:]) / 4 - sum(all_closes[-8:-4]) / 4) / (sum(all_closes[-8:-4]) / 4) * 100) if len(all_closes) >= 8 else 0
        trend_12w = ((all_closes[-1] - all_closes[-12]) / all_closes[-12] * 100) if len(all_closes) >= 12 else 0
        pct_from_ath = ((current - ath) / ath) * 100
        if pct_from_ath > -20:   cycle_phase = "ЭЙФОРИЯ / РАСПРЕДЕЛЕНИЕ"
        elif pct_from_ath > -40: cycle_phase = "КОРРЕКЦИЯ"
        elif pct_from_ath > -60: cycle_phase = "МЕДВЕЖИЙ РЫНОК"
        elif pct_from_ath > -80: cycle_phase = "КАПИТУЛЯЦИЯ"
        else:                    cycle_phase = "ГЛУБОКОЕ ДНО"
        result["weekly"] = {
            "ath":             round(ath, 4),
            "atl":             round(atl, 4),
            "pct_from_ath":    round(pct_from_ath, 1),
            "trend_4w":        round(trend_4w, 1),
            "trend_12w":       round(trend_12w, 1),
            "cycle_phase":     cycle_phase,
            "key_resistances": sorted(set([round(h, 2) for h in all_highs]), reverse=True)[:5],
            "key_supports":    sorted(set([round(l, 2) for l in all_lows]))[:5],
        }
    daily = get_binance_klines(symbol, "1d", 200)
    if daily:
        all_closes_d = [c["close"] for c in daily]
        ma20   = sum(all_closes_d[-20:]) / 20 if len(all_closes_d) >= 20 else None
        ma50   = sum(all_closes_d[-50:]) / 50 if len(all_closes_d) >= 50 else None
        ema200 = calculate_ema(all_closes_d, 200) if len(all_closes_d) >= 200 else None
        closes_7d  = all_closes_d[-7:]
        closes_30d = all_closes_d[-30:]
        trend_7d   = ((closes_7d[-1] - closes_7d[0]) / closes_7d[0] * 100) if len(closes_7d) > 1 else 0
        trend_30d  = ((closes_30d[-1] - closes_30d[0]) / closes_30d[0] * 100) if len(closes_30d) > 1 else 0
        result["daily"] = {
            "ma20":      round(ma20, 4) if ma20 else None,
            "ma50":      round(ma50, 4) if ma50 else None,
            "ema200":    round(ema200, 4) if ema200 else None,
            "trend_7d":  round(trend_7d, 1),
            "trend_30d": round(trend_30d, 1),
        }
    return result
