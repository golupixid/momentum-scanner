"""
Technical indicators for Weekly, Daily, and Hourly data.
Uses 'ta' library + manual Supertrend implementation.
All calculations are lookahead-bias-free.
"""
import logging
import pandas as pd
import numpy as np
import ta
from ta.trend import EMAIndicator, MACD
from ta.momentum import RSIIndicator
from ta.volatility import BollingerBands, AverageTrueRange

logger = logging.getLogger(__name__)

EMA_SHORT = 9
EMA_MID = 16
EMA_LONG = 20
EMA_50 = 50
BB_LENGTH = 20
BB_STD = 2.0
RSI_LENGTH = 14
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
SUPERTREND_LENGTH = 10
SUPERTREND_MULT = 3.0
ATR_LENGTH = 14
WEEKLY_EMA = 20


def _supertrend(high: pd.Series, low: pd.Series, close: pd.Series,
                length: int = 10, multiplier: float = 3.0) -> pd.DataFrame:
    """Manual Supertrend implementation."""
    atr = AverageTrueRange(high, low, close, window=length).average_true_range()
    hl_avg = (high + low) / 2
    upper_band = hl_avg + multiplier * atr
    lower_band = hl_avg - multiplier * atr

    supertrend = pd.Series(np.nan, index=close.index)
    direction = pd.Series(np.nan, index=close.index)

    in_uptrend = True

    for i in range(1, len(close)):
        curr_close = close.iloc[i]
        prev_close = close.iloc[i - 1]

        curr_upper = upper_band.iloc[i]
        curr_lower = lower_band.iloc[i]

        prev_upper = upper_band.iloc[i - 1]
        prev_lower = lower_band.iloc[i - 1]

        # Adjust bands
        curr_upper = min(curr_upper, prev_upper) if prev_close <= prev_upper else curr_upper
        curr_lower = max(curr_lower, prev_lower) if prev_close >= prev_lower else curr_lower

        if in_uptrend:
            if curr_close < curr_lower:
                in_uptrend = False
        else:
            if curr_close > curr_upper:
                in_uptrend = True

        supertrend.iloc[i] = curr_lower if in_uptrend else curr_upper
        direction.iloc[i] = 1 if in_uptrend else -1

    return pd.DataFrame({"supertrend": supertrend, "supertrend_dir": direction})


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    """Flatten MultiIndex columns from yfinance single-ticker downloads."""
    if isinstance(df.columns, pd.MultiIndex):
        df = df.copy()
        df.columns = df.columns.get_level_values(0)
    return df


def add_weekly_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Indicators for weekly structural gate."""
    df = _normalize(df).copy()
    if df.empty or len(df) < WEEKLY_EMA:
        return df
    close = df["Close"].squeeze()
    df[f"ema{WEEKLY_EMA}w"] = EMAIndicator(close, window=WEEKLY_EMA).ema_indicator()
    df["rsi_w"] = RSIIndicator(close, window=RSI_LENGTH).rsi()
    df["above_20w_ema"] = df["Close"] > df[f"ema{WEEKLY_EMA}w"]
    df["pct_above_20w"] = (df["Close"] - df[f"ema{WEEKLY_EMA}w"]) / df[f"ema{WEEKLY_EMA}w"] * 100
    return df


def add_daily_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """All daily indicators: EMAs, BB, MACD, RSI, Supertrend, ATR, Volume."""
    df = _normalize(df).copy()
    if df.empty or len(df) < EMA_LONG:
        return df

    close = df["Close"].squeeze()
    high = df["High"].squeeze()
    low = df["Low"].squeeze()
    volume = df["Volume"].squeeze()

    df[f"ema{EMA_SHORT}"] = EMAIndicator(close, window=EMA_SHORT).ema_indicator()
    df[f"ema{EMA_MID}"] = EMAIndicator(close, window=EMA_MID).ema_indicator()
    df[f"ema{EMA_LONG}"] = EMAIndicator(close, window=EMA_LONG).ema_indicator()
    df[f"ema{EMA_50}"] = EMAIndicator(close, window=EMA_50).ema_indicator()

    # Bollinger Bands
    bb = BollingerBands(close, window=BB_LENGTH, window_dev=BB_STD)
    df["bb_upper"] = bb.bollinger_hband()
    df["bb_mid"] = bb.bollinger_mavg()
    df["bb_lower"] = bb.bollinger_lband()

    # RSI
    df["rsi"] = RSIIndicator(close, window=RSI_LENGTH).rsi()

    # MACD
    macd_ind = MACD(close, window_fast=MACD_FAST, window_slow=MACD_SLOW, window_sign=MACD_SIGNAL)
    df["macd"] = macd_ind.macd()
    df["macd_signal"] = macd_ind.macd_signal()
    df["macd_hist"] = macd_ind.macd_diff()

    # Supertrend
    if len(df) >= SUPERTREND_LENGTH + 1:
        st = _supertrend(high, low, close, SUPERTREND_LENGTH, SUPERTREND_MULT)
        df["supertrend"] = st["supertrend"].values
        df["supertrend_dir"] = st["supertrend_dir"].values

    # ATR
    df["atr"] = AverageTrueRange(high, low, close, window=ATR_LENGTH).average_true_range()

    # Volume
    df["vol_avg_20"] = volume.rolling(20).mean()
    df["vol_ratio"] = volume / df["vol_avg_20"]

    # 20D High (shift 1 to avoid lookahead bias)
    df["high_20d"] = high.rolling(20).max().shift(1)

    return df


def add_hourly_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Hourly indicators for execution planning."""
    df = _normalize(df).copy()
    if df.empty or len(df) < 8:
        return df

    close = df["Close"].squeeze()
    high = df["High"].squeeze()
    low = df["Low"].squeeze()

    df["ema8h"] = EMAIndicator(close, window=8).ema_indicator()
    df["ema20h"] = EMAIndicator(close, window=EMA_LONG).ema_indicator()
    df["atr_h"] = AverageTrueRange(high, low, close, window=ATR_LENGTH).average_true_range()
    df["vol_avg_h"] = df["Volume"].rolling(10).mean()

    return df


