"""
Batch-fetches Weekly, Daily, and Hourly OHLCV data for all universe stocks.
Strategy: batches of 30 stocks per yfinance call (W+D+H).
Uses a browser-like session to avoid Yahoo Finance IP blocking on CI runners.
"""
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
import pandas as pd
import requests
import yfinance as yf
import pytz

from src.universe import get_universe_batches, load_universe

logger = logging.getLogger(__name__)
logging.getLogger("yfinance").setLevel(logging.CRITICAL)  # suppress ticker-level noise

IST = pytz.timezone("Asia/Kolkata")

WEEKLY_PERIOD  = "1y"
WEEKLY_INTERVAL = "1wk"
DAILY_PERIOD   = "6mo"
DAILY_INTERVAL  = "1d"
HOURLY_INTERVAL = "1h"
INDEX_PERIOD   = "3mo"
INDEX_INTERVAL  = "1d"

RETRY_ATTEMPTS = 2
RETRY_DELAY    = 4   # seconds between retries
BATCH_DELAY    = 1.0 # seconds between batch calls

# ── Browser-like session ──────────────────────────────────────────────────────
_session: requests.Session | None = None


def _get_session() -> requests.Session:
    """
    Return a cached requests.Session that looks like a real browser.
    Visits finance.yahoo.com first to pick up cookies (crumb, etc.)
    This prevents Yahoo Finance from blocking GitHub Actions runner IPs.
    """
    global _session
    if _session is not None:
        return _session

    s = requests.Session()
    s.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection":      "keep-alive",
        "Referer":         "https://finance.yahoo.com/",
    })

    # Seed cookies
    for url in [
        "https://finance.yahoo.com",
        "https://query1.finance.yahoo.com/v1/test/getcrumb",
    ]:
        try:
            s.get(url, timeout=10, allow_redirects=True)
            time.sleep(0.5)
        except Exception:
            pass

    _session = s
    logger.info("Yahoo Finance session initialised")
    return _session


# ── Core batch downloader ─────────────────────────────────────────────────────

def _fetch_batch(tickers: list, period: str, interval: str,
                 start: str = None, end: str = None) -> pd.DataFrame:
    """Download one batch with retry. Returns raw yfinance DataFrame."""
    session = _get_session()

    base = dict(
        tickers=" ".join(tickers),
        interval=interval,
        auto_adjust=True,
        progress=False,
        threads=True,
        session=session,
    )
    if start and end:
        base.update(start=start, end=end)
    else:
        base["period"] = period

    for attempt in range(RETRY_ATTEMPTS):
        try:
            df = yf.download(**base)
            return df
        except Exception as e:
            if attempt < RETRY_ATTEMPTS - 1:
                logger.debug(f"Batch retry {attempt+1}: {e}")
                time.sleep(RETRY_DELAY)
            else:
                logger.warning(f"Batch failed after {RETRY_ATTEMPTS} attempts: {e}")
    return pd.DataFrame()


# ── Column splitter ───────────────────────────────────────────────────────────

def _split_multi_df(df: pd.DataFrame, tickers: list) -> dict:
    """
    Split a multi-ticker yfinance DataFrame into {symbol: df} dict.
    Handles (Price, Ticker) and (Ticker, Price) MultiIndex orderings,
    plus single-ticker flat DataFrames.
    """
    result = {}
    if df is None or df.empty:
        return result

    OHLCV = {"Open", "High", "Low", "Close", "Volume",
              "Adj Close", "Dividends", "Stock Splits"}

    if isinstance(df.columns, pd.MultiIndex):
        lvl0_vals = set(df.columns.get_level_values(0))
        # If level 0 contains price fields → (Price, Ticker)
        ticker_level = 1 if lvl0_vals & OHLCV else 0

        available = set(df.columns.get_level_values(ticker_level))
        for ticker in tickers:
            if ticker not in available:
                continue
            sym = ticker.replace(".NS", "")
            try:
                sub = df.xs(ticker, axis=1, level=ticker_level).copy()
                sub = sub.dropna(how="all")
                if len(sub) >= 5:
                    sub.columns = [c.title() if isinstance(c, str) else c
                                   for c in sub.columns]
                    result[sym] = sub
            except Exception:
                pass
    else:
        # Flat: happens when only one ticker returned data
        if len(tickers) == 1 and len(df) >= 5:
            sym = tickers[0].replace(".NS", "")
            sub = df.dropna(how="all").copy()
            sub.columns = [c.title() if isinstance(c, str) else c for c in sub.columns]
            result[sym] = sub

    return result


