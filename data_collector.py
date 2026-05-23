"""
Data collector v3 — быстрые и точные данные для скальпинга TAO.

Принцип: всё что нужно для определения ДНА и ПИКА в реальном времени.
- EMA 9/21/50 на 1H (лента трендов)
- RSI Wilder 14 на 1H и 4H
- Stoch RSI (14,14,3,3)
- MACD (12,26,9)
- Bollinger Bands (20, 2σ)
- VWAP (внутридневной)
- Momentum (ROC 10)
- Pivot-уровни с 4H свечей
- Тренд: consecutive candles + lower lows/higher highs
- Order book bid/ask ratio
- Volume profile (рост/падение с объёмом)
"""
import httpx
from datetime import datetime

BINANCE_BASE = "https://api.binance.com/api/v3"


# ─── тикеры ───────────────────────────────────────────────────────────────────

def get_ticker(symbol: str) -> dict:
    try:
        with httpx.Client(timeout=8) as c:
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

def get_tao_ticker() -> dict:
    return get_ticker("TAOUSDT")

def get_btc_ticker() -> dict:
    try:
        with httpx.Client(timeout=8) as c:
            d = c.get(f"{BINANCE_BASE}/ticker/24hr", params={"symbol": "BTCUSDT"}).json()
            price      = float(d["lastPrice"])
            open_price = float(d["openPrice"])
            return {
                "price":      price,
                "change_24h": round(((price - open_price) / open_price) * 100, 2),
            }
    except Exception as e:
        return {"error": str(e)}


# ─── свечи ────────────────────────────────────────────────────────────────────

def get_klines(symbol: str = "TAOUSDT", interval: str = "1h", limit: int = 60) -> list:
    try:
        with httpx.Client(timeout=8) as c:
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

# Обратная совместимость
def get_binance_klines(symbol="TAOUSDT", interval="1h", limit=50):
    return get_klines(symbol, interval, limit)


# ─── RSI (Wilder) ─────────────────────────────────────────────────────────────

def calculate_rsi(candles: list, period: int = 14) -> float:
    if len(candles) < period + 1:
        return 50.0
    closes = [c["close"] for c in candles]
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    return round(100 - (100 / (1 + avg_gain / avg_loss)), 1)


# ─── Stochastic RSI ───────────────────────────────────────────────────────────

def calculate_stoch_rsi(candles: list, rsi_period=14, stoch_period=14, k_period=3, d_period=3) -> dict:
    if len(candles) < rsi_period + stoch_period + k_period + d_period:
        return {"k": 50.0, "d": 50.0}
    closes = [c["close"] for c in candles]
    rsi_series = []
    for i in range(rsi_period, len(closes)):
        window = closes[i - rsi_period:i + 1]
        g = [max(window[j] - window[j-1], 0) for j in range(1, len(window))]
        l = [max(window[j-1] - window[j], 0) for j in range(1, len(window))]
        ag = sum(g[:rsi_period]) / rsi_period
        al = sum(l[:rsi_period]) / rsi_period
        for k in range(rsi_period, len(g)):
            ag = (ag * (rsi_period - 1) + g[k]) / rsi_period
            al = (al * (rsi_period - 1) + l[k]) / rsi_period
        rsi_series.append(100.0 if al == 0 else 100 - 100 / (1 + ag / al))
    if len(rsi_series) < stoch_period:
        return {"k": 50.0, "d": 50.0}
    stoch_k_raw = []
    for i in range(stoch_period - 1, len(rsi_series)):
        w = rsi_series[i - stoch_period + 1:i + 1]
        lo, hi = min(w), max(w)
        stoch_k_raw.append(50.0 if hi == lo else (rsi_series[i] - lo) / (hi - lo) * 100)
    if len(stoch_k_raw) < k_period:
        return {"k": 50.0, "d": 50.0}
    k_sm = [sum(stoch_k_raw[i - k_period + 1:i + 1]) / k_period for i in range(k_period - 1, len(stoch_k_raw))]
    if len(k_sm) < d_period:
        return {"k": round(k_sm[-1], 1) if k_sm else 50.0, "d": 50.0}
    d_sm = [sum(k_sm[i - d_period + 1:i + 1]) / d_period for i in range(d_period - 1, len(k_sm))]
    return {"k": round(k_sm[-1], 1), "d": round(d_sm[-1], 1)}


