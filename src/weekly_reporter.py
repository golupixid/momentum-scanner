"""
Weekly Report — 8 messages sent Sunday 10 AM IST.
Msg 1: Opportunity summary
Msg 2: Outcome tracking
Msg 3: Pattern performance
Msg 4: Failure analysis
Msg 5: Conviction calibration
Msg 6: Sector analysis
Msg 7: 3-year backtest snapshot
Msg 8: Next week watchlist
"""
import logging
from datetime import datetime, timedelta
from pathlib import Path
import pandas as pd
import pytz

logger = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")
DATA_DIR = Path(__file__).parent.parent / "data"


def _load_journal() -> pd.DataFrame:
    from src.research_journal import _load_master
    return _load_master()


def msg1_opportunity_summary(df: pd.DataFrame, week_start: datetime) -> str:
    """Weekly opportunity summary: signals by conviction/type/sector."""
    week_str = week_start.strftime("%d %b")
    week_end = (week_start + timedelta(days=6)).strftime("%d %b %Y")

    if df.empty:
        return f"*📊 WEEK {week_str} - {week_end}: OPPORTUNITY SUMMARY*\n\nNo signals recorded this week."

    recent = df[pd.to_datetime(df["date"], errors="coerce") >= pd.Timestamp(week_start).tz_localize(None)]
    if recent.empty:
        return f"*📊 WEEK {week_str} - {week_end}*\n\nNo signals this week."

    total = len(recent)
    by_conviction = recent["conviction_level"].value_counts().to_dict()
    by_type = recent["signal_type"].value_counts().to_dict()
    by_sector = recent["sector"].value_counts().head(5).to_dict()

    lines = [
        f"*📊 MSG 1/8 — WEEK {week_str} - {week_end}: OPPORTUNITY SUMMARY*",
        f"",
        f"Total signals generated: {total}",
        f"",
        f"*By Conviction:*",
    ]
    for level in ["HIGH", "MODERATE", "LOW", "WATCHLIST"]:
        count = by_conviction.get(level, 0)
        lines.append(f"  🔥 {level}: {count}")

    lines.extend(["", "*By Signal Type:*"])
    for t, c in by_type.items():
        lines.append(f"  📌 {t}: {c}")

    lines.extend(["", "*Top Sectors:*"])
    for sector, count in list(by_sector.items())[:5]:
        lines.append(f"  📊 {sector}: {count} signals")

    return "\n".join(lines)


def msg2_outcome_tracking(df: pd.DataFrame) -> str:
    """Prior signals resolved — T1/T2/SL/expired rates."""
    resolved = df[df["outcome_label"].notna()] if not df.empty else pd.DataFrame()

    lines = ["*📈 MSG 2/8 — OUTCOME TRACKING*", ""]
    if resolved.empty:
        lines.append("No resolved signals yet. Outcomes updated after 5+ trading days.")
        return "\n".join(lines)

    outcome_counts = resolved["outcome_label"].value_counts().to_dict()
    total = len(resolved)

    lines.extend([
        f"Total resolved signals: {total}",
        "",
        "*Outcomes:*",
    ])
    outcome_labels = ["T1_HIT", "T2_HIT", "SL_HIT", "EXPIRED", "STRUCTURE_EXIT"]
    for label in outcome_labels:
        count = outcome_counts.get(label, 0)
        pct = round(count / total * 100, 1) if total > 0 else 0
        emoji = "🟢" if "T" in label else "🔴" if "SL" in label else "⚪"
        lines.append(f"  {emoji} {label}: {count} ({pct}%)")

    return "\n".join(lines)


def msg3_pattern_performance(df: pd.DataFrame) -> str:
    """Win rate by pattern, conviction, regime."""
    lines = ["*📊 MSG 3/8 — PATTERN PERFORMANCE*", ""]
    resolved = df[df["outcome_label"].notna()] if not df.empty else pd.DataFrame()

    if resolved.empty:
        lines.append("Insufficient data — need resolved signals for pattern analysis.")
        return "\n".join(lines)

    lines.append("*Win Rate by Pattern (T1 hit):*")
    patterns = resolved["pattern"].unique()
    for pattern in patterns:
        subset = resolved[resolved["pattern"] == pattern]
        t1 = (subset["outcome_label"] == "T1_HIT").sum()
        total = len(subset)
        rate = round(t1 / total * 100, 1) if total > 0 else 0
        lines.append(f"  📌 {pattern}: {rate}% ({t1}/{total})")

    lines.extend(["", "*Win Rate by Conviction:*"])
    for level in ["HIGH", "MODERATE", "LOW"]:
        subset = resolved[resolved["conviction_level"] == level]
        if subset.empty:
            continue
        t1 = (subset["outcome_label"] == "T1_HIT").sum()
        total = len(subset)
        rate = round(t1 / total * 100, 1) if total > 0 else 0
        lines.append(f"  🔥 {level}: {rate}% ({t1}/{total})")

    return "\n".join(lines)


