"""Test jugaad-data — symbols, index, and speed."""
import time, warnings
warnings.filterwarnings("ignore")
from datetime import date
from jugaad_data.nse import stock_df

# 1. Symbol corrections: NSE official vs Yahoo Finance
test_symbols = {
    "RELIANCE": "RELIANCE",
    "INFOSYS":  "INFY",      # NSE uses INFY, not INFOSYS
    "BAJAJ-AUTO": "BAJAJ-AUTO",
    "M&M":      "M&M",
    "LT":       "LT",
    "HDFCBANK": "HDFCBANK",
}
print("=== Symbol tests ===")
from_dt = date(2026, 5, 1)
to_dt   = date(2026, 5, 31)
for yf_sym, nse_sym in test_symbols.items():
    try:
        df = stock_df(symbol=nse_sym, from_date=from_dt, to_date=to_dt, series="EQ")
        print(f"  OK  {yf_sym:15} -> {nse_sym}: {len(df)} bars")
    except Exception as e:
        print(f"  ERR {yf_sym:15} -> {nse_sym}: {e}")
    time.sleep(0.3)

# 2. Index data
print("\n=== Index tests ===")
try:
    from jugaad_data.nse import index_df
    for idx in ["NIFTY 50", "NIFTY BANK", "NIFTY IT"]:
        try:
            df = index_df(symbol=idx, from_date=date(2026,5,20), to_date=date(2026,5,31))
            print(f"  OK  {idx}: {len(df)} bars, cols={list(df.columns)[:5]}")
        except Exception as e:
            print(f"  ERR {idx}: {e}")
        time.sleep(0.3)
except ImportError as e:
    print(f"  index_df not available: {e}")
    # Try alternative
    try:
        from jugaad_data.nse import NSEHistory
        print("  Found NSEHistory")
    except:
        pass

# 3. Speed test — 20 symbols in parallel
print("\n=== Parallel speed test (20 symbols) ===")
from concurrent.futures import ThreadPoolExecutor, as_completed
syms_20 = ["RELIANCE","TCS","HDFCBANK","ICICIBANK","INFY","SBIN","HINDUNILVR",
           "BHARTIARTL","ITC","KOTAKBANK","LT","AXISBANK","HCLTECH","MARUTI",
           "WIPRO","ULTRACEMCO","BAJFINANCE","NESTLEIND","NTPC","POWERGRID"]

t0 = time.time()
results = {}
def fetch(sym):
    try:
        df = stock_df(symbol=sym, from_date=date(2026,4,1), to_date=date(2026,5,31), series="EQ")
        return sym, len(df) if df is not None else 0
    except:
        return sym, 0

with ThreadPoolExecutor(max_workers=10) as ex:
    futs = {ex.submit(fetch, s): s for s in syms_20}
    for f in as_completed(futs):
        sym, n = f.result()
        results[sym] = n

elapsed = time.time() - t0
ok = sum(1 for v in results.values() if v > 0)
print(f"  {ok}/{len(syms_20)} symbols, {elapsed:.1f}s total ({elapsed/len(syms_20):.2f}s/sym)")
print(f"  Failures: {[s for s,v in results.items() if v==0]}")
