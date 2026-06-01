"""
Layer 9 — Research Journal. Records every signal with full context.
Pure signal outcome tracking — no fake capital or P&L.
Outcomes updated weekly on Sunday.
"""
import logging
import uuid
from datetime import datetime, date
from pathlib import Path
import pandas as pd
import pytz

logger = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")
DATA_DIR = Path(__file__).parent.parent / "data"
JOURNAL_DIR = DATA_DIR / "research_journal"
SIGNAL_MASTER = JOURNAL_DIR / "signal_master.csv"
OUTCOMES_FILE = JOURNAL_DIR / "outcomes.csv"

MASTER_COLS = [
    "signal_id", "date", "scan_time", "symbol", "sector", "cap_type",
    "signal_type", "pattern", "conviction_level", "probability_band",
    "entry_suggested", "sl", "t1", "t2", "rr_ratio",
    "market_regime", "sector_status", "sector_rotating", "news_flag",
    "global_status", "q1", "q2", "q3", "oi_pattern",
    "price_t1_hit", "price_t2_hit", "price_sl_hit",
    "max_gain_pct", "max_loss_pct", "price_5d", "price_10d", "price_20d",
    "outcome_label", "failure_category", "days_to_outcome",
]


def _load_master() -> pd.DataFrame:
    if not SIGNAL_MASTER.exists():
        return pd.DataFrame(columns=MASTER_COLS)
    return pd.read_csv(SIGNAL_MASTER)


def _save_master(df: pd.DataFrame):
    JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(SIGNAL_MASTER, index=False)


def record_signal(signal: dict, plan: dict, context: dict) -> str:
    """
    Record a signal to signal_master.csv.
    context: {market_regime, sector_status, sector_rotating, news_flag,
               global_status, q1, q2, q3, oi_pattern}
    Returns signal_id.
    """
    df = _load_master()
    now = datetime.now(IST)
    signal_id = str(uuid.uuid4())[:8].upper()

    entry = plan.get("entry_low", 0) if plan else signal.get("close", 0)
    sl = plan.get("stop_recommended", 0) if plan else 0
    t1 = plan.get("t1", 0) if plan else 0
    rr = plan.get("rr", 0) if plan else 0

    row = {
        "signal_id": signal_id,
        "date": now.strftime("%Y-%m-%d"),
        "scan_time": now.strftime("%H:%M"),
        "symbol": signal.get("symbol", ""),
        "sector": signal.get("sector", ""),
        "cap_type": signal.get("cap_type", ""),
        "signal_type": signal.get("signal_type", ""),
        "pattern": signal.get("pattern", ""),
        "conviction_level": signal.get("conviction", ""),
        "probability_band": signal.get("probability_band", ""),
        "entry_suggested": entry,
        "sl": sl,
        "t1": t1,
        "t2": 0,
        "rr_ratio": rr,
        "market_regime": context.get("market_regime", ""),
        "sector_status": context.get("sector_status", ""),
        "sector_rotating": context.get("sector_rotating", False),
        "news_flag": context.get("news_flag", ""),
        "global_status": context.get("global_status", ""),
        "q1": context.get("q1", ""),
        "q2": context.get("q2", ""),
        "q3": context.get("q3", ""),
        "oi_pattern": context.get("oi_pattern", ""),
        # Outcomes filled in later
        "price_t1_hit": None, "price_t2_hit": None, "price_sl_hit": None,
        "max_gain_pct": None, "max_loss_pct": None,
        "price_5d": None, "price_10d": None, "price_20d": None,
        "outcome_label": None, "failure_category": None, "days_to_outcome": None,
    }

    df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    _save_master(df)

    # Also append to weekly file
    week_num = now.isocalendar()[1]
    year = now.year
    weekly_file = JOURNAL_DIR / f"signals_{year}_{week_num:02d}.csv"
    weekly_df = pd.read_csv(weekly_file) if weekly_file.exists() else pd.DataFrame(columns=MASTER_COLS)
    weekly_df = pd.concat([weekly_df, pd.DataFrame([row])], ignore_index=True)
    weekly_df.to_csv(weekly_file, index=False)

    return signal_id


def update_outcomes(symbol: str, signal_date: str, price_5d: float = None,
                     price_10d: float = None, price_20d: float = None,
                     outcome_label: str = None, failure_category: str = None):
    """Update outcome columns for a signal (called on Sunday scan)."""
    df = _load_master()
    mask = (df["symbol"] == symbol) & (df["date"] == signal_date) & df["outcome_label"].isna()
    if mask.sum() == 0:
        return

    if price_5d is not None:
        df.loc[mask, "price_5d"] = price_5d
    if price_10d is not None:
        df.loc[mask, "price_10d"] = price_10d
    if price_20d is not None:
        df.loc[mask, "price_20d"] = price_20d
    if outcome_label:
        df.loc[mask, "outcome_label"] = outcome_label
    if failure_category:
        df.loc[mask, "failure_category"] = failure_category

    _save_master(df)


def get_signals_for_period(days_back: int = 7) -> pd.DataFrame:
    """Return signals from the last N days."""
    df = _load_master()
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    cutoff = pd.Timestamp.now() - pd.Timedelta(days=days_back)
    return df[df["date"] >= cutoff]


def get_outcome_stats() -> dict:
    """Compute win rate stats by conviction level and pattern."""
    df = _load_master()
    if df.empty or "outcome_label" not in df.columns:
        return {}

    resolved = df[df["outcome_label"].notna()]
    if resolved.empty:
        return {}

    stats = {}
    for level in ["HIGH", "MODERATE", "LOW"]:
        subset = resolved[resolved["conviction_level"] == level]
        if subset.empty:
            continue
        t1_hits = (subset["outcome_label"] == "T1_HIT").sum()
        sl_hits = (subset["outcome_label"] == "SL_HIT").sum()
        total = len(subset)
        stats[level] = {
            "total": total,
            "t1_rate": round(t1_hits / total * 100, 1) if total > 0 else 0,
            "sl_rate": round(sl_hits / total * 100, 1) if total > 0 else 0,
        }

    return stats


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    # Test record
    sig = {"symbol": "TEST", "signal_type": "momentum", "pattern": "20D_HIGH",
           "conviction": "HIGH", "probability_band": "70-80%",
           "sector": "IT", "cap_type": "Large", "close": 1000.0}
    ctx = {"market_regime": "Bull", "sector_status": "ABOVE", "q3": "EXPANDING"}
    sid = record_signal(sig, {}, ctx)
    print(f"Recorded: {sid}")
    print(get_signals_for_period(1))
