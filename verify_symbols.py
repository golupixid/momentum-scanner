"""Quick check: which symbols in each universe file work in yfinance."""
import yfinance as yf
import pandas as pd
import time

def check_batch(symbols, label):
    tickers = [s + ".NS" for s in symbols]
    try:
        df = yf.download(" ".join(tickers), period="5d", interval="1d",
                         auto_adjust=True, progress=False)
        if isinstance(df.columns, pd.MultiIndex):
            got = set(df.columns.get_level_values(1))
        elif not df.empty:
            got = set(tickers)
        else:
            got = set()
        working = [t.replace(".NS", "") for t in tickers if t in got]
        missing = [t.replace(".NS", "") for t in tickers if t not in got]
        print(f"{label}: {len(working)}/{len(symbols)} working")
        if missing:
            print(f"  MISSING: {missing[:15]}")
        return working
    except Exception as e:
        print(f"{label}: ERROR {e}")
        return []

# Test Nifty 50 (should all work)
n50 = pd.read_csv("data/universe/nifty50.csv")
check_batch(n50["symbol"].tolist(), "Nifty 50")
time.sleep(2)

# Test first 30 of nifty150
n150 = pd.read_csv("data/universe/nifty150.csv")
check_batch(n150["symbol"].tolist()[:30], "Nifty 150 (first 30)")
time.sleep(2)

# Test first 30 of midcap150
mc = pd.read_csv("data/universe/midcap150.csv")
check_batch(mc["symbol"].tolist()[:30], "Midcap 150 (first 30)")
