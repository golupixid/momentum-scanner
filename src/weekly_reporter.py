"""
Weekly Report — 8 messages sent Sunday 10 AM IST.

New structure:
  Msg 1: Overall stats (T1/T2/SL/expired rates, success rate)
  Msg 2: Closed trades this week (SL hit, T2 hit, expired)
  Msg 3: Open trades (T1_HIT_OPEN first, then ACTIVE)
  Msg 4: Weekly chart watchlist (5 stocks near 52W high on weekly chart)
  Msg 5: Pattern performance
  Msg 6: Conviction calibration
  Msg 7: Sector analysis
  Msg 8: Backtest snapshot
"""
import logging
from datetime import datetime, timedelta, date
from pathlib import Path
import pandas as pd
import pytz

logger = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")
DATA_DIR = Path(__file__).parent.parent / "data"


def _load_journal() -> pd.DataFrame:
    from src.research_journal import _load_master
    return _load_master()


def _load_registry() -> pd.DataFrame:
    from src.signal_registry import _load_registry
    return _load_registry()


# ── MSG 1 — Overall stats ─────────────────────────────────────────────────────

def msg1_overall_stats(df: pd.DataFrame, week_start: datetime) -> str:
    week_str  = week_start.strftime("%d %b")
    week_end  = (week_start + timedelta(days=6)).strftime("%d %b %Y")
    header    = f"*MSG 1/8 — WEEK {week_str} - {week_end}: OVERALL STATS*"

    week_df = pd.DataFrame()
    if not df.empty:
        week_df = df[pd.to_datetime(df["date"], errors="coerce") >= pd.Timestamp(week_start).tz_localize(None)]

    total = len(week_df)
    lines = [header, "", f"Total signals this week: {total}", ""]

    if total == 0:
        lines.append("No signals recorded this week.")
        return "\n".join(lines)

    outcomes = week_df["outcome_label"].value_counts().to_dict() if "outcome_label" in week_df.columns else {}
    resolved = {k: v for k, v in outcomes.items() if k in ("T1_HIT", "T2_HIT", "SL_HIT", "EXPIRED")}
    total_resolved = sum(resolved.values())

    def pct(key):
        if total_resolved == 0:
            return 0.0
        return round(outcomes.get(key, 0) / total_resolved * 100, 1)

    t1_pct    = pct("T1_HIT")
    t2_pct    = pct("T2_HIT")
    sl_pct    = pct("SL_HIT")
    exp_pct   = pct("EXPIRED")
    success   = round(t1_pct + t2_pct, 1)

    lines += [
        f"*Outcome rates (of {total_resolved} resolved):*",
        f"  T1 hit:   {t1_pct}%",
        f"  T2 hit:   {t2_pct}%",
        f"  SL hit:   {sl_pct}%",
        f"  Expired:  {exp_pct}%",
        f"",
        f"*Success rate (T1+T2):* {success}%",
        f"",
    ]

    by_type = week_df["signal_type"].value_counts().to_dict() if "signal_type" in week_df.columns else {}
    if by_type:
        lines.append("*By type:*")
        for t, c in by_type.items():
            lines.append(f"  {t}: {c}")

    return "\n".join(lines)


# ── MSG 2 — Closed trades ─────────────────────────────────────────────────────

def msg2_closed_trades(df: pd.DataFrame, week_start: datetime) -> str:
    lines = ["*MSG 2/8 — CLOSED TRADES THIS WEEK*", ""]

    week_df = pd.DataFrame()
    if not df.empty:
        week_df = df[
            (pd.to_datetime(df["date"], errors="coerce") >= pd.Timestamp(week_start).tz_localize(None)) &
            (df["outcome_label"].isin(["SL_HIT", "T2_HIT", "EXPIRED"]))
        ] if "outcome_label" in df.columns else pd.DataFrame()

    if week_df.empty:
        lines.append("No closed trades this week.")
        return "\n".join(lines)

    for _, row in week_df.iterrows():
        sym     = row.get("symbol", "?")
        outcome = row.get("outcome_label", "?")
        entry   = row.get("entry_price", 0)
        exit_p  = row.get("exit_price", 0)
        days    = row.get("days_held", "?")
        if entry and exit_p and float(entry) > 0:
            pl_pct = round((float(exit_p) - float(entry)) / float(entry) * 100, 2)
            pl_str = f"{'+' if pl_pct >= 0 else ''}{pl_pct}%"
        else:
            pl_str = "N/A"
        outcome_label = {"SL_HIT": "SL HIT", "T2_HIT": "T2 HIT", "EXPIRED": "EXPIRED"}.get(outcome, outcome)
        lines.append(f"  {sym} | {outcome_label} | Entry {entry} | Exit {exit_p} | P&L {pl_str} | {days}d held")

    return "\n".join(lines)


