"""
Nifty 50 market regime detection.
Regime based on Nifty 50 vs 50-day SMA and trend direction.
Returns: Strong Bull | Bull | Weak Bull | Neutral | Weak Bear | Strong Bear
"""
import logging
import pandas as pd
import numpy as np
import yfinance as yf

logger = logging.getLogger(__name__)

NIFTY_TICKER = "^NSEI"
REGIMES = ["Strong Bull", "Bull", "Weak Bull", "Neutral", "Weak Bear", "Strong Bear"]


def _classify_regime(df: pd.DataFrame) -> str:
    """
    Classify regime from Nifty daily OHLCV.
    Logic:
      - 50D SMA position: above/below
      - 200D SMA position: above/below
      - Recent momentum: last 5d direction
    """
    if df is None or df.empty or len(df) < 50:
        return "Neutral"

    # Flatten MultiIndex if present
    if isinstance(df.columns, pd.MultiIndex):
        df = df.droplevel(1, axis=1) if len(df.columns.get_level_values(0).unique()) > 1 else df
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

    close = df["Close"].dropna().squeeze()
    sma50 = float(close.rolling(50).mean().iloc[-1])
    last  = float(close.iloc[-1])

    sma200 = close.rolling(200).mean().iloc[-1] if len(close) >= 200 else None

    # 5-day change
    five_day_chg = (last - float(close.iloc[-5])) / float(close.iloc[-5]) * 100 if len(close) >= 5 else 0

    above_50 = last > sma50
    above_200 = (last > sma200) if sma200 is not None else above_50

    if above_50 and above_200 and five_day_chg > 1:
        return "Strong Bull"
    elif above_50 and above_200:
        return "Bull"
    elif above_50 and not above_200:
        return "Weak Bull"
    elif not above_50 and above_200:
        return "Neutral"
    elif not above_50 and not above_200 and five_day_chg > -2:
        return "Weak Bear"
    else:
        return "Strong Bear"


def fetch_nifty_data() -> pd.DataFrame:
    try:
        df = yf.download(NIFTY_TICKER, period="1y", interval="1d",
                         auto_adjust=True, progress=False)
        return df
    except Exception as e:
        logger.error(f"Failed to fetch Nifty data: {e}")
        return pd.DataFrame()


def get_market_regime(nifty_df: pd.DataFrame = None) -> str:
    if nifty_df is None or nifty_df.empty:
        nifty_df = fetch_nifty_data()
    return _classify_regime(nifty_df)


def is_bull_regime(regime: str) -> bool:
    return regime in ("Strong Bull", "Bull")


def is_bear_regime(regime: str) -> bool:
    return regime in ("Weak Bear", "Strong Bear")


def is_strong_bear(regime: str) -> bool:
    return regime == "Strong Bear"


def signal_allowed(regime: str, signal_type: str) -> bool:
    """
    Returns True if signal_type is permitted in the given regime.
    signal_type: 'momentum' | 'reversal_ab' | 'fno_c1' | 'fno_c2'
    """
    if signal_type == "momentum":
        return regime not in ("Weak Bear", "Strong Bear")
    elif signal_type == "reversal_ab":
        return regime != "Strong Bear"
    elif signal_type == "fno_c1":
        return True  # always allowed (warning in bull)
    elif signal_type == "fno_c2":
        return True  # always tracked; ACTIVE signal in bear
    return True


def get_regime_emoji(regime: str) -> str:
    emojis = {
        "Strong Bull": "🟢🟢",
        "Bull": "🟢",
        "Weak Bull": "🟡",
        "Neutral": "⚪",
        "Weak Bear": "🔴",
        "Strong Bear": "🔴🔴",
    }
    return emojis.get(regime, "⚪")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    df = fetch_nifty_data()
    regime = get_market_regime(df)
    print(f"Market regime: {regime} {get_regime_emoji(regime)}")
