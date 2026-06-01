"""
Batch-fetches Weekly, Daily, and Hourly OHLCV data for all universe stocks.
Strategy: 9 batches of 50 stocks per timeframe = 27 total yfinance calls.
Hourly: >=4 candles today = use today; <4 = last 5 trading days; 8AM = prev full day.
"""
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
import pandas as pd
import yfinance as yf
import pytz

from src.universe import get_universe_batches, load_universe

logger = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")

WEEKLY_PERIOD = "1y"
WEEKLY_INTERVAL = "1wk"
DAILY_PERIOD = "6mo"
DAILY_INTERVAL = "1d"
HOURLY_DAYS = 60  # last 60 calendar days
HOURLY_INTERVAL = "1h"
INDEX_PERIOD = "3mo"
INDEX_INTERVAL = "1d"

RETRY_ATTEMPTS = 3
RETRY_DELAY = 2  # seconds


def _fetch_batch(tickers: list, period: str, interval: str, extra_args: dict = None) -> pd.DataFrame:
    """Fetch one batch via yfinance download with retry."""
    kwargs = dict(tickers=" ".join(tickers), period=period, interval=interval,
                  auto_adjust=True, progress=False, threads=True)
    if extra_args:
        kwargs.update(extra_args)

    for attempt in range(RETRY_ATTEMPTS):
        try:
            df = yf.download(**kwargs)
            return df
        except Exception as e:
            if attempt < RETRY_ATTEMPTS - 1:
                time.sleep(RETRY_DELAY)
            else:
                logger.warning(f"Batch fetch failed after {RETRY_ATTEMPTS} attempts: {e}")
                return pd.DataFrame()


def _fetch_hourly_batch(tickers: list) -> pd.DataFrame:
    """Fetch hourly data (max 60 days for yfinance 1h interval)."""
    end = datetime.now(IST)
    start = end - timedelta(days=60)
    for attempt in range(RETRY_ATTEMPTS):
        try:
            df = yf.download(
                tickers=" ".join(tickers),
                start=start.strftime("%Y-%m-%d"),
                end=end.strftime("%Y-%m-%d"),
                interval=HOURLY_INTERVAL,
                auto_adjust=True,
                progress=False,
                threads=True,
            )
            return df
        except Exception as e:
            if attempt < RETRY_ATTEMPTS - 1:
                time.sleep(RETRY_DELAY)
            else:
                logger.warning(f"Hourly batch fetch failed: {e}")
                return pd.DataFrame()


def _split_multi_df(df: pd.DataFrame, tickers: list) -> dict:
    """
    Split a yfinance multi-ticker DataFrame into per-symbol DataFrames.
    Handles both single-ticker (flat columns) and multi-ticker (MultiIndex columns).
    """
    result = {}
    if df.empty:
        return result

    if isinstance(df.columns, pd.MultiIndex):
        for ticker in tickers:
            sym = ticker.replace(".NS", "")
            try:
                sub = df.xs(ticker, axis=1, level=1) if ticker in df.columns.get_level_values(1) else pd.DataFrame()
                if not sub.empty:
                    sub = sub.dropna(how="all")
                    result[sym] = sub
            except Exception:
                pass
    else:
        # Single ticker returned flat
        if len(tickers) == 1:
            sym = tickers[0].replace(".NS", "")
            result[sym] = df.dropna(how="all")

    return result


def fetch_all_weekly(batches: list) -> dict:
    """Fetch 1wk data for all batches. Returns {symbol: df}."""
    all_data = {}
    for i, batch in enumerate(batches):
        logger.info(f"Weekly batch {i+1}/{len(batches)} ({len(batch)} tickers)")
        df = _fetch_batch(batch, period=WEEKLY_PERIOD, interval=WEEKLY_INTERVAL)
        all_data.update(_split_multi_df(df, batch))
    return all_data


def fetch_all_daily(batches: list) -> dict:
    """Fetch 1d data for all batches. Returns {symbol: df}."""
    all_data = {}
    for i, batch in enumerate(batches):
        logger.info(f"Daily batch {i+1}/{len(batches)} ({len(batch)} tickers)")
        df = _fetch_batch(batch, period=DAILY_PERIOD, interval=DAILY_INTERVAL)
        all_data.update(_split_multi_df(df, batch))
    return all_data