# ── MSG 3 — Open trades ───────────────────────────────────────────────────────

def msg3_open_trades() -> str:
    lines = ["*MSG 3/8 — OPEN TRADES*", ""]
    reg = _load_registry()

    if reg.empty:
        lines.append("No open trades in registry.")
        return "\n".join(lines)

    today_str = date.today().strftime("%Y-%m-%d")

    t1_open = reg[reg["status"] == "T1_HIT_OPEN"].copy()
    active  = reg[reg["status"] == "ACTIVE"].copy()

    def days_held(scan_date_str):
        try:
            return (date.today() - date.fromisoformat(str(scan_date_str))).days
        except Exception:
            return "?"

    if not t1_open.empty:
        lines.append("*T1 HIT — Running toward T2:*")
        for _, row in t1_open.iterrows():
            sym   = row.get("symbol", "?")
            entry = row.get("entry", 0)
            t1    = row.get("t1", 0)
            t2    = row.get("t2", 0)
            d     = days_held(row.get("scan_date", ""))
            lines.append(f"  {sym} | Entry {entry:.0f} | T1 {t1:.0f} | T2 {t2:.0f} | {d}d held")
        lines.append("")

    if not active.empty:
        lines.append("*ACTIVE — Not yet at T1:*")
        for _, row in active.iterrows():
            sym   = row.get("symbol", "?")
            entry = row.get("entry", 0)
            t1    = row.get("t1", 0)
            t2    = row.get("t2", 0)
            d     = days_held(row.get("scan_date", ""))
            lines.append(f"  {sym} | Entry {entry:.0f} | T1 {t1:.0f} | T2 {t2:.0f} | {d}d held")

    if t1_open.empty and active.empty:
        lines.append("No open trades currently.")

    return "\n".join(lines)


# ── MSG 4 — Weekly chart watchlist ────────────────────────────────────────────

def msg4_weekly_watchlist(weekly_data: dict = None) -> str:
    lines = ["*MSG 4/8 — WEEKLY CHART WATCHLIST*", "",
             "Stocks near 52-week high breakout on weekly chart:", ""]

    if not weekly_data:
        lines.append("Weekly data not available — run with weekly_data parameter.")
        return "\n".join(lines)

    candidates = []
    for sym, df_w in weekly_data.items():
        if df_w is None or len(df_w) < 10:
            continue
        try:
            close   = float(df_w["Close"].iloc[-1])
            w52_hi  = float(df_w["High"].tail(52).max())
            if w52_hi <= 0:
                continue
            prox_pct = (close - w52_hi) / w52_hi * 100  # negative if below 52W high
            if -3.0 <= prox_pct <= 0.0:
                candidates.append({
                    "symbol":   sym,
                    "close":    round(close, 2),
                    "w52_high": round(w52_hi, 2),
                    "prox_pct": round(prox_pct, 2),
                })
        except Exception:
            continue

    candidates.sort(key=lambda x: x["prox_pct"], reverse=True)  # closest first

    if not candidates:
        lines.append("No stocks within -3% of 52-week high on weekly chart this week.")
        return "\n".join(lines)

    for item in candidates[:5]:
        sym    = item["symbol"]
        close  = item["close"]
        hi52   = item["w52_high"]
        prox   = item["prox_pct"]
        lines.append(f"  {sym} | 52W High: {hi52:.2f} | Now: {close:.2f} | {prox:.1f}%")

    return "\n".join(lines)


# ── MSG 5 — Pattern performance ───────────────────────────────────────────────

def msg5_pattern_performance(df: pd.DataFrame) -> str:
    lines = ["*MSG 5/8 — PATTERN PERFORMANCE*", ""]
    resolved = df[df["outcome_label"].notna()] if not df.empty else pd.DataFrame()

    if resolved.empty:
        lines.append("Insufficient data — need resolved signals for pattern analysis.")
        return "\n".join(lines)

    lines.append("*Win Rate by Pattern (T1 hit):*")
    for pattern in resolved["pattern"].unique():
        subset = resolved[resolved["pattern"] == pattern]
        t1     = (subset["outcome_label"] == "T1_HIT").sum()
        total  = len(subset)
        rate   = round(t1 / total * 100, 1) if total > 0 else 0
        lines.append(f"  {pattern}: {rate}% ({t1}/{total})")

    lines.extend(["", "*Win Rate by Conviction:*"])
    for level in ["HIGH", "MODERATE", "LOW"]:
        subset = resolved[resolved.get("conviction_level", pd.Series()) == level] if "conviction_level" in resolved.columns else pd.DataFrame()
        if subset.empty:
            continue
        t1    = (subset["outcome_label"] == "T1_HIT").sum()
        total = len(subset)
        rate  = round(t1 / total * 100, 1) if total > 0 else 0
        lines.append(f"  {level}: {rate}% ({t1}/{total})")

    return "\n".join(lines)


