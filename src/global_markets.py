"""
Global bleeding check: GIFT Nifty (absolute override) + 8-index basket (70% threshold).
GIFT Nifty: checked via ^NSEI proxy (Nifty 50 previous close).
World index display uses cash indices (^DJI, ^IXIC) with futures fallback (YM=F, NQ=F).
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import requests
import pandas as pd
import yfinance as yf
import pytz

logger = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")

US_FUTURES = ["ES=F", "NQ=F", "YM=F"]
ASIAN_INDICES = ["^N225", "^HSI", "^KS11", "^AXJO", "000001.SS"]
ALL_GLOBAL = US_FUTURES + ASIAN_INDICES  # 8 total — used for bleeding basket check
BLEEDING_THRESHOLD = -1.5  # percent
BASKET_PCT = 0.70  # 70% of 8 = 6 indices

# Display indices for Telegram header: (canonical_key, primary_ticker, fallback_or_None)
# Primary = cash index (more recognisable); fallback = futures (24/7 available)
_DISPLAY_MAP = [
    ("^DJI",  "^DJI",  "YM=F"),   # Dow Jones
    ("^IXIC", "^IXIC", "NQ=F"),   # Nasdaq
    ("^N225", "^N225", None),      # Nikkei 225
    ("^HSI",  "^HSI",  None),      # Hang Seng
]


@dataclass
class GlobalStatus:
    gift_nifty_change_pct: float = 0.0
    gift_nifty_bleeding: bool = False
    basket_bleeding: bool = False
    bleeding: bool = False  # True if GIFT or basket triggered
    indices_below: list = field(default_factory=list)
    indices_data: dict = field(default_factory=dict)
    error: str = ""


def _fetch_one_ticker(ticker: str, retries: int = 3) -> float | None:
    """
    Fetch last-day % change for one ticker with up to `retries` attempts.
    Returns float on success, None if all attempts fail.
    """
    for attempt in range(1, retries + 1):
        try:
            df = yf.download(ticker, period="5d", interval="1d",
                             auto_adjust=True, progress=False)
            if df is None or df.empty:
                raise ValueError("empty result")
            if isinstance(df.columns, pd.MultiIndex):
                try:
                    df = df.xs(ticker, axis=1, level=1)
                except Exception:
                    df = df.droplevel(1, axis=1)
            closes = df["Close"].dropna().squeeze()
            if len(closes) < 2:
                raise ValueError("fewer than 2 closes")
            prev = float(closes.iloc[-2])
            last = float(closes.iloc[-1])
            if prev <= 0:
                raise ValueError("prev close <= 0")
            return round((last - prev) / prev * 100, 2)
        except Exception as e:
            logger.warning(f"{ticker} fetch attempt {attempt}/{retries} failed: {e}")
    logger.error(f"{ticker}: all {retries} fetch attempts failed")
    return None


def _fetch_gift_nifty_change() -> float:
    """
    Attempt to get GIFT Nifty % change via ^NSEI (Nifty 50 proxy).
    Returns 0.0 if all fetches fail.
    """
    pct = _fetch_one_ticker("^NSEI", retries=3)
    if pct is None:
        logger.warning("GIFT Nifty (^NSEI proxy): all retries failed — using 0.0")
        return 0.0
    return pct


def _fetch_global_index_changes() -> dict:
    """
    Fetch last-day % change for ALL_GLOBAL basket (8 indices, used for bleeding check).
    Each ticker is tried up to 3 times before being excluded.
    Returns {ticker: pct_change}.
    """
    results = {}
    for ticker in ALL_GLOBAL:
        pct = _fetch_one_ticker(ticker, retries=3)
        if pct is not None:
            results[ticker] = pct
        else:
            logger.warning(f"Global basket {ticker}: all retries exhausted — excluded from basket count")
    logger.info(f"Basket fetched: {len(results)}/{len(ALL_GLOBAL)} indices")
    return results


def check_global_bleeding() -> GlobalStatus:
    """
    Returns GlobalStatus with bleeding=True if:
    - GIFT Nifty < -1.5% (absolute override), OR
    - 70%+ (6 of 8) global indices < -1.5%

    indices_data contains basket tickers + display indices (^DJI, ^IXIC) with fallbacks.
    """
    status = GlobalStatus()

    # GIFT Nifty check first
    gift_pct = _fetch_gift_nifty_change()
    status.gift_nifty_change_pct = gift_pct
    if gift_pct < BLEEDING_THRESHOLD:
        status.gift_nifty_bleeding = True
        status.bleeding = True
        logger.info(f"GIFT Nifty bleeding: {gift_pct:.2f}%")

    # Global basket check (futures + Asian indices)
    index_changes = _fetch_global_index_changes()
    status.indices_data = dict(index_changes)

    below = [t for t, pct in index_changes.items() if pct < BLEEDING_THRESHOLD]
    status.indices_below = below
    if len(index_changes) > 0:
        ratio = len(below) / len(ALL_GLOBAL)
        if ratio >= BASKET_PCT:
            status.basket_bleeding = True
            if not status.bleeding:
                status.bleeding = True
            logger.info(f"Basket bleeding: {len(below)}/{len(ALL_GLOBAL)} indices below {BLEEDING_THRESHOLD}%")

    # Fetch display indices (^DJI, ^IXIC, ^N225, ^HSI) with primary→fallback retry
    for key, primary, fallback in _DISPLAY_MAP:
        if key in status.indices_data:
            continue  # already fetched (e.g. ^N225, ^HSI from basket)
        pct = _fetch_one_ticker(primary, retries=3)
        if pct is None and fallback:
            logger.info(f"Display index {primary} unavailable — trying fallback {fallback}")
            pct = _fetch_one_ticker(fallback, retries=3)
        if pct is not None:
            status.indices_data[key] = pct
            logger.info(f"Display index {key}: {pct:+.2f}%")
        else:
            logger.warning(f"Display index {key} ({primary}): all fetches failed — will show N/A")

    return status


def get_global_summary(status: GlobalStatus) -> str:
    """Short text summary for Telegram footer."""
    if status.bleeding:
        reason = "GIFT" if status.gift_nifty_bleeding else f"{len(status.indices_below)}/8 indices"
        return f"🔴 GLOBAL BLEEDING ({reason} < {BLEEDING_THRESHOLD}%)"
    if status.indices_below:
        return f"🟡 Global caution: {len(status.indices_below)}/8 indices weak"
    return f"🟢 Global markets stable"


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    s = check_global_bleeding()
    print(f"GIFT: {s.gift_nifty_change_pct:.2f}%  Bleeding: {s.bleeding}")
    print(f"Index changes: {s.indices_data}")
    print(get_global_summary(s))
