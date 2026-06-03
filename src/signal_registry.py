"""
Signal deduplication registry. Tracks active signals and trade status.
Statuses: ACTIVE | T1_HIT_OPEN | COOLOFF | CLOSED

ACTIVE:      Signal live — monitoring for T1, SL, or expiry.
T1_HIT_OPEN: T1 hit, trade running — monitoring for T2 or expiry.
COOLOFF:     SL hit — blocked for 2 trading days then removed.
CLOSED:      Manually closed — removed on next cleanup.
"""
import logging
import uuid
from datetime import datetime, date, timedelta
from pathlib import Path
import pandas as pd
import pytz

logger = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")
DATA_DIR = Path(__file__).parent.parent / "data"
ACTIVE_SIGNALS_FILE = DATA_DIR / "active_signals" / "active_signals.csv"

PATTERN_EXPIRY_DAYS = {
    "20D_HIGH": 15, "W_PATTERN": 20, "BB_BREAKOUT": 12,
    "EMA_CROSS": 15, "RETEST_RECOVERY": 20, "SHORT_COVER": 8, "LONG_UNWIND": 3,
}
DEFAULT_EXPIRY_DAYS = 10

COLUMNS = [
    "signal_id", "symbol", "signal_type", "pattern", "conviction",
    "entry", "sl", "t1", "t2", "scan_date", "scan_time",
    "expiry_date", "status", "cooloff_until",
]

BLOCKED_STATUSES = {"ACTIVE", "T1_HIT_OPEN", "COOLOFF"}


def _load_registry() -> pd.DataFrame:
    if not ACTIVE_SIGNALS_FILE.exists():
        return pd.DataFrame(columns=COLUMNS)
    try:
        df = pd.read_csv(ACTIVE_SIGNALS_FILE)
        # Back-fill missing columns for backward compatibility
        for col in ["expiry_date", "cooloff_until"]:
            if col not in df.columns:
                df[col] = ""
        if "status" not in df.columns:
            df["status"] = "ACTIVE"
        return df
    except Exception as e:
        logger.warning(f"Could not load registry: {e}")
        return pd.DataFrame(columns=COLUMNS)


def _save_registry(df: pd.DataFrame):
    ACTIVE_SIGNALS_FILE.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(ACTIVE_SIGNALS_FILE, index=False)


def _add_trading_days(start: date, n: int) -> date:
    """Return date that is n trading days (Mon-Fri) after start."""
    d = start
    added = 0
    while added < n:
        d += timedelta(days=1)
        if d.weekday() < 5:
            added += 1
    return d


def cleanup_and_get_blocked(daily_data: dict = None, real_run: bool = True) -> set:
    """
    Process active_signals.csv:
    - ACTIVE: on T1 hit → T1_HIT_OPEN; on SL hit → COOLOFF 2 days; on expiry → remove
    - T1_HIT_OPEN: on T2/SL hit or expiry → remove
    - COOLOFF: when cooloff_until reached → remove
    - CLOSED / unknown → remove

    real_run=False: reads registry for blocked symbols but does NOT write any changes.
    Returns set of symbols that are still live (blocked from new signals).
    """
    df = _load_registry()
    if df.empty:
        return set()

    today_str = date.today().strftime("%Y-%m-%d")
    keep_rows = []
    blocked: set = set()
    changed = False

    for _, row in df.iterrows():
        row = row.to_dict()
        status = str(row.get("status", "ACTIVE"))
        symbol = str(row.get("symbol", ""))

        # Remove CLOSED or unknown statuses
        if status not in BLOCKED_STATUSES:
            changed = True
            continue

        # Get current price
        current_price = None
        if daily_data and symbol in daily_data:
            df_sym = daily_data[symbol]
            if df_sym is not None and not df_sym.empty:
                try:
                    current_price = float(df_sym["Close"].iloc[-1])
                except Exception:
                    pass

        entry = float(row.get("entry", 0) or 0)
        t1    = float(row.get("t1",    0) or 0)
        t2    = float(row.get("t2",    0) or 0)
        sl    = float(row.get("sl",    0) or 0)
        expiry     = str(row.get("expiry_date",  "") or "")
        cooloff_dt = str(row.get("cooloff_until", "") or "")

        # ── COOLOFF ──────────────────────────────────────────────────────────
        if status == "COOLOFF":
            if cooloff_dt and cooloff_dt <= today_str:
                logger.info(f"Registry: {symbol} cooloff expired ({cooloff_dt}) — removing")
                changed = True
                continue  # drop row
            blocked.add(symbol)
            keep_rows.append(row)
            continue

        # ── T1_HIT_OPEN ──────────────────────────────────────────────────────
        if status == "T1_HIT_OPEN":
            if expiry and expiry <= today_str:
                logger.info(f"Registry: {symbol} T1_HIT_OPEN expired ({expiry}) — closing")
                changed = True
                continue
            if current_price is not None:
                if t2 > 0 and current_price >= t2:
                    logger.info(f"Registry: {symbol} hit T2 {t2:.2f} (curr {current_price:.2f}) — closing")
                    changed = True
                    continue
                if sl > 0 and current_price <= sl:
                    logger.info(f"Registry: {symbol} SL hit while T1_HIT_OPEN — closing")
                    changed = True
                    continue
            blocked.add(symbol)
            keep_rows.append(row)
            continue

        # ── ACTIVE ───────────────────────────────────────────────────────────
        # Remove invalid SL (pre-fix legacy data)
        if sl > 0 and entry > 0 and sl >= entry:
            logger.info(f"Registry: {symbol} invalid SL (sl={sl:.2f} >= entry={entry:.2f}) — removing")
            changed = True
            continue

        # Expiry
        if expiry and expiry <= today_str:
            logger.info(f"Registry: {symbol} ACTIVE expired ({expiry}) — removing")
            changed = True
            continue

        if current_price is not None:
            # T1 hit → promote to T1_HIT_OPEN
            if t1 > 0 and current_price >= t1:
                logger.info(f"Registry: {symbol} hit T1 {t1:.2f} (curr {current_price:.2f}) → T1_HIT_OPEN")
                row["status"] = "T1_HIT_OPEN"
                changed = True
                blocked.add(symbol)
                keep_rows.append(row)
                continue
            # SL hit → COOLOFF for 2 trading days
            if sl > 0 and current_price <= sl:
                cooloff_end = _add_trading_days(date.today(), 2).strftime("%Y-%m-%d")
                logger.info(f"Registry: {symbol} hit SL {sl:.2f} (curr {current_price:.2f}) → COOLOFF until {cooloff_end}")
                row["status"] = "COOLOFF"
                row["cooloff_until"] = cooloff_end
                changed = True
                blocked.add(symbol)
                keep_rows.append(row)
                continue

        blocked.add(symbol)
        keep_rows.append(row)

    if changed and real_run:
        new_df = pd.DataFrame(keep_rows)
        if new_df.empty:
            new_df = pd.DataFrame(columns=COLUMNS)
        else:
            for col in COLUMNS:
                if col not in new_df.columns:
                    new_df[col] = ""
        _save_registry(new_df[COLUMNS])

    logger.info(f"Active registry: {len(blocked)} symbols blocked: {sorted(blocked)}")
    return blocked


