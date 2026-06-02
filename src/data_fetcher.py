"""
Data fetcher — uses curl_cffi to spoof Chrome TLS fingerprint, bypassing
Yahoo Finance's GitHub Actions IP block. Works on ubuntu-latest runners.

curl_cffi makes yfinance requests look like a real Chrome browser,
which Yahoo Finance does not rate-limit or block.
"""
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

import pandas as pd
import yfinance as yf
import pytz

logger = logging.getLogger(__name__)
logging.getLogger("yfinance").setLevel(logging.CRITICAL)
logging.getLogger("urllib3").setLevel(logging.ERROR)

IST = pytz.timezone("Asia/Kolkata")

WEEKLY_PERIOD   = "1y"
WEEKLY_INTERVAL = "1wk"
DAILY_PERIOD    = "6mo"
DAILY_INTERVAL  = "1d"
HOURLY_INTERVAL = "1h"
INDEX_PERIOD    = "3mo"
INDEX_INTERVAL  = "1d"

BATCH_SIZE  = 50    # stocks per yfinance batch call
BATCH_DELAY = 0.3   # seconds between batches


# ── Session ───────────────────────────────────────────────────────────────────

_session = None


def _get_session():
    """
    Return a curl_cffi Chrome-impersonating session.
    Falls back to a plain requests.Session if curl_cffi is not installed.
    """
    global _session
    if _session is not None:
        return _session

    try:
        from curl_cffi import requests as cr
        _session = cr.Session(impersonate="chrome")
        logger.info("curl_cffi Chrome session ready (bypasses GitHub Actions block)")
    except ImportError:
        import requests
        _session = requests.Session()
        _session.headers["User-Agent"] = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36"
        )
        logger.warning("curl_cffi not installed — using plain requests session")

    return _session


# ── Column splitter ───────────────────────────────────────────────────────────

def _split_multi_df(df: pd.DataFrame, tickers: list) -> dict:
    """
    Split a multi-ticker yfinance DataFrame → {symbol: df}.
    Handles both (Price, Ticker) and (Ticker, Price) MultiIndex orderings.
    """
    result = {}
    if df is None or df.empty:
        return result

    OHLCV = {"Open", "High", "Low", "Close", "Volume",
              "Adj Close", "Dividends", "Stock Splits"}

    if isinstance(df.columns, pd.MultiIndex):
        lvl0 = set(df.columns.get_level_values(0))
        ticker_level = 1 if (lvl0 & OHLCV) else 0
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
        # Flat columns — single ticker
        if len(tickers) == 1 and not df.empty and len(df) >= 5:
            sym = tickers[0].replace(".NS", "")
            sub = df.dropna(how="all").copy()
            sub.columns = [c.title() if isinstance(c, str) else c for c in sub.columns]
            result[sym] = sub

    return result


# ── Batch download ────────────────────────────────────────────────────────────

def _fetch_batch(tickers: list, period: str, interval: str,
                 start: str = None, end: str = None) -> pd.DataFrame:
    """Download one batch with curl_cffi session and retry."""
    session = _get_session()
    kwargs  = dict(
        tickers=" ".join(tickers),
        interval=interval,
        auto_adjust=True,
        progress=False,
        threads=True,
        session=session,
    )
    if start and end:
        kwargs.update(start=start, end=end)
    else:
        kwargs["period"] = period

    for attempt in range(2):
        try:
            return yf.download(**kwargs)
        except Exception as e:
            if attempt == 0:
                time.sleep(2)
            else:
                logger.debug(f"Batch failed: {e}")
    return pd.DataFrame()


# ── Public fetchers ───────────────────────────────────────────────────────────

def _run_batches(symbols: list, period: str, interval: str,
                 start: str = None, end: str = None,
                 label: str = "") -> dict:
    all_data: dict = {}
    tickers  = [s + ".NS" for s in symbols]
    batches  = [tickers[i:i + BATCH_SIZE]
                for i in range(0, len(tickers), BATCH_SIZE)]

    for i, batch in enumerate(batches):
        df = _fetch_batch(batch, period=period, interval=interval,
                          start=start, end=end)
        all_data.update(_split_multi_df(df, batch))
        time.sleep(BATCH_DELAY)

    logger.info(f"{label or interval} fetch: {len(all_data)}/{len(symbols)} symbols with data")
    return all_data


def fetch_all_weekly(symbols: list) -> dict:
    return _run_batches(symbols, WEEKLY_PERIOD, WEEKLY_INTERVAL, label="Weekly")


def fetch_all_daily(symbols: list) -> dict:
    return _run_batches(symbols, DAILY_PERIOD, DAILY_INTERVAL, label="Daily")


def fetch_all_hourly(symbols: list) -> dict:
    end_dt  = datetime.now(IST)
    start_s = (end_dt - timedelta(days=60)).strftime("%Y-%m-%d")
    end_s   = end_dt.strftime("%Y-%m-%d")
    return _run_batches(symbols, period=None, interval=HOURLY_INTERVAL,
                        start=start_s, end=end_s, label="Hourly")


def fetch_index_data(tickers: list) -> dict:
    """Fetch index/sector data individually."""
    session = _get_session()
    result  = {}
    for ticker in tickers:
        try:
            df = yf.download(ticker, period=INDEX_PERIOD, interval=INDEX_INTERVAL,
                             auto_adjust=True, progress=False, session=session)
            if df is not None and not df.empty:
                if isinstance(df.columns, pd.MultiIndex):
                    df = df.droplevel(1, axis=1)
                result[ticker] = df.dropna(how="all")
        except Exception as e:
            logger.debug(f"Index {ticker}: {e}")
        time.sleep(0.2)
    return result


# ── Hourly slice helper ───────────────────────────────────────────────────────

def get_relevant_hourly(df_hourly: pd.DataFrame,
                        scan_time: datetime = None) -> pd.DataFrame:
    if df_hourly is None or df_hourly.empty:
        return df_hourly

    now       = scan_time or datetime.now(IST)
    today_str = now.strftime("%Y-%m-%d")

    idx = df_hourly.index
    if hasattr(idx, "tz") and idx.tz is None:
        idx = idx.tz_localize("UTC").tz_convert(IST)
    elif hasattr(idx, "tz") and idx.tz is not None:
        idx = idx.tz_convert(IST)

    df       = df_hourly.copy()
    df.index = idx
    dates    = sorted(set(idx.strftime("%Y-%m-%d")))

    if now.hour == 8 and now.minute < 30:
        prev = dates[-2] if len(dates) >= 2 and dates[-1] == today_str else dates[-1]
        return df[df.index.strftime("%Y-%m-%d") == prev]

    today_df = df[df.index.strftime("%Y-%m-%d") == today_str]
    if len(today_df) >= 4:
        return today_df

    return df[df.index.strftime("%Y-%m-%d").isin(dates[-5:])]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    from src.universe import get_universe_batches
    symbols = ["RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "SBIN"]
    print("Testing weekly fetch…")
    w = fetch_all_weekly(symbols)
    for s, df in w.items():
        print(f"  {s}: {len(df)} weekly bars")