def msg4_failure_analysis(df: pd.DataFrame) -> str:
    """Categorised failures + common factors + improvement suggestions."""
    lines = ["*🔍 MSG 4/8 — FAILURE ANALYSIS*", ""]
    failures = df[df["outcome_label"] == "SL_HIT"] if not df.empty else pd.DataFrame()

    if failures.empty:
        lines.append("No stop-loss hits to analyse yet.")
        return "\n".join(lines)

    categories = failures["failure_category"].value_counts().to_dict() if "failure_category" in failures.columns else {}

    cat_desc = {
        "False Breakout": "Volume faded after breakout | Near 52W high",
        "EMA Cross Fail": "Volume at minimum | RSI < 45 on cross",
        "W-Pattern Fail": "Lows unequal | Neckline at resistance",
        "Timing Failure": "In avoid window | Friday late | Pre-event",
        "Sector Drag": "Sector near 20D EMA | Deteriorated post-signal",
    }

    lines.append(f"Total SL hits: {len(failures)}")
    lines.append("")
    lines.append("*Failure Categories:*")

    for cat, count in categories.items():
        desc = cat_desc.get(cat, "")
        lines.append(f"  🔴 {cat}: {count} — {desc}")

    if not categories:
        lines.append("  No categorised failures yet — categories assigned Sunday.")

    return "\n".join(lines)


def msg5_conviction_calibration(df: pd.DataFrame) -> str:
    """Are HIGH signals actually outperforming?"""
    from src.research_journal import get_outcome_stats
    lines = ["*🎯 MSG 5/8 — CONVICTION CALIBRATION*", ""]

    stats = get_outcome_stats()
    if not stats:
        lines.append("Insufficient data. Calibration needs 20+ resolved signals per level.")
        lines.append("")
        lines.append("*Estimated Probability Bands:*")
        lines.append("  HIGH: 70-80% (starting estimate)")
        lines.append("  MODERATE: 55-65% (starting estimate)")
        lines.append("  LOW: 40-50% (starting estimate)")
        return "\n".join(lines)

    lines.append("*Actual Win Rates vs Estimates:*")
    for level in ["HIGH", "MODERATE", "LOW"]:
        s = stats.get(level, {})
        actual = s.get("t1_rate", 0)
        target = {"HIGH": "70-80", "MODERATE": "55-65", "LOW": "40-50"}.get(level, "?")
        status = "✅" if actual >= int(target.split("-")[0]) else "⚠️"
        lines.append(f"  {status} {level}: {actual}% actual vs {target}% target ({s.get('total',0)} signals)")

    return "\n".join(lines)


def msg6_sector_analysis(df: pd.DataFrame) -> str:
    """Best/worst sectors, rotation outcomes."""
    lines = ["*🏭 MSG 6/8 — SECTOR ANALYSIS*", ""]

    if df.empty:
        lines.append("No signals recorded yet.")
        return "\n".join(lines)

    sector_signals = df.groupby("sector").agg(
        count=("symbol", "count"),
        high_count=("conviction_level", lambda x: (x == "HIGH").sum()),
    ).sort_values("count", ascending=False)

    lines.append("*Signal Count by Sector:*")
    for sector, row in sector_signals.head(8).iterrows():
        lines.append(f"  📊 {sector}: {row['count']} signals ({row['high_count']} HIGH)")

    return "\n".join(lines)


def msg7_backtest_snapshot() -> str:
    """3-year backtest snapshot (static summary until backtester runs)."""
    backtest_file = DATA_DIR / "backtest" / "backtest_summary.csv"
    lines = ["*📚 MSG 7/8 — BACKTEST SNAPSHOT*", ""]

    if not backtest_file.exists() or backtest_file.stat().st_size < 10:
        lines.extend([
            "Backtest not yet run.",
            "",
            "Run `python -m src.backtester` to generate 3-year backtest.",
            "Results will appear here once available.",
        ])
        return "\n".join(lines)

    df = pd.read_csv(backtest_file)
    lines.append("*Top Patterns (3-year backtest):*")
    for _, row in df.head(5).iterrows():
        lines.append(f"  #{int(row.get('rank',0))} {row.get('setup','?')}: "
                     f"Win rate {row.get('win_1_5r',0):.0f}% | "
                     f"Freq {row.get('signals_per_year',0):.0f}/yr")

    return "\n".join(lines)


def msg8_next_week_watchlist(df: pd.DataFrame) -> str:
    """Stocks near key levels — building setups for next week."""
    lines = ["*👀 MSG 8/8 — NEXT WEEK WATCHLIST*", ""]
    lines.extend([
        "Stocks to watch next week (near key technical levels):",
        "",
        "_This section populated from Sunday pre-market scan._",
        "_Check Monday 8AM scan for confirmation._",
        "",
        "Run the 8AM Sunday scan for auto-populated watchlist.",
    ])
    return "\n".join(lines)


def build_weekly_report() -> list:
    """Build all 8 weekly report messages. Returns list of strings."""
    df = _load_journal()
    now = datetime.now(IST)
    week_start = now - timedelta(days=now.weekday() + 1)  # last Monday

    messages = [
        msg1_opportunity_summary(df, week_start),
        msg2_outcome_tracking(df),
        msg3_pattern_performance(df),
        msg4_failure_analysis(df),
        msg5_conviction_calibration(df),
        msg6_sector_analysis(df),
        msg7_backtest_snapshot(),
        msg8_next_week_watchlist(df),
    ]
    return messages


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    messages = build_weekly_report()
    for i, msg in enumerate(messages, 1):
        print(f"\n{'='*50}")
        print(f"MESSAGE {i}/8")
        print("=" * 50)
        print(msg)
