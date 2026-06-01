"""
News Scanner — two parts:
Part A: Stock news (last 30 days) for final 15 ranked stocks only. Advisory.
Part B: Market headlines (World top 3 + India top 10) for all scans.
"""
import logging
import re
import time
from datetime import datetime, timedelta
from typing import Optional
import feedparser
import requests

logger = logging.getLogger(__name__)

MAX_HEADLINE_LEN = 80
NEWS_CACHE_TTL = 3600  # seconds

_headline_cache = {"data": None, "ts": 0}

MARKET_FEEDS = [
    # India
    "https://feeds.feedburner.com/ndtvprofit-latest",
    "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
    "https://www.moneycontrol.com/rss/marketreports.xml",
    # World
    "https://feeds.reuters.com/reuters/businessNews",
    "https://feeds.bbci.co.uk/news/business/rss.xml",
]

STOCK_NEWS_FEEDS = [
    "https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms",
    "https://www.moneycontrol.com/rss/latestnews.xml",
]


def _clean(text: str, maxlen: int = MAX_HEADLINE_LEN) -> str:
    text = re.sub(r"<[^>]+>", "", text or "").strip()
    text = re.sub(r"\s+", " ", text)
    return text[:maxlen]


def _fetch_feed(url: str, timeout: int = 8) -> list:
    """Fetch RSS feed. Returns list of (title, published_parsed) tuples."""
    try:
        feed = feedparser.parse(url)
        items = []
        for entry in feed.entries[:20]:
            title = _clean(entry.get("title", ""))
            pub = entry.get("published_parsed", None)
            if title:
                items.append((title, pub))
        return items
    except Exception as e:
        logger.debug(f"Feed fetch failed {url}: {e}")
        return []


def fetch_market_headlines(force_refresh: bool = False) -> dict:
    """
    Fetch market headlines (cached at 8AM, refreshed at 1PM + 3PM).
    Returns {'india': [...], 'world': [...]}
    """
    global _headline_cache
    now = time.time()
    if not force_refresh and _headline_cache["data"] and (now - _headline_cache["ts"]) < NEWS_CACHE_TTL:
        return _headline_cache["data"]

    india_feeds = MARKET_FEEDS[:3]
    world_feeds = MARKET_FEEDS[3:]

    india_items = []
    for url in india_feeds:
        india_items.extend(_fetch_feed(url))

    world_items = []
    for url in world_feeds:
        world_items.extend(_fetch_feed(url))

    # Deduplicate and take top N
    india_titles = list(dict.fromkeys(t for t, _ in india_items))[:10]
    world_titles = list(dict.fromkeys(t for t, _ in world_items))[:3]

    result = {"india": india_titles, "world": world_titles,
              "fetched_at": datetime.now().strftime("%H:%M")}
    _headline_cache = {"data": result, "ts": now}
    return result


def fetch_stock_news(symbol: str, days: int = 30) -> dict:
    """
    Fetch news for a specific stock (last 30 days). Advisory only.
    Returns {'items': [(title, days_ago)], 'sentiment': 'positive'|'negative'|'neutral'}
    """
    items = []
    cutoff = datetime.now() - timedelta(days=days)

    for url in STOCK_NEWS_FEEDS:
        entries = _fetch_feed(url)
        for title, pub in entries:
            if symbol.upper() in title.upper():
                if pub:
                    pub_dt = datetime(*pub[:6])
                    if pub_dt > cutoff:
                        days_ago = (datetime.now() - pub_dt).days
                        items.append((title, days_ago))
                else:
                    items.append((title, 0))

    # Basic sentiment heuristic
    negative_kw = ["sebi", "notice", "fraud", "scam", "penalty", "loss", "downgrade",
                   "default", "debt", "crisis", "fall", "drop", "decline", "concern"]
    positive_kw = ["order", "win", "profit", "record", "upgrade", "growth",
                   "expansion", "contract", "launch", "partnership"]

    sentiment = "neutral"
    combined_text = " ".join(t for t, _ in items).lower()
    neg_hits = sum(1 for k in negative_kw if k in combined_text)
    pos_hits = sum(1 for k in positive_kw if k in combined_text)

    if neg_hits > pos_hits + 1:
        sentiment = "negative"
    elif pos_hits > neg_hits:
        sentiment = "positive"

    return {
        "symbol": symbol,
        "items": sorted(items, key=lambda x: x[1])[:5],  # most recent first
        "sentiment": sentiment,
        "negative": sentiment == "negative",
    }


def format_news_for_card(news: dict) -> str:
    """Format news for Telegram signal card (advisory lines)."""
    if not news or not news.get("items"):
        return "📰 NEWS: Pure technical — no major news found"

    lines = []
    for title, days_ago in news["items"][:3]:
        age = f"{days_ago}d ago" if days_ago > 0 else "today"
        emoji = "🔴" if news.get("negative") else "📰"
        lines.append(f"{emoji} {title[:60]} ({age})")

    return "\n".join(lines)


def format_headlines_for_telegram(headlines: dict) -> str:
    """Format market headlines for Telegram header message."""
    lines = ["📰 *MARKET HEADLINES*"]
    if headlines.get("world"):
        lines.append("🌍 *World:*")
        for h in headlines["world"][:3]:
            lines.append(f"  • {h}")
    if headlines.get("india"):
        lines.append("🇮🇳 *India:*")
        for h in headlines["india"][:10]:
            lines.append(f"  • {h}")
    fetched = headlines.get("fetched_at", "")
    if fetched:
        lines.append(f"_Updated: {fetched}_")
    return "\n".join(lines)


def fetch_news_for_signals(symbols: list) -> dict:
    """Fetch news for a list of symbols (final 15 only). Returns {symbol: news_dict}."""
    result = {}
    for sym in symbols[:15]:  # hard cap at 15 per spec
        result[sym] = fetch_stock_news(sym)
        time.sleep(0.5)  # gentle rate limit
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    headlines = fetch_market_headlines(force_refresh=True)
    print("India headlines:", headlines.get("india", [])[:3])
    print("World headlines:", headlines.get("world", [])[:2])
