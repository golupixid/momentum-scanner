"""
Local runner — run the scanner directly on your Windows machine.
Use this to test before setting up the self-hosted runner.

Usage:
    python run_local.py            # full scan
    python run_local.py premarket  # 8AM pre-market pulse
    python run_local.py weekly     # weekly report (8 messages)
    python run_local.py dry        # full scan, no Telegram
"""
import os
import sys
import logging
from datetime import datetime
import pytz

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
IST = pytz.timezone("Asia/Kolkata")

mode = sys.argv[1] if len(sys.argv) > 1 else "full"

if mode == "dry":
    os.environ["TELEGRAM_BOT_TOKEN"] = ""
    os.environ["TELEGRAM_CHAT_ID"]   = ""
    print("DRY RUN — Telegram disabled. Messages printed to console.\n")
else:
    # Credentials must be set as environment variables or in a .env file
    if not os.environ.get("TELEGRAM_BOT_TOKEN"):
        print("ERROR: Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID as env vars first.")
        print("  Windows: set TELEGRAM_BOT_TOKEN=your_token")
        print("           set TELEGRAM_CHAT_ID=-100yourchannelid")
        sys.exit(1)

scan_time = datetime.now(IST)
print(f"Scan time: {scan_time.strftime('%d %b %Y %H:%M IST')}")
print(f"Mode: {mode}\n")

if mode == "weekly":
    from src.weekly_reporter import build_weekly_report
    from src.telegram_bot import send_messages
    msgs = build_weekly_report()
    send_messages(msgs)
    print(f"Weekly report sent ({len(msgs)} messages)")

elif mode == "premarket":
    import main as m
    m.run_pre_market_scan(scan_time)

else:
    import main as m
    m.run_full_scan(scan_time)
