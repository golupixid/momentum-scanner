"""
Data fetcher — calls Yahoo Finance v8 chart API directly via curl_cffi.

curl_cffi spoofs a Chrome TLS fingerprint, bypassing GitHub Actions
IP-based blocking. We do NOT use yfinance.download() because it routes
requests through its own internal mechanism that ignores the session
parameter for batch calls. Instead we call the v8/finance/chart endpoint
directly, parse the JSON, and build DataFrames ourselves.

Speed: ~1–2s per symbol.  With 20 parallel workers: ~450 symbols / 100s.
"""
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytz

logger = logging.getLogger(__name__)
logging.getLogger("urllib3").setLevel(logging.ERROR)

IST = pytz.timezone("Asia/Kolkata")

# ── Yahoo Finance API constants ───────────────────────────────────────────────
YF_BASE  = "https://query1.finance.yahoo.com/v8/finance/chart"
FALLBACK = "https://query2.finance.yahoo.com/v8/finance/chart"

INTERVAL_PERIODS = {
    "1wk": "1y",
    "1d":  "6mo",
    "1h":  "60d",
}

MAX_WORKERS = 20
PER_CALL_TIMEOUT = 15   # seconds per HTTP request

# ── curl_cffi session ─────────────────────────────────────────────────────────
_session = None


def _get_session():
    global _session
    if _session is not None:
        return _session
    try:
        from curl_cffi import requests as cr
        _session = cr.Session(impersonate="chrome")
        logger.info("curl_cffi Chrome session ready")
    except ImportError:
        import requests
        _session = requests.Session()
        _session.headers.update({
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36")
        })
        logger.warning("curl_cffi not available — falling back to plain requests")
    return _session


# ── Direct Yahoo Finance v8 fetcher ──────────────────────────────────────────

def _fetch_one_yf(symbol_ns: str, interval: str, period: str = None,
                  start_ts: int = None, end_ts: int = None) -> pd.DataFrame:
    """
    Fetch OHLCV for one Yahoo Finance ticker (e.g. 'RELIANCE.NS') by calling
    the v8/finance/chart endpoint directly via curl_cffi.
    Returns a DataFrame with DatetimeIndex and Open/High/Low/Close/Volume columns.
    """
    session = _get_session()
    params = {"interval": interval, "events": "history"}

    if start_ts and end_ts:
        params["period1"] = start_ts
        params["period2"] = end_ts
    else:
        params["range"] = period or INTERVAL_PERIODS.get(interval, "6mo")

    for base in (YF_BASE, FALLBACK):
        url = f"{base}/{symbol_ns}"
        try:
            r = session.get(url, params=params, timeout=PER_CALL_TIMEOUT)
            if r.status_code != 200:
                continue
            data   = r.json()
            result = data.get("chart", {}).get("result")
            if not result:
                return pd.DataFrame()
            chart  = result[0]
            ts     = chart.get("timestamp", [])
            if not ts:
                return pd.DataFrame()

            quote    = chart["indicators"]["quote"][0]
            adjclose = (chart["indicators"].get("adjclose", [{}])[0]
                        .get("adjclose", quote.get("close", [])))

            df = pd.DataFrame({
                "Open":   quote.get("open",   [None]*len(ts)),
                "High":   quote.get("high",   [None]*len(ts)),
                "Low":    quote.get("low",    [None]*len(ts)),
                "Close":  adjclose,
                "Volume": quote.get("volume", [None]*len(ts)),
            }, index=pd.to_datetime(ts, unit="s", utc=True).tz_convert(IST).normalize())
            df.index.name = "Date"
            return df.dropna(how="all").sort_index()

        except Exception as e:
            logger.debug(f"{symbol_ns} {interval}: {e}")

    return pd.DataFrame()


# ── Batch parallel fetcher ────────────────────────────────────────────────────

def _fetch_parallel(symbols: list, interval: str,
                    period: str = None,
                    start_ts: int = None, end_ts: int = None,
                    label: str = "") -> dict:
    """Fetch all symbols in parallel. Returns {symbol: DataFrame}."""
    results: dict = {}
    done    = 0

    def _worker(sym):
        ticker = sym + ".NS"
        return sym, _fetch_one_yf(ticker, interval, period, start_ts, end_ts)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(_worker, sym): sym for sym in symbols}
        for fut in as_completed(futures):
            sym, df = fut.result()
            if not df.empty:
                results[sym] = df
            done += 1
            if done % 100 == 0:
                logger.info(f"  {label} {done}/{len(symbols)}, {len(results)} with data")

    logger.info(f"{label} fetch: {len(results)}/{len(symbols)} symbols with data")
    return results


# ── Public API ────────────────────────────────────────────────────────────────

def fetch_all_weekly(symbols: list) -> dict:
    return _fetch_parallel(symbols, "1wk", period="1y", label="Weekly")


def fetch_all_daily(symbols: list) -> dict:
    return _fetch_parallel(symbols, "1d", period="6mo", label="Daily")


def fetch_all_hourly(symbols: list) -> dict:
    now   = datetime.now(timezone.utc)
    start = int((now - timedelta(days=60)).timestamp())
    end   = int(now.timestamp())
    return _fetch_parallel(symbols, "1h", start_ts=start, end_ts=end, label="Hourly")


def fetch_index_data(tickers: list) -> dict:
    """Fetch index tickers (e.g. '^NSEI', '^NSEBANK'). Returns {ticker: df}."""
    result: dict = {}
    for ticker in tickers:
        df = _fetch_one_yf(ticker, "1d", period="3mo")
        if not df.empty:
            result[ticker] = df
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
    if idx.tz is None:
        idx = idx.tz_localize("UTC").tz_convert(IST)
    else:
        idx = idx.tz_convert(IST)

    df       = df_hourly.copy()
    df.index = idx.normalize()
    dates    = sorted(set(df.index.strftime("%Y-%m-%d")))

    if now.hour == 8 and now.minute < 30:
        prev = dates[-2] if len(dates) >= 2 and dates[-1] == today_str else dates[-1]
        return df[df.index.strftime("%Y-%m-%d") == prev]

    today_df = df[df.index.strftime("%Y-%m-%d") == today_str]
    if len(today_df) >= 4:
        return today_df

    return df[df.index.strftime("%Y-%m-%d").isin(dates[-5:])]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    t0   = time.time()
    syms = ["RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "SBIN"]
    w    = fetch_all_weekly(syms)
    d    = fetch_all_daily(syms)
    for s in syms:
        print(f"  {s}: W={len(w.get(s,pd.DataFrame()))}  D={len(d.get(s,pd.DataFrame()))}")
    print(f"Done in {time.time()-t0:.1f}s")