# ─── MACD ─────────────────────────────────────────────────────────────────────

def calculate_macd(candles: list) -> dict:
    if len(candles) < 35:
        return {"macd": 0, "signal": 0, "cross": "none", "histogram": 0, "histogram_dir": "flat"}
    closes = [c["close"] for c in candles]

    def ema(values, period):
        k = 2 / (period + 1)
        r = [values[0]]
        for v in values[1:]:
            r.append(v * k + r[-1] * (1 - k))
        return r

    e12 = ema(closes, 12)
    e26 = ema(closes, 26)
    macd_line   = [e12[i] - e26[i] for i in range(len(closes))]
    signal_line = ema(macd_line, 9)

    lm, ls = macd_line[-1], signal_line[-1]
    pm, ps = macd_line[-2], signal_line[-2]
    hist      = lm - ls
    prev_hist = pm - ps

    if pm <= ps and lm > ls:
        cross = "bullish"
    elif pm >= ps and lm < ls:
        cross = "bearish"
    else:
        cross = "none"

    if abs(hist) > abs(prev_hist) * 1.05:
        hist_dir = "growing"
    elif abs(hist) < abs(prev_hist) * 0.95:
        hist_dir = "shrinking"
    else:
        hist_dir = "flat"

    return {
        "macd":          round(lm, 4),
        "signal":        round(ls, 4),
        "cross":         cross,
        "histogram":     round(hist, 4),
        "histogram_dir": hist_dir,
    }


# ─── Bollinger Bands ──────────────────────────────────────────────────────────

def calculate_bollinger_bands(candles: list, period: int = 20, std_dev: float = 2.0) -> dict:
    if len(candles) < period:
        return {}
    closes = [c["close"] for c in candles[-period:]]
    mid    = sum(closes) / period
    sigma  = (sum((x - mid) ** 2 for x in closes) / period) ** 0.5
    upper  = mid + std_dev * sigma
    lower  = mid - std_dev * sigma
    return {
        "upper":     round(upper, 4),
        "mid":       round(mid, 4),
        "lower":     round(lower, 4),
        "width_pct": round((upper - lower) / mid * 100 if mid else 0, 2),
    }


# ─── EMA лента ────────────────────────────────────────────────────────────────

def calculate_ema(values: list, period: int) -> float:
    if not values:
        return 0.0
    if len(values) < period:
        return values[-1]
    k = 2 / (period + 1)
    e = values[0]
    for v in values[1:]:
        e = v * k + e * (1 - k)
    return e

def calculate_ema_ribbon(candles: list) -> dict:
    """EMA 9, 21, 50 на 1H — главный фильтр тренда для скальпинга."""
    if len(candles) < 50:
        return {}
    closes = [c["close"] for c in candles]
    return {
        "ema9":  round(calculate_ema(closes, 9),  4),
        "ema21": round(calculate_ema(closes, 21), 4),
        "ema50": round(calculate_ema(closes, 50), 4),
    }


# ─── VWAP (внутридневной) ─────────────────────────────────────────────────────

def calculate_vwap(candles: list) -> float:
    """
    VWAP по последним 24 свечам 1H (приближение дневного VWAP).
    Цена выше VWAP = бычий контекст, ниже = медвежий.
    """
    window = candles[-24:] if len(candles) >= 24 else candles
    total_vol = sum(c["volume"] for c in window)
    if total_vol == 0:
        return 0.0
    vwap = sum(
        ((c["high"] + c["low"] + c["close"]) / 3) * c["volume"]
        for c in window
    ) / total_vol
    return round(vwap, 4)


# ─── Momentum (Rate of Change) ────────────────────────────────────────────────

def calculate_momentum(candles: list, period: int = 10) -> float:
    """ROC = (close_now - close_N_bars_ago) / close_N_bars_ago * 100"""
    if len(candles) < period + 1:
        return 0.0
    closes = [c["close"] for c in candles]
    return round((closes[-1] - closes[-period - 1]) / closes[-period - 1] * 100, 2)


