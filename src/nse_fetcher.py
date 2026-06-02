"""
NSE data fetcher using jugaad-data.
Works reliably on GitHub-hosted runners (downloads from NSE, not Yahoo Finance).
Provides daily + weekly OHLCV for all NSE stocks in our universe.

Speed: ~60s for 450 stocks with 10 parallel workers.
Market regime and sector bleeding are derived from the downloaded stock data
(avoids needing separate index API calls that break on CI).
"""
import logging
import warnings
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta

import pandas as pd

warnings.filterwarnings("ignore")
logger = logging.getLogger(__name__)
logging.getLogger("jugaad_data").setLevel(logging.CRITICAL)
logging.getLogger("urllib3").setLevel(logging.ERROR)   # suppress connection pool noise

# ── Config ────────────────────────────────────────────────────────────────────
FETCH_MONTHS   = 13   # months of history (covers both weekly gate + daily signals)
MAX_WORKERS    = 20   # parallel workers
WORKER_SLEEP   = 0.02
PER_CALL_TIMEOUT = 12  # seconds — caps time on slow/non-existent symbols

# Yahoo Finance / universe symbol → NSE official trading symbol (where they differ)
SYMBOL_MAP: dict = {
    "INFOSYS": "INFY",   # NSE uses INFY, not INFOSYS
    # Add more here if needed: "YAHOO_SYM": "NSE_SYM"
}


# ── Internal helpers ──────────────────────────────────────────────────────────

def _to_ohlcv(raw: pd.DataFrame) -> pd.DataFrame:
    """
    Normalise jugaad-data output to standard OHLCV DataFrame
    with a clean DatetimeIndex.
    jugaad columns: DATE, SERIES, OPEN, HIGH, LOW, PREV. CLOSE, LTP, CLOSE, VOLUME …
    """
    if raw is None or raw.empty:
        return pd.DataFrame()

    df = raw.copy()

    if "DATE" in df.columns:
        df = df.set_index("DATE")

    df.index = pd.DatetimeIndex(df.index).normalize()
    df.index.name = "Date"
    df = df.sort_index()

    df = df.rename(columns={
        "OPEN": "Open", "HIGH": "High",
        "LOW": "Low",  "CLOSE": "Close",
        "VOLUME": "Volume",
    })

    cols = [c for c in ("Open", "High", "Low", "Close", "Volume") if c in df.columns]
    return df[cols].apply(pd.to_numeric, errors="coerce").dropna(how="all")


def _fetch_one(symbol: str, from_dt: date, to_dt: date) -> pd.DataFrame:
    """Fetch with PER_CALL_TIMEOUT so non-existent/slow symbols fail fast."""
    import concurrent.futures as cf
    from jugaad_data.nse import stock_df

    nse_sym = SYMBOL_MAP.get(symbol, symbol)
    time.sleep(WORKER_SLEEP)

    def _call():
        return stock_df(symbol=nse_sym, from_date=from_dt, to_date=to_dt, series="EQ")

    with cf.ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(_call)
        try:
            raw = fut.result(timeout=PER_CALL_TIMEOUT)
            return _to_ohlcv(raw)
        except cf.TimeoutError:
            logger.debug(f"{symbol}: timeout after {PER_CALL_TIMEOUT}s")
            return pd.DataFrame()
        except Exception as e:
            logger.debug(f"{symbol} ({nse_sym}): {e}")
            return pd.DataFrame()


# ── Public API ────────────────────────────────────────────────────────────────

def fetch_all_stocks(symbols: list) -> dict:
    """
    Download FETCH_MONTHS of daily OHLCV for every symbol via jugaad-data.
    Returns {symbol: DataFrame} with a clean DatetimeIndex.
    """
    today   = date.today()
    from_dt = today - timedelta(days=FETCH_MONTHS * 31)

    results: dict = {}
    done = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(_fetch_one, sym, from_dt, today): sym
                   for sym in symbols}
        for fut in as_completed(futures):
            sym = futures[fut]
            df  = fut.result()
            if not df.empty:
                results[sym] = df
            done += 1
            if done % 50 == 0:
                logger.info(f"  fetched {done}/{len(symbols)}, {len(results)} with data")

    logger.info(f"fetch_all_stocks: {len(results)}/{len(symbols)} symbols returned data")
    return results


