"""
Probe which data sources work from this machine.
Run this locally first, then push to GitHub Actions to verify CI compatibility.
"""
import requests, time, zipfile, io
from datetime import datetime, timedelta

def probe(name, fn):
    try:
        result = fn()
        print(f"  OK  {name}: {result}")
    except Exception as e:
        print(f"  ERR {name}: {e}")

print("=== NSE Archives (bhav copy) ===")
# Try last few trading days
for days_back in [1, 2, 3, 4, 5]:
    dt = datetime.now() - timedelta(days=days_back)
    if dt.weekday() >= 5:
        continue  # skip weekend
    yr  = dt.strftime("%Y")
    mon = dt.strftime("%b").upper()
    dd  = dt.strftime("%d")
    url = f"https://archives.nseindia.com/content/historical/EQUITIES/{yr}/{mon}/cm{dd}{mon}{yr}bhav.csv.zip"
    try:
        r = requests.get(url, timeout=15)
        if r.status_code == 200:
            z = zipfile.ZipFile(io.BytesIO(r.content))
            csv_name = z.namelist()[0]
            import pandas as pd
            df = pd.read_csv(z.open(csv_name))
            eq = df[df.get("SERIES", df.get(" SERIES", "")) == "EQ"]
            print(f"  OK  NSE bhav {dt.strftime('%Y-%m-%d')}: {len(eq)} EQ stocks, cols={list(df.columns[:5])}")
            break
        else:
            print(f"  404 NSE bhav {dt.strftime('%Y-%m-%d')}: {r.status_code}")
    except Exception as e:
        print(f"  ERR NSE bhav {dt.strftime('%Y-%m-%d')}: {e}")
    time.sleep(0.5)

print("\n=== Stooq (via pandas_datareader) ===")
try:
    import pandas_datareader as pdr
    df = pdr.get_data_stooq("RELIANCE.NS", start="2026-05-01", end="2026-06-01")
    print(f"  OK  Stooq RELIANCE.NS: {len(df)} bars, cols={list(df.columns)}")
except ImportError:
    print("  SKIP pandas_datareader not installed")
except Exception as e:
    print(f"  ERR Stooq: {e}")

print("\n=== jugaad-data ===")
try:
    from jugaad_data.nse import stock_df
    from datetime import date
    df = stock_df(symbol="RELIANCE", from_date=date(2026, 5, 1), to_date=date(2026, 5, 31), series="EQ")
    print(f"  OK  jugaad RELIANCE: {len(df)} bars")
except ImportError:
    print("  SKIP jugaad-data not installed")
except Exception as e:
    print(f"  ERR jugaad: {e}")

print("\n=== NSE newer archive (nsearchives) ===")
for days_back in [1, 2, 3, 4, 5]:
    dt = datetime.now() - timedelta(days=days_back)
    if dt.weekday() >= 5:
        continue
    date_str = dt.strftime("%d%b%Y").upper()
    url = f"https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{date_str}.csv"
    try:
        r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code == 200 and len(r.content) > 1000:
            print(f"  OK  nsearchives {dt.strftime('%Y-%m-%d')}: {len(r.content)//1024}KB")
            break
        else:
            print(f"  {r.status_code} nsearchives {dt.strftime('%Y-%m-%d')}")
    except Exception as e:
        print(f"  ERR nsearchives: {e}")
    time.sleep(0.5)