def is_duplicate(symbol: str, signal_type: str, pattern: str,
                  check_date: date = None) -> bool:
    """Return True if this symbol+signal_type+pattern was already sent today."""
    df = _load_registry()
    if df.empty:
        return False
    today = check_date or date.today()
    today_str = today.strftime("%Y-%m-%d")
    match = df[
        (df["symbol"] == symbol) &
        (df["signal_type"] == signal_type) &
        (df["pattern"] == pattern) &
        (df["scan_date"] == today_str) &
        (df["status"].isin(BLOCKED_STATUSES))
    ]
    return not match.empty


def register_signal(signal: dict, plan: dict = None) -> str:
    """Register a new signal. Returns signal_id. Only call when real_run=True."""
    df = _load_registry()
    now_ist = datetime.now(IST)
    signal_id = str(uuid.uuid4())[:8].upper()
    pattern = signal.get("pattern", "")
    expiry_days = PATTERN_EXPIRY_DAYS.get(pattern, DEFAULT_EXPIRY_DAYS)
    expiry_date = (date.today() + timedelta(days=expiry_days)).strftime("%Y-%m-%d")

    entry = plan.get("entry_low", 0) if plan else 0
    sl    = plan.get("stop_recommended", 0) if plan else 0
    t1    = plan.get("t1", 0) if plan else 0
    t2    = plan.get("t2", 0) if plan else 0

    new_row = {
        "signal_id":    signal_id,
        "symbol":       signal.get("symbol", ""),
        "signal_type":  signal.get("signal_type", ""),
        "pattern":      pattern,
        "conviction":   signal.get("conviction", ""),
        "entry":        entry,
        "sl":           sl,
        "t1":           t1,
        "t2":           t2,
        "scan_date":    now_ist.strftime("%Y-%m-%d"),
        "scan_time":    now_ist.strftime("%H:%M"),
        "expiry_date":  expiry_date,
        "status":       "ACTIVE",
        "cooloff_until": "",
    }
    df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
    _save_registry(df)
    return signal_id


def dedup_signals(signals: list) -> list:
    """Filter out signals already sent today. Returns non-duplicate signals."""
    unique = []
    for sig in signals:
        sym     = sig.get("symbol", "")
        stype   = sig.get("signal_type", "")
        pattern = sig.get("pattern", "")
        if not is_duplicate(sym, stype, pattern):
            unique.append(sig)
        else:
            logger.debug(f"Dedup: {sym} {pattern} already in registry today")
    return unique


def clear_registry():
    """Clear all entries from active_signals.csv. Used by --clear-registry flag."""
    _save_registry(pd.DataFrame(columns=COLUMNS))
    logger.info("Registry cleared — active_signals.csv reset to empty")


def get_active_signals_today() -> list:
    """Return list of signal dicts active today."""
    df = _load_registry()
    if df.empty:
        return []
    today = date.today().strftime("%Y-%m-%d")
    today_df = df[(df["scan_date"] == today) & (df["status"].isin(BLOCKED_STATUSES))]
    return today_df.to_dict("records")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("Active today:", get_active_signals_today())
    blocked = cleanup_and_get_blocked()
    print("Blocked symbols:", blocked)