# ── MSG 6 — Conviction calibration ───────────────────────────────────────────

def msg6_conviction_calibration(df: pd.DataFrame) -> str:
    from src.research_journal import get_outcome_stats
    lines = ["*MSG 6/8 — CONVICTION CALIBRATION*", ""]
    stats = get_outcome_stats()
    if not stats:
        lines += [
            "Insufficient data (need 20+ resolved signals per level).",
            "", "*Estimated Probability Bands:*",
            "  HIGH: 70-80% (starting estimate)",
            "  MODERATE: 55-65% (starting estimate)",
            "  LOW: 40-50% (starting estimate)",
        ]
        return "\n".join(lines)

    lines.append("*Actual Win Rates vs Estimates:*")
    for level in ["HIGH", "MODERATE", "LOW"]:
        s      = stats.get(level, {})
        actual = s.get("t1_rate", 0)
        target = {"HIGH": "70-80", "MODERATE": "55-65", "LOW": "40-50"}.get(level, "?")
        status = "OK" if actual >= int(target.split("-")[0]) else "BELOW TARGET"
        lines.append(f"  {level}: {actual}% actual vs {target}% target ({s.get('total',0)} signals) [{status}]")

    return "\n".join(lines)


# ── MSG 7 — Sector analysis ───────────────────────────────────────────────────

def msg7_sector_analysis(df: pd.DataFrame) -> str:
    lines = ["*MSG 7/8 — SECTOR ANALYSIS*", ""]
    if df.empty:
        lines.append("No signals recorded yet.")
        return "\n".join(lines)

    sector_col = "sector" if "sector" in df.columns else None
    if sector_col is None:
        lines.append("Sector data not available.")
        return "\n".join(lines)

    sector_signals = df.groupby(sector_col).agg(count=("symbol", "count")).sort_values("count", ascending=False)
    lines.append("*Signal Count by Sector:*")
    for sector, row in sector_signals.head(8).iterrows():
        lines.append(f"  {sector}: {row['count']} signals")

    return "\n".join(lines)


# ── MSG 8 — Backtest snapshot ─────────────────────────────────────────────────

def msg8_backtest_snapshot() -> str:
    backtest_file = DATA_DIR / "backtest" / "backtest_summary.csv"
    lines = ["*MSG 8/8 — BACKTEST SNAPSHOT*", ""]

    if not backtest_file.exists() or backtest_file.stat().st_size < 10:
        lines += [
            "Backtest not yet run.",
            "",
            "Run `python -m src.backtester` to generate 3-year backtest.",
            "Results will appear here once available.",
        ]
        return "\n".join(lines)

    df = pd.read_csv(backtest_file)
    lines.append("*Top Patterns (3-year backtest):*")
    for _, row in df.head(5).iterrows():
        lines.append(f"  #{int(row.get('rank',0))} {row.get('setup','?')}: "
                     f"Win rate {row.get('win_1_5r',0):.0f}% | "
                     f"Freq {row.get('signals_per_year',0):.0f}/yr")

    return "\n".join(lines)


# ── Build all messages ────────────────────────────────────────────────────────

def build_weekly_report(weekly_data: dict = None) -> list:
    """Build all 8 weekly report messages. Returns list of strings."""
    df         = _load_journal()
    now        = datetime.now(IST)
    week_start = now - timedelta(days=now.weekday() + 1)  # last Monday

    messages = [
        msg1_overall_stats(df, week_start),
        msg2_closed_trades(df, week_start),
        msg3_open_trades(),
        msg4_weekly_watchlist(weekly_data),
        msg5_pattern_performance(df),
        msg6_conviction_calibration(df),
        msg7_sector_analysis(df),
        msg8_backtest_snapshot(),
    ]
    return messages


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    messages = build_weekly_report()
    for i, msg in enumerate(messages, 1):
        print(f"\n{'='*50}\nMESSAGE {i}/8\n{'='*50}")
        print(msg)
