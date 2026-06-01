"""
Sector bleeding: checks if a sector's index fell > 1.5% today.
Independently applied per sector, regardless of Nifty regime.
Exception: FNO Long Unwinding (C2) still generated even in bleeding sector.
"""
import logging
from pathlib import Path
import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
SECTOR_MAP_PATH = DATA_DIR / "sectors" / "sector_map.csv"
BLEEDING_THRESHOLD = -1.5  # percent

_sector_map_cache = None
_sector_status_cache = {}


def load_sector_map() -> pd.DataFrame:
    global _sector_map_cache
    if _sector_map_cache is not None:
        return _sector_map_cache
    df = pd.read_csv(SECTOR_MAP_PATH)
    df.columns = [c.strip().lower() for c in df.columns]
    _sector_map_cache = df
    return df


def _get_index_change_pct(ticker: str) -> float:
    """Fetch today's % change for a sector index. Returns 0.0 on failure."""
    try:
        df = yf.download(ticker, period="5d", interval="1d",
                         auto_adjust=True, progress=False)
        if df is None or df.empty or len(df) < 2:
            return 0.0
        closes = df["Close"].dropna()
        if len(closes) < 2:
            return 0.0
        prev = float(closes.iloc[-2])
        last = float(closes.iloc[-1])
        if prev <= 0:
            return 0.0
        return round((last - prev) / prev * 100, 2)
    except Exception as e:
        logger.debug(f"Sector index {ticker} fetch error: {e}")
        return 0.0


def get_all_sector_status(index_data: dict = None) -> dict:
    """
    Returns {sector_name: {'change_pct': float, 'bleeding': bool}}.
    If index_data is pre-fetched (from parallel runner), use it; else fetch.
    """
    global _sector_status_cache
    sector_map = load_sector_map()
    result = {}

    for _, row in sector_map.iterrows():
        sector = row["sector"]
        ticker = row["index_ticker"]

        if sector in result:
            continue  # same sector mapped multiple ways

        if index_data and ticker in index_data:
            df = index_data[ticker]
            if df is not None and len(df) >= 2:
                closes = df["Close"].dropna()
                prev = float(closes.iloc[-2]) if len(closes) >= 2 else 0
                last = float(closes.iloc[-1]) if len(closes) >= 1 else 0
                pct = round((last - prev) / prev * 100, 2) if prev > 0 else 0.0
            else:
                pct = 0.0
        else:
            pct = _get_index_change_pct(ticker)

        result[sector] = {
            "change_pct": pct,
            "bleeding": pct < BLEEDING_THRESHOLD,
            "ticker": ticker,
        }

    _sector_status_cache = result
    return result


def is_sector_bleeding(sector: str, sector_status: dict = None) -> bool:
    """Check if a specific sector is bleeding today."""
    if sector_status is None:
        sector_status = get_all_sector_status()
    info = sector_status.get(sector, {})
    return info.get("bleeding", False)


def get_bleeding_sectors(sector_status: dict = None) -> list:
    """Return list of sector names that are bleeding."""
    if sector_status is None:
        sector_status = get_all_sector_status()
    return [s for s, v in sector_status.items() if v.get("bleeding", False)]


def get_sector_status_text(sector_status: dict) -> str:
    """Format sector status for Telegram header."""
    lines = []
    for sector, info in sorted(sector_status.items()):
        pct = info["change_pct"]
        emoji = "🔴" if info["bleeding"] else ("🟡" if pct < 0 else "🟢")
        lines.append(f"{emoji} {sector}: {pct:+.1f}%")
    return "\n".join(lines)


def check_sector_rotation(sector: str, sector_status_prev_week: dict,
                          sector_status_current: dict) -> bool:
    """
    Detect if sector was below 20D EMA last week and now above.
    Approximated via: sector index was negative last week, positive now.
    """
    prev = sector_status_prev_week.get(sector, {}).get("change_pct", 0)
    curr = sector_status_current.get(sector, {}).get("change_pct", 0)
    return prev < 0 < curr


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    status = get_all_sector_status()
    for s, v in status.items():
        print(f"{s}: {v['change_pct']:+.1f}% bleeding={v['bleeding']}")
    print("\nBleeding sectors:", get_bleeding_sectors(status))
