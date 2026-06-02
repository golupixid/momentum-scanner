"""Test Stooq as NSE data source — no auth, no IP blocking."""
import requests, time
from datetime import date, timedelta
import pandas as pd, io

def stooq_fetch(symbol, days=180):
    end   = date.today()
    start = end - timedelta(days=days)
    # Stooq uses lowercase .ns for NSE stocks
    url = (f"https://stooq.com/q/d/l/"
           f"?s={symbol.lower()}.ns"
           f"&d1={start.strftime('%Y%m%d')}"
           f"&d2={end.strftime('%Y%m%d')}&i=d")
    try:
        r = requests.get(url, timeout=15,
                         headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200 or len(r.content) < 50:
            return None
        df = pd.read_csv(io.StringIO(r.text))
        if df.empty or "Date" not in df.columns:
            return None
        df["Date"] = pd.to_datetime(df["Date"])
        df = df.set_index("Date").sort_index()
        return df
    except Exception as e:
        return None

# Test 10 well-known NSE stocks
test_syms = ["RELIANCE","TCS","HDFCBANK","INFOSYS","ICICIBANK",
             "SBIN","HINDUNILVR","BHARTIARTL","ITC","KOTAKBANK"]

print("=== Stooq NSE stock test ===")
for sym in test_syms:
    t0 = time.time()
    df = stooq_fetch(sym)
    elapsed = time.time() - t0
    if df is not None and not df.empty:
        print(f"  OK  {sym}: {len(df)} bars  cols={list(df.columns)}  ({elapsed:.2f}s)")
    else:
        print(f"  ERR {sym}  ({elapsed:.2f}s)")

print("\n=== Stooq weekly test ===")
url = ("https://stooq.com/q/d/l/"
       f"?s=reliance.ns"
       f"&d1={(date.today()-timedelta(days=400)).strftime('%Y%m%d')}"
       f"&d2={date.today().strftime('%Y%m%d')}&i=w")
try:
    r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
    df = pd.read_csv(io.StringIO(r.text))
    print(f"  Weekly RELIANCE: {len(df)} bars  cols={list(df.columns)}")
except Exception as e:
    print(f"  ERR: {e}")

print("\n=== Parallel speed test (20 Nifty50 stocks) ===")
from concurrent.futures import ThreadPoolExecutor, as_completed
nifty20 = ["RELIANCE","TCS","HDFCBANK","ICICIBANK","INFY","SBIN","HINDUNILVR",
           "BHARTIARTL","ITC","KOTAKBANK","LT","AXISBANK","HCLTECH","MARUTI",
           "WIPRO","ULTRACEMCO","BAJFINANCE","NESTLEIND","NTPC","POWERGRID"]
t0 = time.time()
results = {}
with ThreadPoolExecutor(max_workers=10) as ex:
    futs = {ex.submit(stooq_fetch, s): s for s in nifty20}
    for f in as_completed(futs):
        sym = futs[f]
        df  = f.result()
        results[sym] = len(df) if df is not None else 0
elapsed = time.time() - t0
ok = sum(1 for v in results.values() if v > 0)
print(f"  {ok}/{len(nifty20)} symbols in {elapsed:.1f}s")
print(f"  Failures: {[s for s,v in results.items() if v==0]}")