def is_supertrend_bullish(df: pd.DataFrame) -> bool:
    if "supertrend_dir" not in df.columns:
        return False
    val = df["supertrend_dir"].dropna()
    if val.empty:
        return False
    return int(val.iloc[-1]) == 1


def is_volume_sufficient(df: pd.DataFrame, multiplier: float = 1.3) -> bool:
    if "vol_ratio" not in df.columns:
        return False
    val = df["vol_ratio"].dropna()
    if val.empty:
        return False
    return float(val.iloc[-1]) >= multiplier


def is_rsi_rising(df: pd.DataFrame, bars: int = 2) -> bool:
    if "rsi" not in df.columns:
        return False
    rsi = df["rsi"].dropna()
    if len(rsi) < bars + 1:
        return False
    for i in range(1, bars + 1):
        if rsi.iloc[-i] <= rsi.iloc[-(i + 1)]:
            return False
    return True


def is_macd_hist_growing(df: pd.DataFrame) -> bool:
    if "macd_hist" not in df.columns:
        return False
    hist = df["macd_hist"].dropna()
    if len(hist) < 2:
        return False
    return float(hist.iloc[-1]) > float(hist.iloc[-2])


def get_q3_momentum(df: pd.DataFrame) -> str:
    """
    Q3: EXPANDING | NEUTRAL | CONTRACTING
    EXPANDING: RSI rising 2+ bars + MACD hist growing + Vol above avg
    NEUTRAL: RSI rising 1 bar OR vol at average
    CONTRACTING: otherwise
    """
    rsi_2 = is_rsi_rising(df, bars=2)
    rsi_1 = is_rsi_rising(df, bars=1)
    macd_grow = is_macd_hist_growing(df)
    vol_ok = is_volume_sufficient(df, 1.0)

    if rsi_2 and macd_grow and vol_ok:
        return "EXPANDING"
    if rsi_1 or vol_ok:
        return "NEUTRAL"
    return "CONTRACTING"


if __name__ == "__main__":
    import yfinance as yf
    logging.basicConfig(level=logging.INFO)
    df = yf.download("RELIANCE.NS", period="6mo", interval="1d",
                     auto_adjust=True, progress=False)
    # Flatten multi-level columns from single-ticker yfinance download
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = add_daily_indicators(df)
    cols = [c for c in ["Close", "ema9", "ema16", "ema20", "rsi",
                         "supertrend_dir", "vol_ratio"] if c in df.columns]
    print(df.tail(3)[cols].to_string())
    print("Q3:", get_q3_momentum(df))
    print("Supertrend bullish:", is_supertrend_bullish(df))
