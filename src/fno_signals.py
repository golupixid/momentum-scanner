"""
FNO-specific data: OI (Open Interest) and PCR (Put-Call Ratio).
Source: NSE public API (with volume-proxy fallback when API is unavailable).
"""
import logging
import requests
import pandas as pd
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)

NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://www.nseindia.com/",
    "Connection": "keep-alive",
}

DATA_DIR = Path(__file__).parent.parent / "data"
OI_CACHE_FILE = DATA_DIR / "active_signals" / "oi_cache.csv"


def _get_nse_session():
    """
    Get a curl_cffi session with Chrome TLS fingerprint for NSE API.
    NSE blocks plain requests from cloud IPs; curl_cffi bypasses this.
    Falls back to plain requests if curl_cffi is unavailable.
    """
    try:
        from curl_cffi import requests as cr
        session = cr.Session(impersonate="chrome")
        logger.debug("NSE: using curl_cffi Chrome session")
    except ImportError:
        session = requests.Session()
        logger.debug("NSE: using plain requests session (curl_cffi not available)")
    return session


def fetch_nse_oi_data(symbol: str) -> dict:
    """
    Fetch OI data for FNO symbol from NSE option chain API.
    Uses curl_cffi Chrome fingerprint to bypass GitHub Actions IP blocks.
    Returns {'prev_oi': float, 'curr_oi': float, 'pcr': float} or {} on failure.
    """
    try:
        session = _get_nse_session()
        # Establish NSE session cookie first
        session.get("https://www.nseindia.com", headers=NSE_HEADERS, timeout=10)
        url = f"https://www.nseindia.com/api/option-chain-equities?symbol={symbol}"
        resp = session.get(url, headers=NSE_HEADERS, timeout=10)
        if resp.status_code != 200:
            return {}
        data = resp.json()
        records = data.get("records", {})
        ce_oi = sum(r.get("CE", {}).get("openInterest", 0)
                    for r in records.get("data", []) if "CE" in r)
        pe_oi = sum(r.get("PE", {}).get("openInterest", 0)
                    for r in records.get("data", []) if "PE" in r)
        if ce_oi <= 0:
            return {}
        pcr = pe_oi / ce_oi

        # Use CE OI change from tradeInfo if available
        trade_info = records.get("tradeInfo", {})
        prev_oi_raw = trade_info.get("totOI", 0)
        prev_oi = float(prev_oi_raw) if prev_oi_raw else ce_oi  # fallback to same

        return {"prev_oi": prev_oi, "curr_oi": float(ce_oi), "pcr": float(pcr)}
    except Exception as e:
        logger.debug(f"NSE OI fetch failed for {symbol}: {e}")
        return {}


def _volume_proxy_oi(symbol: str, df_daily: pd.DataFrame) -> dict:
    """
    Fix 5: Proxy OI from daily price/volume when NSE API is unavailable.
    Logic:
      - Volume spike (>1.5x 20d avg) + price rising → probable short covering
        (OI falling as shorts exit, price rising)
      - Volume spike + price falling → probable long unwinding
      - No volume spike → neutral OI
    Returns oi dict compatible with get_oi_data() output.
    """
    if df_daily is None or len(df_daily) < 21:
        return {"prev_oi": 0, "curr_oi": 0, "oi_change_pct": 0.0, "pcr": 1.0}

    last = df_daily.iloc[-1]
    prev = df_daily.iloc[-2]
    curr_vol = float(last.get("Volume", 0))
    avg_vol = float(df_daily["Volume"].tail(20).mean()) if "Volume" in df_daily.columns else 0
    vol_ratio = curr_vol / avg_vol if avg_vol > 0 else 1.0

    close_now = float(last["Close"])
    close_prev = float(prev["Close"])
    price_rising = close_now > close_prev
    price_change_pct = (close_now - close_prev) / close_prev * 100 if close_prev > 0 else 0

    if vol_ratio >= 1.5:
        if price_rising:
            # Short covering proxy: OI falling ~3% (short positions being closed)
            oi_change_pct = -3.0
            pcr = 1.2  # Bearish options hedging (PE > CE OI)
        else:
            # Long unwinding proxy: OI falling ~3% (long positions exiting)
            oi_change_pct = -3.0
            pcr = 0.8
    elif vol_ratio >= 1.2 and price_rising:
        # Mild short covering signal
        oi_change_pct = -2.5
        pcr = 1.1
    else:
        # Neutral
        oi_change_pct = 0.0
        pcr = 1.0

    # Synthetic OI values for interface compatibility
    base_oi = 1_000_000
    curr_oi = base_oi * (1 + oi_change_pct / 100)
    return {
        "prev_oi": float(base_oi),
        "curr_oi": float(curr_oi),
        "oi_change_pct": round(oi_change_pct, 2),
        "pcr": round(pcr, 2),
        "_proxy": True,
    }


