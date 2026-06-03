"""
Main entry point for NSE Momentum Scanner.
Called by GitHub Actions for each of the 5 daily scan times.

LOCAL TESTING:
  Create .env in project root with:
    TELEGRAM_BOT_TOKEN=your_token
    TELEGRAM_CHAT_ID=your_chat_id
  Run: python main.py
  → Full scan, sends to Telegram, does NOT write to registry.

  To write to registry (real signal tracking):
    python main.py --real-run

  To clear active_signals.csv:
    python main.py --clear-registry

FLAGS:
  --real-run        Write signals to active_signals.csv (GitHub Actions / real market runs)
  --dry-run         Skip Telegram send (silent test)
  --clear-registry  Wipe active_signals.csv and exit
  --time 8AM|...    Force a specific scan time slot
"""
import argparse
import csv
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
import pytz

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")


def _load_env_file():
    """Auto-load .env for local testing (never overrides existing env vars)."""
    env_file = Path(__file__).parent / ".env"
    if not env_file.exists():
        return
    loaded = 0
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            if os.environ.setdefault(k.strip(), v.strip()) == v.strip():
                loaded += 1
    if loaded:
        logger.info(f"Loaded {loaded} vars from .env (local test mode)")


HOLIDAYS_FILE = Path(__file__).parent / "data" / "holidays" / "nse_holidays.csv"


def _load_nse_holidays() -> set:
    if not HOLIDAYS_FILE.exists():
        return set()
    holidays = set()
    with open(HOLIDAYS_FILE, newline="") as f:
        for row in csv.DictReader(f):
            d = row.get("date", "").strip()
            if d:
                holidays.add(d)
    return holidays


def _is_nse_holiday(today: datetime = None) -> tuple:
    now_ist  = today or datetime.now(IST)
    today_str = now_ist.strftime("%Y-%m-%d")
    holidays  = _load_nse_holidays()
    if today_str in holidays:
        desc_map = {}
        if HOLIDAYS_FILE.exists():
            with open(HOLIDAYS_FILE, newline="") as f:
                for row in csv.DictReader(f):
                    desc_map[row.get("date","").strip()] = row.get("description","Holiday")
        return True, desc_map.get(today_str, "NSE Holiday")
    return False, ""


def is_8am_scan(scan_time: datetime) -> bool:
    return scan_time.hour == 8 and scan_time.minute < 30


def run_pre_market_scan(scan_time: datetime):
    """8 AM pre-market: global + GIFT + rotation (no trade signals)."""
    from src.global_markets import check_global_bleeding, get_global_summary
    from src.market_regime import get_market_regime, get_regime_emoji
    from src.sector_bleeding import get_all_sector_status
    from src.telegram_bot import send_message

    logger.info("Running 8AM pre-market scan")
    global_status = check_global_bleeding()
    sector_status = get_all_sector_status()
    market_regime = get_market_regime()
    regime_emoji  = get_regime_emoji(market_regime)

    lines = [
        f"PRE-MARKET PULSE | {scan_time.strftime('%d %b %Y %H:%M IST')}",
        f"",
        f"Market Regime: {regime_emoji} {market_regime}",
        f"Global: {get_global_summary(global_status)}",
        f"",
        f"Sector Status:",
    ]
    for sector, info in sorted(sector_status.items()):
        pct   = info.get("change_pct", 0)
        emoji = "DOWN" if info.get("bleeding") else ("FLAT" if pct < 0 else "UP")
        lines.append(f"  {emoji} {sector}: {pct:+.1f}%")
    if global_status.bleeding:
        lines.extend(["", "GLOBAL BLEEDING — Only FNO Long Unwinding signals in full scans."])
    lines.extend(["", "_Next scan: 10:00 AM IST_"])
    send_message("\n".join(lines))
    logger.info("Pre-market scan sent")


def run_full_scan(scan_time: datetime, real_run: bool = False):
    """Full scan: all signals, 5 Telegram messages."""
    from src.parallel_runner import full_scan_pipeline
    from src.telegram_bot import (send_messages, build_header_message,
                                   build_signal_group_message, build_footer_message)

    logger.info(f"Running full scan at {scan_time.strftime('%H:%M IST')} | real_run={real_run}")
    results = full_scan_pipeline(scan_time, real_run=real_run)

    msg1 = build_header_message(
        results["market_regime"],
        results["sector_status"],
        results["rotating_sectors"],
        results["headlines"],
        results["global_status"],
    )

    sym_info = results["symbol_info"]

    msg2 = build_signal_group_message(
        f"TOP 5 MOMENTUM BREAKOUT ({len(results['momentum'])} signals)",
        results["momentum"], results["plans"], results["news_data"], sym_info,
    )
    msg3 = build_signal_group_message(
        f"TOP 5 REVERSAL A+B ({len(results['reversal'])} signals)",
        results["reversal"], results["plans"], results["news_data"], sym_info,
    )
    msg4 = build_signal_group_message(
        f"TOP 5 FNO SIGNALS ({len(results['fno'])} signals)",
        results["fno"], results["plans"], results["news_data"], sym_info,
    )
    msg5 = build_footer_message(
        results["momentum_wl"],
        results["reversal_wl"],
    )

    send_messages([msg1, msg2, msg3, msg4, msg5])
    logger.info(
        f"Scan complete — {results['elapsed_seconds']}s | "
        f"MOM:{len(results['momentum'])} REV:{len(results['reversal'])} "
        f"FNO:{len(results['fno'])}"
    )


def main():
    _load_env_file()

    parser = argparse.ArgumentParser(description="NSE Momentum Scanner")
    parser.add_argument("--time", default="auto",
                        help="Scan time: 8AM|10AM|11:30AM|1PM|3PM|auto")
    parser.add_argument("--dry-run", action="store_true",
                        help="Run full scan but suppress Telegram messages")
    parser.add_argument("--real-run", action="store_true",
                        help="Write signals to registry (GitHub Actions / real market scans only)")
    parser.add_argument("--clear-registry", action="store_true",
                        help="Wipe active_signals.csv and exit")
    args = parser.parse_args()

    if args.clear_registry:
        from src.signal_registry import clear_registry
        clear_registry()
        logger.info("Registry cleared. Exiting.")
        sys.exit(0)

    scan_time = datetime.now(IST)
    logger.info(f"Scan triggered at {scan_time.strftime('%H:%M IST')} | "
                f"time={args.time} real_run={args.real_run}")

    is_holiday, holiday_desc = _is_nse_holiday(scan_time)
    if is_holiday:
        logger.info(f"NSE holiday ({holiday_desc}) — skipping scan")
        sys.exit(0)

    if args.dry_run:
        os.environ["TELEGRAM_BOT_TOKEN"] = ""
        os.environ["TELEGRAM_CHAT_ID"]   = ""

    try:
        if is_8am_scan(scan_time) or args.time == "8AM":
            run_pre_market_scan(scan_time)
        else:
            run_full_scan(scan_time, real_run=args.real_run)
    except Exception as e:
        logger.exception(f"Scan failed: {e}")
        from src.telegram_bot import send_message
        send_message(f"SCANNER ERROR\n{type(e).__name__}: {str(e)[:200]}")
        sys.exit(1)


if __name__ == "__main__":
    main()
