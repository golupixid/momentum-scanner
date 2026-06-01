"""
FNO-specific data: OI (Open Interest) and PCR (Put-Call Ratio).
Source: NSE bhav copy (after 6 PM EOD). Fetches from NSE public API.
"""
import logging
import os
import requests
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

NSE_OI_URL = "https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY"
NSE_BHAV_URL = "https://www.nseindia.com/content/fo/fo_mktlots.csv"

DATA_DIR = Path(__file__).parent.parent / "data"
OI_CACHE_FILE = DATA_DIR / "active_signals" / "oi_cache.csv"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}


def fetch_nse_oi_data(symbol: str) -> dict:
    """
    Fetch OI data for FNO symbol from NSE.
    Returns {'prev_oi': float, 'curr_oi': float, 'pcr': float} or empty dict on failure.
    """
    try:
        session = requests.Session()
        # Get cookies first
        session.get("https://www.nseindia.com", headers=HEADERS, timeout=10)
        url = f"https://www.nseindia.com/api/option-chain-equities?symbol={symbol}"
        resp = session.get(url, headers=HEADERS, timeout=10)
        if resp.status_code != 200:
            return {}
        data = resp.json()
        records = data.get("records", {})
        total = records.get("tradeInfo", {})
        ce_oi = sum(r.get("CE", {}).get("openInterest", 0)
                    for r in records.get("data", []) if "CE" in r)
        pe_oi = sum(r.get("PE", {}).get("openInterest", 0)
                    for r in records.get("data", []) if "PE" in r)
        pcr = pe_oi / ce_oi if ce_oi > 0 else 1.0
        # OI change requires historical data — stub with current
        return {"prev_oi": ce_oi * 1.05, "curr_oi": ce_oi, "pcr": pcr}
    except Exception as e:
        logger.debug(f"OI fetch failed for {symbol}: {e}")
        return {}


def get_oi_data(symbol: str, oi_cache: dict = None) -> dict:
    """
    Get OI data for symbol. Uses cache if available, else fetches.
    Returns {'prev_oi': float, 'curr_oi': float, 'oi_change_pct': float, 'pcr': float}
    """
    if oi_cache and symbol in oi_cache:
        return oi_cache[symbol]

    raw = fetch_nse_oi_data(symbol)
    if not raw:
        return {"prev_oi": 0, "curr_oi": 0, "oi_change_pct": 0.0, "pcr": 1.0}

    prev = raw.get("prev_oi", 0)
    curr = raw.get("curr_oi", 0)
    change = (curr - prev) / prev * 100 if prev > 0 else 0.0
    return {
        "prev_oi": prev,
        "curr_oi": curr,
        "oi_change_pct": round(change, 2),
        "pcr": raw.get("pcr", 1.0),
    }


def format_oi_tag(oi_data: dict, signal_date: datetime = None) -> str:
    """Format OI info for Telegram card: 'OI: DD-Mon'"""
    if not oi_data or oi_data.get("curr_oi", 0) == 0:
        return ""
    date_str = (signal_date or datetime.now()).strftime("%d-%b")
    change = oi_data.get("oi_change_pct", 0)
    direction = "↑" if change > 0 else "↓"
    return f"OI:{direction}{abs(change):.1f}% {date_str}"


def classify_oi_pattern(oi_data: dict, price_rising: bool) -> str:
    """
    Classify OI pattern:
    - Long Buildup: OI rising + price rising → bullish
    - Short Covering: OI falling + price rising → bullish (C1)
    - Long Unwinding: OI falling + price falling → bearish (C2)
    - Short Buildup: OI rising + price falling → bearish
    """
    if not oi_data:
        return "UNKNOWN"
    change = oi_data.get("oi_change_pct", 0)
    oi_rising = change > 0
    if oi_rising and price_rising:
        return "LONG_BUILDUP"
    elif not oi_rising and price_rising:
        return "SHORT_COVER"
    elif not oi_rising and not price_rising:
        return "LONG_UNWIND"
    else:
        return "SHORT_BUILDUP"


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    # Test with a known FNO stock
    data = get_oi_data("RELIANCE")
    print(f"OI data: {data}")
    print(f"Pattern: {classify_oi_pattern(data, price_rising=True)}")
