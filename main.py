"""
Main entry point for Momentum Scanner scan runs.
Called by GitHub Actions for each of the 5 daily scan times.
Run: python main.py [--time 8AM|10AM|11:30AM|1PM|3PM]
"""
import argparse
import logging
import sys
from datetime import datetime
import pytz

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")


def is_8am_scan(scan_time: datetime) -> bool:
    return scan_time.hour == 8 and scan_time.minute < 30


def run_pre_market_scan(scan_time: datetime):
    """8 AM pre-market: global + GIFT + rotation + watchlist (no trade signals)."""
    from src.global_markets import check_global_bleeding, get_global_summary
    from src.market_regime import get_market_regime, get_regime_emoji
    from src.sector_bleeding import get_all_sector_status
    from src.telegram_bot import send_message

    logger.info("Running 8AM pre-market scan")

    global_status = check_global_bleeding()
    sector_status = get_all_sector_status()
    market_regime = get_market_regime()
    regime_emoji = get_regime_emoji(market_regime)

    lines = [
        f"🌅 *PRE-MARKET PULSE* | {scan_time.strftime('%d %b %Y %H:%M IST')}",
        f"",
        f"🏛 *Market Regime:* {regime_emoji} {market_regime}",
        f"🌍 *Global:* {get_global_summary(global_status)}",
        f"",
        f"📊 *Sector Status:*",
    ]

    for sector, info in sorted(sector_status.items()):
        pct = info.get("change_pct", 0)
        emoji = "🔴" if info.get("bleeding") else ("🟡" if pct < 0 else "🟢")
        lines.append(f"  {emoji} {sector}: {pct:+.1f}%")

    if global_status.bleeding:
        lines.extend([
            "",
            "⚠️ *GLOBAL BLEEDING DETECTED*",
            "Only FNO Long Unwinding signals will be generated in full scans.",
        ])

    lines.extend(["", "_Next scan: 10:00 AM IST_"])

    send_message("\n".join(lines))
    logger.info("Pre-market scan sent")


def run_full_scan(scan_time: datetime):
    """Full scan: all signals, 5 Telegram messages."""
    from src.parallel_runner import full_scan_pipeline
    from src.telegram_bot import (send_messages, build_header_message,
                                   build_signal_group_message, build_footer_message)

    logger.info(f"Running full scan at {scan_time.strftime('%H:%M IST')}")

    results = full_scan_pipeline(scan_time)

    msg1 = build_header_message(
        results["market_regime"],
        results["sector_status"],
        results["rotating_sectors"],
        results["headlines"],
        results["global_status"],
    )

    symbol_info = results["symbol_info"]

    msg2 = build_signal_group_message(
        f"📈 TOP 5 MOMENTUM BREAKOUT ({len(results['momentum'])} signals)",
        results["momentum"],
        results["plans"],
        results["news_data"],
        symbol_info,
    )

    msg3 = build_signal_group_message(
        f"🔄 TOP 5 REVERSAL A+B ({len(results['reversal'])} signals)",
        results["reversal"],
        results["plans"],
        results["news_data"],
        symbol_info,
    )

    msg4 = build_signal_group_message(
        f"📊 TOP 5 FNO SIGNALS ({len(results['fno'])} signals)",
        results["fno"],
        results["plans"],
        results["news_data"],
        symbol_info,
    )

    msg5 = build_footer_message(
        results["watchlist"],
        results["overflow"],
        results["global_status"],
        results["symbol_sector_map"],
    )

    send_messages([msg1, msg2, msg3, msg4, msg5])
    logger.info(f"Scan complete — {results['elapsed_seconds']}s | "
                f"MOM:{len(results['momentum'])} REV:{len(results['reversal'])} "
                f"FNO:{len(results['fno'])}")


def main():
    parser = argparse.ArgumentParser(description="NSE Momentum Scanner")
    parser.add_argument("--time", default="auto",
                        help="Scan time: 8AM|10AM|11:30AM|1PM|3PM|auto")
    parser.add_argument("--dry-run", action="store_true",
                        help="Run scan but don't send Telegram messages")
    args = parser.parse_args()

    scan_time = datetime.now(IST)
    logger.info(f"Scan triggered at {scan_time.strftime('%H:%M IST')} | mode={args.time}")

    if args.dry_run:
        import os
        os.environ["TELEGRAM_BOT_TOKEN"] = ""
        os.environ["TELEGRAM_CHAT_ID"] = ""

    try:
        if is_8am_scan(scan_time) or args.time == "8AM":
            run_pre_market_scan(scan_time)
        else:
            run_full_scan(scan_time)
    except Exception as e:
        logger.exception(f"Scan failed: {e}")
        from src.telegram_bot import send_message
        send_message(f"⚠️ *SCANNER ERROR*\n{type(e).__name__}: {str(e)[:200]}")
        sys.exit(1)


if __name__ == "__main__":
    main()