# ── Timeframe fetchers ────────────────────────────────────────────────────────

def fetch_all_weekly(batches: list) -> dict:
    all_data: dict = {}
    for i, batch in enumerate(batches):
        df = _fetch_batch(batch, period=WEEKLY_PERIOD, interval=WEEKLY_INTERVAL)
        all_data.update(_split_multi_df(df, batch))
        time.sleep(BATCH_DELAY)
    logger.info(f"Weekly fetch complete: {len(all_data)} symbols with data")
    return all_data


def fetch_all_daily(batches: list) -> dict:
    all_data: dict = {}
    for i, batch in enumerate(batches):
        df = _fetch_batch(batch, period=DAILY_PERIOD, interval=DAILY_INTERVAL)
        all_data.update(_split_multi_df(df, batch))
        time.sleep(BATCH_DELAY)
    logger.info(f"Daily fetch complete: {len(all_data)} symbols with data")
    return all_data


def fetch_all_hourly(batches: list) -> dict:
    all_data: dict = {}
    end_dt   = datetime.now(IST)
    start_dt = end_dt - timedelta(days=60)
    start    = start_dt.strftime("%Y-%m-%d")
    end      = end_dt.strftime("%Y-%m-%d")

    for i, batch in enumerate(batches):
        df = _fetch_batch(batch, period=None, interval=HOURLY_INTERVAL,
                          start=start, end=end)
        all_data.update(_split_multi_df(df, batch))
        time.sleep(BATCH_DELAY)
    logger.info(f"Hourly fetch complete: {len(all_data)} symbols with data")
    return all_data


def fetch_index_data(tickers: list) -> dict:
    """Fetch index / sector data. Returns {ticker: df}."""
    session = _get_session()
    result  = {}
    for ticker in tickers:
        try:
            df = yf.download(ticker, period=INDEX_PERIOD, interval=INDEX_INTERVAL,
                             auto_adjust=True, progress=False, session=session)
            if df is not None and not df.empty:
                if isinstance(df.columns, pd.MultiIndex):
                    df = df.xs(ticker, axis=1, level=1) if ticker in df.columns.get_level_values(1) else df
                result[ticker] = df.dropna(how="all")
        except Exception as e:
            logger.debug(f"Index {ticker} failed: {e}")
        time.sleep(0.3)
    return result


# ── Hourly slice helper ───────────────────────────────────────────────────────

def get_relevant_hourly(df_hourly: pd.DataFrame,
                        scan_time: datetime = None) -> pd.DataFrame:
    """Return hourly slice per spec timing rules."""
    if df_hourly is None or df_hourly.empty:
        return df_hourly

    now = scan_time or datetime.now(IST)
    today_str = now.strftime("%Y-%m-%d")

    idx = df_hourly.index
    if hasattr(idx, "tz") and idx.tz is None:
        idx = idx.tz_localize("UTC").tz_convert(IST)
    elif hasattr(idx, "tz") and idx.tz is not None:
        idx = idx.tz_convert(IST)

    df = df_hourly.copy()
    df.index = idx

    is_8am = (now.hour == 8 and now.minute < 30)
    dates  = sorted(set(idx.strftime("%Y-%m-%d")))

    if is_8am:
        prev = dates[-2] if len(dates) >= 2 and dates[-1] == today_str else dates[-1]
        return df[df.index.strftime("%Y-%m-%d") == prev]

    today_df = df[df.index.strftime("%Y-%m-%d") == today_str]
    if len(today_df) >= 4:
        return today_df

    last_5 = dates[-5:]
    return df[df.index.strftime("%Y-%m-%d").isin(last_5)]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    batches = get_universe_batches(30)
    test    = [batches[0][:3]]   # just 3 tickers
    print("Testing weekly fetch with 3 tickers…")
    w = fetch_all_weekly(test)
    for sym, df in w.items():
        print(f"  {sym}: {len(df)} bars  cols={list(df.columns)}")
