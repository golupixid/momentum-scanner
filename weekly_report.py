"""
Weekly report entry point — called by GitHub Actions on Sunday 10 AM IST.
Sends 8 messages. On every 13th Sunday, also sends quarterly report.
"""
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


def main():
    from src.weekly_reporter import build_weekly_report
    from src.quarterly_reporter import build_quarterly_report, is_quarterly_sunday
    from src.telegram_bot import send_messages
    from src.universe import load_universe
    from src.data_fetcher import fetch_all_weekly

    now = datetime.now(IST)
    logger.info(f"Weekly report starting at {now.strftime('%d %b %Y %H:%M IST')}")

    # Fetch weekly data for msg4 (weekly chart watchlist)
    logger.info("Fetching weekly data for weekly watchlist...")
    try:
        universe    = load_universe()
        # Only fetch Large + Mid cap stocks for weekly watchlist (faster)
        top_symbols = universe[universe["cap_type"].isin(["Large", "Mid"])]["symbol"].tolist()
        weekly_data = fetch_all_weekly(top_symbols)
        logger.info(f"Weekly data fetched: {len(weekly_data)} symbols")
    except Exception as e:
        logger.warning(f"Weekly data fetch failed: {e} — msg4 will show N/A")
        weekly_data = {}

    # Weekly report: 8 messages
    weekly_msgs = build_weekly_report(weekly_data=weekly_data)
    send_messages(weekly_msgs)
    logger.info(f"Sent {len(weekly_msgs)} weekly report messages")

    # Quarterly report every 13th Sunday
    if is_quarterly_sunday():
        logger.info("Quarterly Sunday detected — sending quarterly report")
        quarterly_msgs = build_quarterly_report()
        send_messages(quarterly_msgs)
        logger.info(f"Sent {len(quarterly_msgs)} quarterly report messages")


if __name__ == "__main__":
    main()
