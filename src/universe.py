"""
Loads and manages the NSE stock universe (~450 stocks).
Priority: Large > Mid > Small. Deduplication across index files.
"""
import os
import pandas as pd
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
UNIVERSE_DIR = DATA_DIR / "universe"

INDEX_FILES = [
    ("nifty50.csv", "Large"),
    ("nifty150.csv", "Large"),
    ("midcap150.csv", "Mid"),
    ("smallcap250.csv", "Small"),
]

_universe_cache = None
_fno_cache = None


def load_universe(force_reload: bool = False) -> pd.DataFrame:
    global _universe_cache
    if _universe_cache is not None and not force_reload:
        return _universe_cache

    frames = []
    for filename, default_cap in INDEX_FILES:
        path = UNIVERSE_DIR / filename
        if not path.exists():
            continue
        df = pd.read_csv(path)
        df.columns = [c.strip().lower() for c in df.columns]
        if "cap_type" not in df.columns:
            df["cap_type"] = default_cap
        frames.append(df)

    if not frames:
        raise FileNotFoundError(f"No universe CSV files found in {UNIVERSE_DIR}")

    combined = pd.concat(frames, ignore_index=True)
    combined["symbol"] = combined["symbol"].str.strip().str.upper()

    # Deduplicate: keep first occurrence (Large > Mid > Small order preserved)
    combined = combined.drop_duplicates(subset="symbol", keep="first")
    combined = combined.reset_index(drop=True)

    # Ensure sector column exists
    if "sector" not in combined.columns:
        combined["sector"] = "Unknown"

    # Add yfinance ticker suffix
    combined["ticker"] = combined["symbol"] + ".NS"

    _universe_cache = combined
    return combined


def load_fno_stocks(force_reload: bool = False) -> set:
    global _fno_cache
    if _fno_cache is not None and not force_reload:
        return _fno_cache

    path = UNIVERSE_DIR / "fno_stocks.csv"
    if not path.exists():
        return set()

    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]
    _fno_cache = set(df["symbol"].str.strip().str.upper().tolist())
    return _fno_cache


def get_universe_symbols() -> list:
    return load_universe()["symbol"].tolist()


def get_ticker_map() -> dict:
    """Returns {symbol: ticker} e.g. {'RELIANCE': 'RELIANCE.NS'}"""
    df = load_universe()
    return dict(zip(df["symbol"], df["ticker"]))


def get_symbol_info(symbol: str) -> dict:
    df = load_universe()
    row = df[df["symbol"] == symbol.upper()]
    if row.empty:
        return {}
    return row.iloc[0].to_dict()


def get_universe_batches(batch_size: int = 50) -> list:
    """Split universe tickers into batches for yfinance bulk download."""
    tickers = load_universe()["ticker"].tolist()
    return [tickers[i:i + batch_size] for i in range(0, len(tickers), batch_size)]


if __name__ == "__main__":
    df = load_universe()
    print(f"Universe loaded: {len(df)} stocks")
    print(df["cap_type"].value_counts())
    fno = load_fno_stocks()
    print(f"FNO stocks: {len(fno)}")
    print(get_universe_batches()[0][:5])
