"""
News fetcher — новости + sentiment scoring.

Источники: CoinDesk, CoinTelegraph, Decrypt, TheBlock, Cointelegraph API, CoinGecko
Sentiment: простой keyword-based (без внешних ML-моделей, работает без pip-пакетов)
"""
import httpx
import feedparser
from datetime import datetime, timezone


RSS_FEEDS = [
    ("CoinDesk",      "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("CoinTelegraph", "https://cointelegraph.com/rss"),
    ("Decrypt",       "https://decrypt.co/feed"),
    ("TheBlock",      "https://www.theblock.co/rss.xml"),
    ("CryptoSlate",   "https://cryptoslate.com/feed/"),
    ("BeInCrypto",    "https://beincrypto.com/feed/"),
]

TAO_KEYWORDS = [
    "TAO", "Bittensor", "bittensor", "AI crypto", "decentralized AI",
    "subnet", "OpenTensor", "BitAPAI", "AI token", "machine learning crypto",
    "opentensor", "nakamoto", "dtao", "dTAO",
]

AI_KEYWORDS = [
    "OpenAI", "ChatGPT", "Nvidia", "artificial intelligence", "AI regulation",
    "AI breakthrough", "Anthropic", "Google AI", "Microsoft AI", "AI ETF",
    "AI crypto", "AI agent", "large language model", "LLM",
]

# Слова для sentiment — позитив / негатив
POSITIVE_WORDS = [
    "surge", "rally", "bullish", "pump", "breakout", "adoption", "partnership",
    "launch", "upgrade", "milestone", "record", "growth", "buy", "moon",
    "recover", "rebound", "boom", "rise", "gain", "positive", "optimistic",
    "integration", "listing", "approved", "invest", "accumulate",
    "рост", "рекорд", "купить", "подъём", "прорыв", "листинг",
]

NEGATIVE_WORDS = [
    "crash", "dump", "bearish", "hack", "exploit", "ban", "regulation",
    "sell", "drop", "decline", "fear", "loss", "scam", "fraud", "lawsuit",
    "collapse", "plunge", "fall", "risk", "warning", "concern", "delay",
    "uncertainty", "слив", "падение", "запрет", "взлом", "мошенничество",
]


def _sentiment(title: str, summary: str) -> str:
    text = (title + " " + summary).lower()
    pos = sum(1 for w in POSITIVE_WORDS if w.lower() in text)
    neg = sum(1 for w in NEGATIVE_WORDS if w.lower() in text)
    if pos > neg:
        return "positive"
    elif neg > pos:
        return "negative"
    return "neutral"


def fetch_rss_news(limit_per_feed=3) -> list:
    all_news = []
    for source, url in RSS_FEEDS:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:limit_per_feed]:
                title   = entry.get("title", "")
                summary = entry.get("summary", "")[:300]
                link    = entry.get("link", "")
                published = entry.get("published", "")
                all_news.append({
                    "source":    source,
                    "title":     title,
                    "summary":   summary,
                    "link":      link,
                    "published": published,
                    "sentiment": _sentiment(title, summary),
                })
        except Exception:
            continue
    return all_news


def filter_relevant_news(news: list) -> dict:
    tao_news = []
    ai_news  = []
    for item in news:
        text = (item["title"] + " " + item["summary"]).lower()
        if any(kw.lower() in text for kw in TAO_KEYWORDS):
            tao_news.append(item)
        elif any(kw.lower() in text for kw in AI_KEYWORDS):
            ai_news.append(item)
    # Сортируем: негативные первыми (они важнее для риск-оценки)
    tao_news.sort(key=lambda x: 0 if x["sentiment"] == "negative" else 1)
    return {"tao": tao_news[:4], "ai": ai_news[:4]}


def get_coingecko_market() -> dict:
    """Данные TAO из CoinGecko: market cap rank, sentiment_votes, dev activity."""
    try:
        with httpx.Client(timeout=10) as c:
            r = c.get(
                "https://api.coingecko.com/api/v3/coins/bittensor",
                params={"localization": "false", "tickers": "false",
                        "market_data": "true", "community_data": "true",
                        "developer_data": "false", "sparkline": "false"}
            )
            d = r.json()
            md = d.get("market_data", {})
            return {
                "market_cap_rank":       d.get("market_cap_rank"),
                "sentiment_votes_up":    d.get("sentiment_votes_up_percentage"),
                "sentiment_votes_down":  d.get("sentiment_votes_down_percentage"),
                "price_change_7d":       md.get("price_change_percentage_7d"),
                "price_change_30d":      md.get("price_change_percentage_30d"),
                "ath_change_percentage": md.get("ath_change_percentage", {}).get("usd"),
            }
    except Exception:
        return {}


def get_coingecko_events() -> list:
    """7-дневный ценовой график TAO из CoinGecko."""
    try:
        with httpx.Client(timeout=10) as c:
            data = c.get(
                "https://api.coingecko.com/api/v3/coins/bittensor/market_chart",
                params={"vs_currency": "usd", "days": "7", "interval": "daily"}
            ).json()
            prices  = data.get("prices", [])
            changes = []
            for i in range(1, len(prices)):
                prev = prices[i-1][1]
                curr = prices[i][1]
                pct  = ((curr - prev) / prev) * 100
                date = datetime.fromtimestamp(prices[i][0]/1000).strftime("%d.%m")
                changes.append({"date": date, "price": round(curr, 2), "change": round(pct, 1)})
            return changes
    except Exception:
        return []


def get_all_news() -> dict:
    raw      = fetch_rss_news()
    filtered = filter_relevant_news(raw)
    history  = get_coingecko_events()
    cg_data  = get_coingecko_market()

    # Общий sentiment по TAO новостям
    tao_news = filtered["tao"]
    pos = sum(1 for n in tao_news if n.get("sentiment") == "positive")
    neg = sum(1 for n in tao_news if n.get("sentiment") == "negative")
    overall = "positive" if pos > neg else ("negative" if neg > pos else "neutral")

    return {
        "tao_news":          tao_news,
        "ai_news":           filtered["ai"],
        "price_history_7d":  history,
        "coingecko":         cg_data,
        "overall_sentiment": overall,
        "pos_count":         pos,
        "neg_count":         neg,
    }