# ─── Тренд ────────────────────────────────────────────────────────────────────

def detect_trend(candles: list) -> dict:
    if len(candles) < 5:
        return {"direction": "unknown", "consecutive_down": 0, "consecutive_up": 0,
                "lower_lows": False, "higher_highs": False, "slope": 0}

    closes = [c["close"] for c in candles]

    consec_down = 0
    consec_up   = 0
    for i in range(len(closes) - 1, 0, -1):
        if closes[i] < closes[i - 1]:
            if consec_up == 0:
                consec_down += 1
            else:
                break
        elif closes[i] > closes[i - 1]:
            if consec_down == 0:
                consec_up += 1
            else:
                break
        else:
            break

    recent = candles[-10:] if len(candles) >= 10 else candles
    lows   = [c["low"]  for c in recent]
    highs  = [c["high"] for c in recent]
    lower_lows   = lows[-1]  < lows[-3]  < lows[0]  if len(lows)  >= 4 else False
    higher_highs = highs[-1] > highs[-3] > highs[0] if len(highs) >= 4 else False

    n = min(10, len(closes))
    xs = list(range(n))
    ys = closes[-n:]
    mx, my = sum(xs) / n, sum(ys) / n
    num = sum((xs[i] - mx) * (ys[i] - my) for i in range(n))
    den = sum((xs[i] - mx) ** 2 for i in range(n))
    slope = round((num / den) / my * 100 if den and my else 0, 3)

    if consec_down >= 3 or (lower_lows and slope < -0.3):
        direction = "down"
    elif consec_up >= 3 or (higher_highs and slope > 0.3):
        direction = "up"
    elif consec_down >= 2:
        direction = "down"
    elif consec_up >= 2:
        direction = "up"
    else:
        direction = "sideways"

    return {
        "direction":        direction,
        "consecutive_down": consec_down,
        "consecutive_up":   consec_up,
        "lower_lows":       lower_lows,
        "higher_highs":     higher_highs,
        "slope":            slope,
    }


# ─── Pivot support/resistance ─────────────────────────────────────────────────

def _find_pivot_levels(candles: list, n: int = 2) -> tuple:
    """Swing pivot highs/lows — реальные уровни рынка."""
    if len(candles) < 6:
        return 0, 0
    price = candles[-1]["close"]
    pivot_highs, pivot_lows = [], []
    for i in range(n, len(candles) - n):
        h = candles[i]["high"]
        l = candles[i]["low"]
        if all(h >= candles[i - j]["high"] and h >= candles[i + j]["high"] for j in range(1, n + 1)):
            pivot_highs.append(h)
        if all(l <= candles[i - j]["low"] and l <= candles[i + j]["low"] for j in range(1, n + 1)):
            pivot_lows.append(l)
    res = min((h for h in pivot_highs if h > price * 1.002), default=0)
    sup = max((l for l in pivot_lows  if l < price * 0.998), default=0)
    return round(sup, 4), round(res, 4)


def calculate_support_resistance(candles_1h: list, candles_4h: list = None) -> dict:
    if not candles_1h:
        return {}
    vols = [c["volume"] for c in candles_1h]
    avg_vol = sum(vols) / len(vols) if vols else 1
    cur_vol = vols[-1] if vols else 0

    src = candles_4h if (candles_4h and len(candles_4h) >= 10) else candles_1h
    sup, res = _find_pivot_levels(src)

    if sup == 0 or res == 0:
        w = candles_1h[-30:]
        if sup == 0:
            sup = round(min(c["low"]  for c in w), 4)
        if res == 0:
            res = round(max(c["high"] for c in w), 4)

    return {
        "support":        sup,
        "resistance":     res,
        "avg_volume":     round(avg_vol, 0),
        "current_volume": round(cur_vol, 0),
        "volume_spike":   cur_vol > avg_vol * 1.5,
        "volume_ratio":   round(cur_vol / avg_vol if avg_vol else 1, 2),
    }


# ─── Volume анализ ────────────────────────────────────────────────────────────

