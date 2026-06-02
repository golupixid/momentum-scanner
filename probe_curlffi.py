"""Test curl_cffi session + yfinance for NSE stocks."""
import yfinance as yf
from curl_cffi import requests as cr
import pandas as pd, time

session = cr.Session(impersonate="chrome")

print("=== curl_cffi + yfinance batch test ===")
tickers = ["RELIANCE.NS","TCS.NS","HDFCBANK.NS","ICICIBANK.NS","INFOSYS.NS",
           "SBIN.NS","HINDUNILVR.NS","BHARTIARTL.NS","ITC.NS","KOTAKBANK.NS"]

t0 = time.time()
df = yf.download(
    " ".join(tickers),
    period="1mo", interval="1d",
    auto_adjust=True, progress=False,
    session=session,
)
elapsed = time.time() - t0
print(f"  Time: {elapsed:.1f}s  Shape: {df.shape}")

if isinstance(df.columns, pd.MultiIndex):
    got = sorted(set(df.columns.get_level_values(1)))
    print(f"  Symbols with data ({len(got)}): {[t.replace('.NS','') for t in got]}")
elif not df.empty:
    print(f"  Got {len(df)} rows, columns: {list(df.columns)}")
else:
    print("  EMPTY — no data returned")

print("\n=== 6-month daily test (5 stocks) ===")
for ticker in tickers[:5]:
    t0 = time.time()
    df = yf.download(ticker, period="6mo", interval="1d",
                     auto_adjust=True, progress=False, session=session)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    print(f"  {ticker}: {len(df)} bars  ({time.time()-t0:.1f}s)")
    time.sleep(0.2)

print("\n=== Weekly test ===")
df = yf.download("RELIANCE.NS", period="1y", interval="1wk",
                  auto_adjust=True, progress=False, session=session)
if isinstance(df.columns, pd.MultiIndex):
    df.columns = df.columns.get_level_values(0)
print(f"  RELIANCE weekly: {len(df)} bars")
