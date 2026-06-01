"""
Quarterly Report (every 13th Sunday).
System performance | Pattern deep dive | Failure library
Proposed upgrades | Alpha vs Nifty | Conviction recalibration
"""
import logging
from datetime import datetime
from pathlib import Path
import pandas as pd
import pytz

logger = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")
DATA_DIR = Path(__file__).parent.parent / "data"


def is_quarterly_sunday() -> bool:
    """Returns True if today is the 13th Sunday (quarterly report day)."""
    now = datetime.now(IST)
    if now.weekday() != 6:  # Sunday = 6
        return False
    # Count Sundays since year start
    year_start = datetime(now.year, 1, 1, tzinfo=IST)
    days = (now - year_start).days
    sunday_count = sum(1 for d in range(days + 1)
                       if (year_start + __import__("datetime").timedelta(days=d)).weekday() == 6)
    return sunday_count % 13 == 0


def build_quarterly_report() -> list:
    """Build quarterly report messages."""
    from src.research_journal import _load_master, get_outcome_stats
    df = _load_master()
    stats = get_outcome_stats()
    now = datetime.now(IST)

    messages = []

    # Message 1: System performance
    lines = [
        f"*🎯 QUARTERLY REPORT — {now.strftime('%B %Y')}*",
        f"",
        f"*Section 1: System Performance*",
        f"",
    ]
    if df.empty:
        lines.append("Insufficient data for quarterly analysis.")
    else:
        total = len(df)
        resolved = df["outcome_label"].notna().sum()
        lines.extend([
            f"Total signals generated: {total}",
            f"Resolved signals: {resolved}",
            f"Resolution rate: {round(resolved/total*100,1) if total > 0 else 0}%",
        ])
        if stats:
            lines.append("")
            lines.append("*Win Rates vs Targets:*")
            for level, s in stats.items():
                lines.append(f"  {level}: {s['t1_rate']}% (target: 70-80% for HIGH)")
    messages.append("\n".join(lines))

    # Message 2: Pattern deep dive
    lines = ["*📊 Section 2: Pattern Deep Dive*", ""]
    if not df.empty and "outcome_label" in df.columns:
        resolved_df = df[df["outcome_label"].notna()]
        if not resolved_df.empty:
            for pattern in resolved_df["pattern"].unique():
                subset = resolved_df[resolved_df["pattern"] == pattern]
                t1_rate = (subset["outcome_label"] == "T1_HIT").sum() / len(subset) * 100
                sl_rate = (subset["outcome_label"] == "SL_HIT").sum() / len(subset) * 100
                lines.extend([
                    f"*{pattern}:*",
                    f"  T1 rate: {t1_rate:.1f}% | SL rate: {sl_rate:.1f}% | N={len(subset)}",
                ])
        else:
            lines.append("No resolved signals yet.")
    else:
        lines.append("No data available.")
    messages.append("\n".join(lines))

    # Message 3: Conviction recalibration
    lines = ["*🎯 Section 3: Conviction Recalibration*", ""]
    lines.extend([
        "Based on actual outcomes vs estimated probability bands:",
        "",
        "Current estimates (to be updated with real data):",
        "  HIGH: 70-80% → Update after 50+ signals",
        "  MODERATE: 55-65% → Update after 50+ signals",
        "  LOW: 40-50% → Update after 50+ signals",
        "",
        "Calibration runs quarterly. Next: in 13 weeks.",
    ])
    messages.append("\n".join(lines))

    # Message 4: Failure pattern library
    lines = ["*🔍 Section 4: Failure Pattern Library*", ""]
    if not df.empty and "failure_category" in df.columns:
        cats = df["failure_category"].value_counts()
        for cat, count in cats.head(5).items():
            lines.append(f"  🔴 {cat}: {count} occurrences")
    else:
        lines.append("Building failure library — needs 3 months of data.")
    messages.append("\n".join(lines))

    # Message 5: Proposed upgrades + Alpha vs Nifty
    lines = [
        "*🚀 Section 5: Proposed Upgrades + Alpha*",
        "",
        "Upgrade proposals (evidence-based after 3 months):",
        "  • Volume threshold: review 1.3x vs 1.5x impact",
        "  • W-Pattern tolerance: review by cap type",
        "  • Sector bleeding threshold: -1.5% vs -2.0%",
        "",
        "*Alpha vs Nifty:*",
        "  Nifty 50 3-month return: [fetch from yfinance]",
        "  Scanner HIGH signals return: [from journal outcomes]",
        "",
        "_Full alpha comparison available after 90+ days of tracking._",
    ]
    messages.append("\n".join(lines))

    return messages


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(f"Is quarterly Sunday: {is_quarterly_sunday()}")
    msgs = build_quarterly_report()
    for i, m in enumerate(msgs, 1):
        print(f"\n{'='*40}\nQUARTERLY MSG {i}\n{'='*40}")
        print(m)