def analyze_volume_profile(candles: list) -> dict:
    """
    Определяет характер объёма:
    - buying_pressure: рост цены + высокий объём
    - selling_pressure: падение цены + высокий объём
    - climax: экстремальный объём (потенциальный разворот)
    """
    if len(candles) < 10:
        return {"type": "normal", "ratio": 1.0, "climax": False}
    vols   = [c["volume"] for c in candles]
    avg    = sum(vols[:-3]) / max(len(vols[:-3]), 1)
    recent = candles[-3:]
    max_vol_candle = max(recent, key=lambda c: c["volume"])
    ratio  = max_vol_candle["volume"] / avg if avg else 1.0
    bullish = max_vol_candle["close"] > max_vol_candle["open"]
    climax  = ratio >= 2.5

    if climax and bullish:
        vtype = "buying_climax"
    elif climax and not bullish:
        vtype = "selling_climax"
    elif ratio >= 1.5 and bullish:
        vtype = "buying_pressure"
    elif ratio >= 1.5 and not bullish:
        vtype = "selling_pressure"
    else:
        vtype = "normal"

    return {"type": vtype, "ratio": round(ratio, 2), "climax": climax}


# ─── Order book ───────────────────────────────────────────────────────────────

def get_order_book_ratio(symbol: str = "TAOUSDT", depth: int = 20) -> dict:
    try:
        with httpx.Client(timeout=6) as c:
            ob = c.get(f"{BINANCE_BASE}/depth", params={"symbol": symbol, "limit": depth}).json()
            bids = sum(float(x[1]) for x in ob.get("bids", []))
            asks = sum(float(x[1]) for x in ob.get("asks", []))
            ratio = round(bids / asks, 2) if asks else 1.0
            return {"bid_vol": round(bids, 2), "ask_vol": round(asks, 2), "bid_ask_ratio": ratio}
    except Exception:
        return {}


# ─── Fear & Greed ─────────────────────────────────────────────────────────────

def get_fear_greed() -> dict:
    try:
        with httpx.Client(timeout=8) as c:
            d = c.get("https://api.alternative.me/fng/?limit=1").json()["data"][0]
            return {"value": int(d["value"]), "label": d["value_classification"]}
    except Exception:
        return {"value": 50, "label": "Unknown"}


# ─── История ──────────────────────────────────────────────────────────────────

def get_history(symbol: str = "TAOUSDT") -> dict:
    result = {}
    weekly = get_klines(symbol, "1w", 200)
    if weekly:
        all_highs  = [c["high"]  for c in weekly]
        all_lows   = [c["low"]   for c in weekly]
        all_closes = [c["close"] for c in weekly]
        ath = max(all_highs)
        atl = min(all_lows)
        cur = all_closes[-1]
        pct_ath = (cur - ath) / ath * 100
        pct_atl = (cur - atl) / atl * 100
        t4w  = (sum(all_closes[-4:]) / 4 - sum(all_closes[-8:-4]) / 4) / (sum(all_closes[-8:-4]) / 4) * 100 if len(all_closes) >= 8 else 0
        t12w = (all_closes[-1] - all_closes[-12]) / all_closes[-12] * 100 if len(all_closes) >= 12 else 0
        if pct_ath > -20:   cycle = "ЭЙФОРИЯ / РАСПРЕДЕЛЕНИЕ"
        elif pct_ath > -40: cycle = "КОРРЕКЦИЯ"
        elif pct_ath > -60: cycle = "МЕДВЕЖИЙ РЫНОК"
        elif pct_ath > -80: cycle = "КАПИТУЛЯЦИЯ"
        else:               cycle = "ГЛУБОКОЕ ДНО"
        result["weekly"] = {
            "ath": round(ath, 2), "atl": round(atl, 2),
            "pct_from_ath": round(pct_ath, 1),
            "pct_from_atl": round(pct_atl, 1),
            "trend_4w": round(t4w, 1), "trend_12w": round(t12w, 1),
            "cycle_phase": cycle,
            "key_resistances": sorted(set(round(h, 0) for h in all_highs), reverse=True)[:5],
            "key_supports":    sorted(set(round(l, 0) for l in all_lows))[:5],
        }
    daily = get_klines(symbol, "1d", 200)
    if daily:
        closes = [c["close"] for c in daily]
        ma20   = sum(closes[-20:]) / 20  if len(closes) >= 20  else None
        ma50   = sum(closes[-50:]) / 50  if len(closes) >= 50  else None
        ema200 = calculate_ema(closes, 200) if len(closes) >= 200 else None
        t7d    = (closes[-1] - closes[-7])  / closes[-7]  * 100 if len(closes) >= 7  else 0
        t30d   = (closes[-1] - closes[-30]) / closes[-30] * 100 if len(closes) >= 30 else 0
        result["daily"] = {
            "ma20":      round(ma20,   2) if ma20   else None,
            "ma50":      round(ma50,   2) if ma50   else None,
            "ema200":    round(ema200, 2) if ema200 else None,
            "trend_7d":  round(t7d,  1),
            "trend_30d": round(t30d, 1),
        }
    return result


