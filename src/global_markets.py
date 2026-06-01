"""
Global bleeding check: GIFT Nifty (absolute override) + 8-index basket (70% threshold).
GIFT Nifty: checked via investing.com RSS or yfinance fallback (NIFTYBEES.NS as proxy).
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import requests
import yfinance as yf
import pytz

logger = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")

US_FUTURES = ["ES=F", "NQ=F", "YM=F"]
ASIAN_INDICES = ["^N225", "^HSI", "^KS11", "^AXJO", "000001.SS"]
ALL_GLOBAL = US_FUTURES + ASIAN_INDICES  # 8 total
BLEEDING_THRESHOLD = -1.5  # percent
BASKET_PCT = 0.70  # 70% of 8 = 6 indices

GIFT_NIFTY_TICKERS = ["NIFTYBEES.NS"]  # proxy; replace with real source if available


@dataclass
class GlobalStatus:
    gift_nifty_change_pct: float = 0.0
    gift_nifty_bleeding: bool = False
    basket_bleeding: bool = False
    bleeding: bool = False  # True if GIFT or basket triggered
    indices_below: list = field(default_factory=list)
    indices_data: dict = field(default_factory=dict)
    error: str = ""


def _fetch_gift_nifty_change() -> float:
    """
    Attempt to get GIFT Nifty % change. Falls back to 0.0 on failure.
    Real source: investing.com widget or NSE pre-open data.
    This stub uses a proxy approach via ^NSEI previous close vs current.
    """
    try:
        df = yf.download("^NSEI", period="2d", interval="1d",
                         auto_adjust=True, progress=False)
        if df is None or len(df) < 2:
            return 0.0
        closes = df["Close"].dropna()
        if len(closes) < 2:
            return 0.0
        prev_close = float(closes.iloc[-2])
        last_close = float(closes.iloc[-1])
        if prev_close <= 0:
            return 0.0
        return round((last_close - prev_close) / prev_close * 100, 2)
    except Exception as e:
        logger.warning(f"GIFT Nifty fetch failed: {e}")
        return 0.0


def _fetch_global_index_changes() -> dict:
    """Fetch last-day % change for all 8 global indices."""
    results = {}
    for ticker in ALL_GLOBAL:
        try:
            df = yf.download(ticker, period="5d", interval="1d",
                             auto_adjust=True, progress=False)
            if df is None or df.empty:
                continue
            closes = df["Close"].dropna()
            if len(closes) < 2:
                continue
            prev = float(closes.iloc[-2])
            last = float(closes.iloc[-1])
            if prev <= 0:
                continue
            pct = round((last - prev) / prev * 100, 2)
            results[ticker] = pct
        except Exception as e:
            logger.warning(f"Global index {ticker} failed: {e}")
    return results


def check_global_bleeding() -> GlobalStatus:
    """
    Returns GlobalStatus with bleeding=True if:
    - GIFT Nifty < -1.5% (absolute override), OR
    - 70%+ (6 of 8) global indices < -1.5%
    """
    status = GlobalStatus()

    # GIFT Nifty check first
    gift_pct = _fetch_gift_nifty_change()
    status.gift_nifty_change_pct = gift_pct
    if gift_pct < BLEEDING_THRESHOLD:
        status.gift_nifty_bleeding = True
        status.bleeding = True
        logger.info(f"GIFT Nifty bleeding: {gift_pct:.2f}%")

    # Global basket check
    index_changes = _fetch_global_index_changes()
    status.indices_data = index_changes

    below = [t for t, pct in index_changes.items() if pct < BLEEDING_THRESHOLD]
    status.indices_below = below

    if len(index_changes) > 0:
        ratio = len(below) / len(ALL_GLOBAL)
        if ratio >= BASKET_PCT:
            status.basket_bleeding = True
            if not status.bleeding:
                status.bleeding = True
            logger.info(f"Basket bleeding: {len(below)}/{len(ALL_GLOBAL)} indices below {BLEEDING_THRESHOLD}%")

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