def build_daily_weekly(raw: dict) -> tuple:
    """
    From the 13-month raw data, produce:
      daily_data  — last 7 months  {symbol: df}
      weekly_data — full 13 months resampled weekly  {symbol: df}
    Returns (daily_data, weekly_data).
    """
    daily_cut  = pd.Timestamp.now() - pd.DateOffset(months=7)
    weekly_cut = pd.Timestamp.now() - pd.DateOffset(months=FETCH_MONTHS)

    daily_data:  dict = {}
    weekly_data: dict = {}

    for sym, df in raw.items():
        if df.empty:
            continue

        # ── Weekly ──────────────────────────────────────────────────────────
        wdf = df[df.index >= weekly_cut]
        if len(wdf) >= 10:
            weekly = (
                wdf.resample("W-FRI")
                .agg(Open=("Open","first"), High=("High","max"),
                     Low=("Low","min"),   Close=("Close","last"),
                     Volume=("Volume","sum"))
                .dropna(how="all")
            )
            if len(weekly) >= 10:
                weekly_data[sym] = weekly

        # ── Daily ───────────────────────────────────────────────────────────
        ddf = df[df.index >= daily_cut]
        if len(ddf) >= 20:
            daily_data[sym] = ddf

    logger.info(f"build_daily_weekly: {len(daily_data)} daily, {len(weekly_data)} weekly")
    return daily_data, weekly_data


def derive_market_regime(daily_data: dict, nifty50_symbols: list) -> str:
    """
    Compute market regime from what fraction of Nifty 50 stocks are
    above their 50-day EMA.  No external API call needed.
    """
    from ta.trend import EMAIndicator

    in_universe = [s for s in nifty50_symbols if s in daily_data]
    if len(in_universe) < 15:
        return "Neutral"

    above_50 = 0
    counted  = 0

    for sym in in_universe:
        df = daily_data[sym]
        if len(df) < 52:
            continue
        close = df["Close"].squeeze()
        try:
            ema50 = EMAIndicator(close, window=50).ema_indicator().iloc[-1]
            if float(close.iloc[-1]) > float(ema50):
                above_50 += 1
            counted += 1
        except Exception:
            pass

    if counted == 0:
        return "Neutral"

    pct = above_50 / counted

    if pct >= 0.75:  return "Strong Bull"
    if pct >= 0.60:  return "Bull"
    if pct >= 0.45:  return "Weak Bull"
    if pct >= 0.30:  return "Neutral"
    if pct >= 0.15:  return "Weak Bear"
    return "Strong Bear"


def derive_sector_status(daily_data: dict, symbol_sector_map: dict) -> dict:
    """
    Compute each sector's average 1-day return from the downloaded stocks.
    Returns {sector: {'change_pct': float, 'bleeding': bool, 'ticker': str}}.
    """
    sector_returns: dict = {}

    for sym, df in daily_data.items():
        sector = symbol_sector_map.get(sym, "Unknown")
        if df is None or len(df) < 2:
            continue
        close = df["Close"].squeeze()
        prev  = float(close.iloc[-2])
        last  = float(close.iloc[-1])
        if prev <= 0:
            continue
        pct = (last - prev) / prev * 100
        sector_returns.setdefault(sector, []).append(pct)

    status: dict = {}
    for sector, returns in sector_returns.items():
        avg = sum(returns) / len(returns)
        status[sector] = {
            "change_pct": round(avg, 2),
            "bleeding":   avg < -1.5,
            "ticker":     f"sector:{sector}",
        }

    return status


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)

    test_syms = ["RELIANCE", "TCS", "HDFCBANK", "INFOSYS", "ICICIBANK",
                 "SBIN", "HINDUNILVR", "BHARTIARTL", "ITC", "KOTAKBANK"]
    print(f"Testing {len(test_syms)} symbols …")
    t0   = time.time()
    raw  = fetch_all_stocks(test_syms)
    d, w = build_daily_weekly(raw)
    print(f"Done in {time.time()-t0:.1f}s")
    for s in test_syms:
        nd = len(d[s]) if s in d else 0
        nw = len(w[s]) if s in w else 0
        print(f"  {s:15}: {nd} daily  {nw} weekly")