# ─── Bottom patterns ──────────────────────────────────────────────────────────

def detect_bottom_patterns(candles_1h: list, candles_4h: list, candles_daily: list) -> dict:
    result = {
        "multiple_bottom": False, "bottom_level": 0.0, "bottom_touches": 0,
        "capitulation_drop": False, "drop_pct": 0.0,
        "volume_climax": False, "volume_climax_ratio": 0.0,
        "rsi_divergence": False,
        "hammer_reversal": False,
        "bottom_score": 0, "description": [],
    }
    desc = []

    if len(candles_4h) >= 20:
        lows_4h = [c["low"] for c in candles_4h[-40:]]
        local_lows = [lows_4h[i] for i in range(1, len(lows_4h) - 1)
                      if lows_4h[i] < lows_4h[i-1] and lows_4h[i] < lows_4h[i+1]]
        if len(local_lows) >= 3:
            base = sorted(local_lows)[0]
            cluster = [l for l in local_lows if l <= base * 1.02]
            if len(cluster) >= 3:
                result.update({"multiple_bottom": True, "bottom_level": round(base, 2),
                               "bottom_touches": len(cluster)})
                result["bottom_score"] += 3
                desc.append(f"🔁 {len(cluster)}x дно на ${base:.2f}")

    if len(candles_1h) >= 6:
        recent = candles_1h[-6:]
        pre = recent[:3]; drop = recent[3:]
        pre_chg  = (pre[-1]["close"] - pre[0]["open"]) / pre[0]["open"] * 100
        drop_chg = (drop[-1]["close"] - drop[0]["open"]) / drop[0]["open"] * 100
        if drop_chg <= -5.0 and abs(pre_chg) < 3.0:
            result.update({"capitulation_drop": True, "drop_pct": round(drop_chg, 2)})
            result["bottom_score"] += 2
            desc.append(f"💥 Капитуляция -{abs(drop_chg):.1f}% за 3 свечи")
        elif drop_chg <= -8.0:
            result.update({"capitulation_drop": True, "drop_pct": round(drop_chg, 2)})
            result["bottom_score"] += 3
            desc.append(f"💥 Резкий дамп -{abs(drop_chg):.1f}%")

    if len(candles_1h) >= 20:
        vols = [c["volume"] for c in candles_1h]
        avg  = sum(vols[:-3]) / max(len(vols[:-3]), 1)
        max_vol = max(c["volume"] for c in candles_1h[-3:])
        ratio = max_vol / avg if avg else 1.0
        if ratio >= 2.0:
            big = max(candles_1h[-3:], key=lambda c: c["volume"])
            if big["close"] < big["open"]:
                result.update({"volume_climax": True, "volume_climax_ratio": round(ratio, 1)})
                result["bottom_score"] += 2 if ratio >= 3.0 else 1
                desc.append(f"📊 Volume climax {ratio:.1f}x на падении")

    if len(candles_1h) >= 30:
        closes = [c["close"] for c in candles_1h]
        prev_min = None
        for i in range(len(closes) - 2, 5, -1):
            if closes[i] < closes[i-1] and closes[i] < closes[i+1]:
                prev_min = i; break
        if prev_min:
            rsi_now  = calculate_rsi(candles_1h)
            rsi_prev = calculate_rsi(candles_1h[:prev_min+1])
            if closes[-1] < closes[prev_min] and rsi_now > rsi_prev + 5:
                result["rsi_divergence"] = True
                result["bottom_score"] += 2
                desc.append(f"📈 RSI дивергенция (цена ↓, RSI ↑)")

    if candles_1h:
        last = candles_1h[-1]
        body  = abs(last["close"] - last["open"])
        lower = min(last["close"], last["open"]) - last["low"]
        upper = last["high"] - max(last["close"], last["open"])
        if body > 0 and lower >= body * 2 and upper <= body * 0.5:
            result["hammer_reversal"] = True
            result["bottom_score"] += 1
            desc.append(f"🔨 Молот — покупатели вернулись")

    result["description"] = desc
    result["bottom_score"] = min(result["bottom_score"], 10)
    return result