def fetch_all_hourly(batches: list) -> dict:
    """Fetch 1h data for all batches. Returns {symbol: df}."""
    all_data = {}
    for i, batch in enumerate(batches):
        logger.info(f"Hourly batch {i+1}/{len(batches)} ({len(batch)} tickers)")
        df = _fetch_hourly_batch(batch)
        all_data.update(_split_multi_df(df, batch))
    return all_data


def fetch_index_data(tickers: list) -> dict:
    """Fetch index/sector data. Returns {ticker: df}."""
    result = {}
    for ticker in tickers:
        try:
            df = yf.download(ticker, period=INDEX_PERIOD, interval=INDEX_INTERVAL,
                             auto_adjust=True, progress=False)
            if not df.empty:
                result[ticker] = df.dropna(how="all")
        except Exception as e:
            logger.warning(f"Index fetch failed for {ticker}: {e}")
    return result


def get_hourly_candles_today(df_hourly: pd.DataFrame) -> int:
    """Count how many hourly candles exist for today (IST)."""
    if df_hourly is None or df_hourly.empty:
        return 0
    now_ist = datetime.now(IST)
    today_str = now_ist.strftime("%Y-%m-%d")
    # Convert index to IST
    idx = df_hourly.index
    if hasattr(idx, 'tz_localize'):
        if idx.tz is None:
            idx = idx.tz_localize("UTC").tz_convert(IST)
        else:
            idx = idx.tz_convert(IST)
    today_candles = idx[idx.strftime("%Y-%m-%d") == today_str]
    return len(today_candles)


def get_relevant_hourly(df_hourly: pd.DataFrame, scan_time: datetime = None) -> pd.DataFrame:
    """
    Return the relevant hourly slice per spec:
    - 8AM scan: previous full trading day
    - <4 candles today: last 5 trading days (25-30 candles)
    - >=4 candles today: today's data
    """
    if df_hourly is None or df_hourly.empty:
        return df_hourly

    now_ist = scan_time or datetime.now(IST)
    today_str = now_ist.strftime("%Y-%m-%d")

    idx = df_hourly.index
    if hasattr(idx, 'tz') and idx.tz is None:
        idx = idx.tz_localize("UTC").tz_convert(IST)
    elif hasattr(idx, 'tz') and idx.tz is not None:
        idx = idx.tz_convert(IST)

    df_hourly = df_hourly.copy()
    df_hourly.index = idx

    is_8am = (now_ist.hour == 8 and now_ist.minute < 30)

    if is_8am:
        # Use previous full trading day
        dates = sorted(set(idx.strftime("%Y-%m-%d")))
        if len(dates) >= 2:
            prev_day = dates[-2] if dates[-1] == today_str else dates[-1]
            return df_hourly[df_hourly.index.strftime("%Y-%m-%d") == prev_day]
        return df_hourly.tail(8)

    today_candles = df_hourly[df_hourly.index.strftime("%Y-%m-%d") == today_str]
    if len(today_candles) >= 4:
        return today_candles

    # <4 candles today → last 5 trading days
    dates = sorted(set(idx.strftime("%Y-%m-%d")))
    last_5 = dates[-5:]
    return df_hourly[df_hourly.index.strftime("%Y-%m-%d").isin(last_5)]


def fetch_all_parallel(batches: list, max_workers: int = 4) -> tuple:
    """
    Fetch W+D+H data in parallel threads.
    Returns (weekly_data, daily_data, hourly_data) as dicts.
    """
    results = {"weekly": {}, "daily": {}, "hourly": {}}

    def fetch_w():
        results["weekly"] = fetch_all_weekly(batches)

    def fetch_d():
        results["daily"] = fetch_all_daily(batches)

    def fetch_h():
        results["hourly"] = fetch_all_hourly(batches)

    with ThreadPoolExecutor(max_workers=3) as ex:
        futures = [ex.submit(fetch_w), ex.submit(fetch_d), ex.submit(fetch_h)]
        for f in as_completed(futures):
            f.result()  # raise any exceptions

    return results["weekly"], results["daily"], results["hourly"]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    batches = get_universe_batches(50)
    print(f"Total batches: {len(batches)}")
    # Test single batch
    test_batch = batches[0][:3]
    print(f"Testing with: {test_batch}")
    df = _fetch_batch(test_batch, period="1mo", interval="1d")
    print(f"Shape: {df.shape}")
    split = _split_multi_df(df, test_batch)
    for sym, sdf in split.items():
        print(f"  {sym}: {len(sdf)} rows")
