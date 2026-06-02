"""
Data fetcher — public interface consumed by parallel_runner.py.
Primary source: jugaad-data via nse_fetcher (works on GitHub-hosted runners).
Hourly data: yfinance best-effort (empty dict if blocked; execution plans
             fall back to daily-based estimates in that case).
"""
import logging
import time
from datetime import datetime, timedelta

import pandas as pd
import pytz

logger = logging.getLogger(__name__)
logging.getLogger("yfinance").setLevel(logging.CRITICAL)
logging.getLogger("urllib3").setLevel(logging.ERROR)
IST = pytz.timezone("Asia/Kolkata")


# ── Main fetchers (delegated to nse_fetcher) ──────────────────────────────────

def fetch_all_data(symbols: list) -> tuple:
    """
    Fetch daily + weekly data for all symbols via jugaad-data.
    Returns (daily_data, weekly_data) as {symbol: df} dicts.
    """
    from src.nse_fetcher import fetch_all_stocks, build_daily_weekly
    raw = fetch_all_stocks(symbols)
    return build_daily_weekly(raw)


# ── Hourly (best-effort yfinance) ─────────────────────────────────────────────

def fetch_all_hourly(symbols: list) -> dict:
    """
    Try to fetch 60-day hourly data via yfinance.
    Returns empty dict if GitHub runner IPs are blocked (normal on CI).
    Execution plans will fall back to daily-based estimates.
    """
    try:
        import yfinance as yf
        import requests

        session = requests.Session()
        session.headers["User-Agent"] = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36"
        )

        end   = datetime.now(IST)
        start = (end - timedelta(days=60)).strftime("%Y-%m-%d")
        end_s = end.strftime("%Y-%m-%d")

        results: dict = {}
        batch_size = 30
        batches = [symbols[i:i+batch_size] for i in range(0, len(symbols), batch_size)]

        for batch in batches:
            tickers = [s + ".NS" for s in batch]
            try:
                df = yf.download(
                    " ".join(tickers), start=start, end=end_s,
                    interval="1h", auto_adjust=True,
                    progress=False, threads=True, session=session,
                )
                if df.empty:
                    continue
                if isinstance(df.columns, pd.MultiIndex):
                    for ticker in tickers:
                        sym = ticker.replace(".NS", "")
                        avail = df.columns.get_level_values(1)
                        if ticker in avail:
                            sub = df.xs(ticker, axis=1, level=1).dropna(how="all")
                            if len(sub) >= 4:
                                results[sym] = sub
            except Exception:
                pass
            time.sleep(0.5)

        logger.info(f"Hourly fetch: {len(results)}/{len(symbols)} symbols "
                    f"({'CI blocked — using daily fallback' if not results else 'OK'})")
        return results

    except Exception as e:
        logger.info(f"Hourly fetch skipped: {e}")
        return {}


# ── Hourly slice helper (unchanged) ──────────────────────────────────────────

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