# ─── Главная функция сбора данных ─────────────────────────────────────────────

def collect_all_data(symbol: str = "TAOUSDT") -> dict:
    tao = get_ticker(symbol)
    btc = get_btc_ticker()
    fg  = get_fear_greed()

    c1h  = get_klines(symbol, "1h",  60)
    c4h  = get_klines(symbol, "4h",  50)
    cdly = get_klines(symbol, "1d",  30)

    history = get_history(symbol)

    rsi_1h    = calculate_rsi(c1h)
    rsi_4h    = calculate_rsi(c4h)
    stoch     = calculate_stoch_rsi(c1h)
    macd_1h   = calculate_macd(c1h)
    macd_4h   = calculate_macd(c4h)
    bb_1h     = calculate_bollinger_bands(c1h)
    bb_4h     = calculate_bollinger_bands(c4h)
    levels    = calculate_support_resistance(c1h, c4h)
    ema_r     = calculate_ema_ribbon(c1h)
    vwap      = calculate_vwap(c1h)
    momentum  = calculate_momentum(c1h)
    vol_prof  = analyze_volume_profile(c1h)
    trend     = detect_trend(c1h)
    trend_4h  = detect_trend(c4h)
    ob        = get_order_book_ratio(symbol)
    bottom    = detect_bottom_patterns(c1h, c4h, cdly)

    daily = history.get("daily", {})

    return {
        "tao":          tao,
        "btc":          btc,
        "fear_greed":   fg,
        "rsi_1h":       rsi_1h,
        "rsi_4h":       rsi_4h,
        "stoch_rsi_1h": stoch,
        "macd_1h":      macd_1h,
        "macd_4h":      macd_4h,
        "bb_1h":        bb_1h,
        "bb_4h":        bb_4h,
        "levels":       levels,
        "ema_ribbon":   ema_r,
        "vwap":         vwap,
        "momentum":     momentum,
        "volume_profile": vol_prof,
        "trend":        trend,
        "trend_4h":     trend_4h,
        "order_book":   ob,
        "history":      history,
        "bottom":       bottom,
        "symbol":       symbol,
        "timestamp":    datetime.now().strftime("%d.%m.%Y %H:%M"),
    }


# ─── Быстрый скан монеты ──────────────────────────────────────────────────────

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
        ticker    = get_ticker(symbol)
        c1h       = get_klines(symbol, "1h", 60)
        c4h       = get_klines(symbol, "4h", 40)
        rsi_1h    = calculate_rsi(c1h)
        rsi_4h    = calculate_rsi(c4h)
        macd      = calculate_macd(c1h)
        bb        = calculate_bollinger_bands(c1h)
        levels    = calculate_support_resistance(c1h, c4h)
        trend     = detect_trend(c1h)
        trend_4h  = detect_trend(c4h)
        return {
            "price":      ticker.get("price", 0),
            "change_24h": ticker.get("change_24h", 0),
            "rsi_1h":     rsi_1h,
            "rsi_4h":     rsi_4h,
            "macd":       macd,
            "bb":         bb,
            "support":    levels.get("support", 0),
            "resistance": levels.get("resistance", 0),
            "trend":      trend,
            "trend_4h":   trend_4h,
        }
    except Exception:
        return {}