def get_oi_data(symbol: str, oi_cache: dict = None, df_daily: pd.DataFrame = None) -> dict:
    """
    Get OI data for symbol. Uses cache if available, else tries NSE API,
    falls back to volume proxy.
    Returns {'prev_oi', 'curr_oi', 'oi_change_pct', 'pcr'}
    """
    if oi_cache and symbol in oi_cache:
        return oi_cache[symbol]

    raw = fetch_nse_oi_data(symbol)
    if raw:
        prev = raw.get("prev_oi", 0)
        curr = raw.get("curr_oi", 0)
        if prev > 0:
            change = (curr - prev) / prev * 100
        else:
            change = 0.0
        return {
            "prev_oi": prev,
            "curr_oi": curr,
            "oi_change_pct": round(change, 2),
            "pcr": raw.get("pcr", 1.0),
        }

    # Fallback to volume proxy
    if df_daily is not None and not df_daily.empty:
        return _volume_proxy_oi(symbol, df_daily)

    return {"prev_oi": 0, "curr_oi": 0, "oi_change_pct": 0.0, "pcr": 1.0}


def build_oi_cache_for_fno(fno_stocks: set, daily_data: dict) -> dict:
    """
    Fix 5: Build OI cache for all FNO stocks before parallel processing.
    Tries NSE API first; falls back to volume proxy from already-fetched daily_data.
    Returns {symbol: oi_dict}.
    """
    cache = {}
    fno_list = sorted(fno_stocks)
    logger.info(f"FNO: Building OI cache for {len(fno_list)} FNO stocks")

    nse_ok = 0
    proxy_used = 0

    for symbol in fno_list:
        df_d = daily_data.get(symbol)

        # Try NSE API
        raw = fetch_nse_oi_data(symbol)
        if raw:
            prev = raw.get("prev_oi", 0)
            curr = raw.get("curr_oi", 0)
            change = (curr - prev) / prev * 100 if prev > 0 else 0.0
            cache[symbol] = {
                "prev_oi": prev,
                "curr_oi": curr,
                "oi_change_pct": round(change, 2),
                "pcr": raw.get("pcr", 1.0),
            }
            nse_ok += 1
        else:
            # Fallback: volume proxy
            proxy = _volume_proxy_oi(symbol, df_d)
            cache[symbol] = proxy
            proxy_used += 1

    logger.info(
        f"FNO OI cache built: {len(cache)} stocks | "
        f"NSE API: {nse_ok} | volume proxy: {proxy_used}"
    )

    # Debug: show OI change distribution
    changes = [v.get("oi_change_pct", 0) for v in cache.values()]
    falling = [c for c in changes if c < -2.0]
    logger.info(
        f"FNO OI debug: {len(falling)} stocks with OI change < -2% "
        f"(potential short-covering candidates)"
    )

    return cache


def format_oi_tag(oi_data: dict, signal_date: datetime = None) -> str:
    """Format OI info for Telegram card."""
    if not oi_data or oi_data.get("curr_oi", 0) == 0:
        return ""
    date_str = (signal_date or datetime.now()).strftime("%d-%b")
    change = oi_data.get("oi_change_pct", 0)
    direction = "↑" if change > 0 else "↓"
    proxy_note = " (est)" if oi_data.get("_proxy") else ""
    return f"OI:{direction}{abs(change):.1f}%{proxy_note} {date_str}"


def classify_oi_pattern(oi_data: dict, price_rising: bool) -> str:
    """
    Classify OI pattern:
    LONG_BUILDUP: OI rising + price rising
    SHORT_COVER:  OI falling + price rising
    LONG_UNWIND:  OI falling + price falling
    SHORT_BUILDUP: OI rising + price falling
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
    data = get_oi_data("RELIANCE")
    print(f"OI data: {data}")
    print(f"Pattern: {classify_oi_pattern(data, price_rising=True)}")
