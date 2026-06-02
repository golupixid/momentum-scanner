"""
Telegram delivery. Sends 5 messages per scan run to the channel.
Uses python-telegram-bot v20 (async) with sync wrappers for simplicity.
"""
import asyncio
import logging
import os
from datetime import datetime

import pytz

logger = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

MAX_MSG_LEN = 4096
PARSE_MODE = "Markdown"


async def _send_async(token: str, chat_id: str, text: str, parse_mode: str = PARSE_MODE):
    """Send a single Telegram message. Falls back to plain text on parse error."""
    from telegram import Bot
    bot    = Bot(token=token)
    chunks = [text[i:i + MAX_MSG_LEN] for i in range(0, len(text), MAX_MSG_LEN)]
    for chunk in chunks:
        for pm in (parse_mode, None):   # retry without formatting if parse fails
            try:
                await bot.send_message(
                    chat_id=chat_id,
                    text=chunk,
                    parse_mode=pm,
                    disable_web_page_preview=True,
                )
                break
            except Exception as e:
                if pm is None:
                    logger.error(f"Telegram send failed: {e}")
                else:
                    logger.debug(f"Parse mode {pm} failed, retrying plain: {e}")


def send_message(text: str, token: str = None, chat_id: str = None):
    """Sync wrapper to send one Telegram message."""
    token = token or BOT_TOKEN
    chat_id = chat_id or CHAT_ID
    if not token or not chat_id:
        logger.warning("Telegram credentials not set. Message not sent.")
        logger.info(f"[TELEGRAM PREVIEW]\n{text}\n")
        return
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.ensure_future(_send_async(token, chat_id, text))
        else:
            loop.run_until_complete(_send_async(token, chat_id, text))
    except RuntimeError:
        asyncio.run(_send_async(token, chat_id, text))


def send_messages(texts: list, token: str = None, chat_id: str = None):
    """Send multiple messages sequentially."""
    for text in texts:
        send_message(text, token, chat_id)


# ── Signal card formatter ─────────────────────────────────────────────────────

def _cap_emoji(cap_type: str) -> str:
    return {"Large": "🔵", "Mid": "🟡", "Small": "🔴"}.get(cap_type, "⚪")


def _conviction_header(level: str) -> str:
    headers = {
        "HIGH": "🔥🔥🔥🔥 HIGH CONVICTION",
        "MODERATE": "🔥🔥🔥 MODERATE CONVICTION",
        "LOW": "🔥🔥 LOW CONVICTION",
        "WATCHLIST": "🔥 WATCHLIST",
    }
    return headers.get(level, level)


def _signal_type_line(signal: dict) -> str:
    stype = signal.get("signal_type", "")
    pattern = signal.get("pattern", "")
    setup = signal.get("setup", "")
    if stype == "momentum":
        return f"📈 Momentum Breakout — {pattern.replace('_', ' ')}"
    elif stype == "reversal":
        return f"🔄 Reversal Setup {setup} — {pattern.replace('_', ' ')}"
    elif stype == "fno":
        return f"📊 FNO Signal {setup} — {pattern.replace('_', ' ')}"
    return f"📌 {pattern}"


def _timeframe_validity(pattern: str) -> str:
    validity = {
        "20D_HIGH": "T1: 3-10d | T2: 5-15d | Max: 15d",
        "W_PATTERN": "T1: 5-15d | T2: 10-20d | Max: 20d",
        "BB_BREAKOUT": "T1: 2-7d | T2: 5-12d | Max: 12d",
        "EMA_CROSS": "T1: 3-10d | T2: 5-15d | Max: 15d",
        "RETEST_RECOVERY": "T1: 5-15d | T2: 10-20d | Max: 20d",
        "SHORT_COVER": "T1: 1-5d | T2: 3-8d | Max: 8d",
        "LONG_UNWIND": "T1: 1-3d | Max: 3d",
    }
    return validity.get(pattern, "T1: 3-10d | T2: 5-15d")


def format_signal_card(signal: dict, plan: dict = None, news: dict = None,
                        rank: int = 1) -> str:
    """
    Format a complete signal card for Telegram.
    Follows the spec card format exactly.
    """
    sym = signal.get("symbol", "")
    cap = signal.get("cap_type", "Large")
    sector = signal.get("sector", "Unknown")
    conviction = signal.get("conviction", "LOW")
    prob = signal.get("probability_band", "")
    close = signal.get("close", 0)
    vol_ratio = signal.get("vol_ratio", 0)
    oi_tag = signal.get("oi_tag", "")
    sector_rotating = signal.get("sector_rotating", False)

    cap_e = _cap_emoji(cap)
    alignment = "🌟 FULL ALIGNMENT" if conviction == "HIGH" else ""
    rotation = "🔄 SECTOR ROTATING UP" if sector_rotating else ""
    badge = alignment or rotation

    lines = [
        f"[{rank}/5] *{sym}* {cap_e}",
        f"{cap} | {sector} {badge}",
        f"",
        f"{_conviction_header(conviction)}",
        f"",
        f"{_signal_type_line(signal)}",
        f"⭕ Conditions: Q1={'✅' if signal.get('q1') else '❌'} "
        f"Q2={'✅' if signal.get('q2') else '❌'} "
        f"Q3={signal.get('q3', 'N/A')}",
        f"",
        f"💰 Price: ₹{close:.2f}",
        f"📊 Vol: {vol_ratio:.1f}x",
    ]

    if oi_tag:
        lines.append(f"📈 {oi_tag}")

    if plan and not plan.get("error"):
        entry_low = plan.get("entry_low", 0)
        entry_high = plan.get("entry_high", 0)
        sl = plan.get("stop_recommended", 0)
        t1 = plan.get("t1", 0)
        rr = plan.get("rr", 0)
        rr_rating = plan.get("rr_rating", {})
        timing = plan.get("timing", {})

        # T2 estimate: T1 + (T1 - entry) * 0.67
        t2 = round(t1 + (t1 - entry_low) * 0.67, 0) if t1 > entry_low > 0 else 0

        lines.extend([
            f"",
            f"🎯 Buy: ₹{entry_low:.0f}",
            f"🛑 SL: ₹{sl:.0f}",
            f"🎯 T1: ₹{t1:.0f} | T2: ₹{t2:.0f}",
            f"📊 Prob: {prob} | R:R {rr:.1f} {rr_rating.get('emoji', '')}",
            f"",
            f"⏱ EXECUTION: Entry ₹{entry_low:.0f}-{entry_high:.0f} | "
            f"Stop ₹{sl:.0f} | T1 ₹{t1:.0f} | R:R {rr:.1f}",
            f"⏰ Timing: {timing.get('label', 'N/A')} {timing.get('emoji', '')}",
        ])

    lines.append(f"📅 {_timeframe_validity(signal.get('pattern', ''))}")

    if news and news.get("items"):
        lines.append("")
        lines.append("📰 NEWS (last 30d):")
        for title, days_ago in news["items"][:2]:
            age = f"{days_ago}d ago" if days_ago else "today"
            emoji = "🔴" if news.get("negative") else "📰"
            lines.append(f"  {emoji} {title[:60]} ({age})")
    else:
        lines.append("📰 NEWS: Pure technical")

    return "\n".join(lines)


# ── 5-message scan formatter ──────────────────────────────────────────────────

def build_header_message(regime: str, sector_status: dict, rotating_sectors: list,
                           headlines: dict, global_status) -> str:
    """Message 1: Header with regime, sector pulse, news."""
    from src.market_regime import get_regime_emoji
    from src.global_markets import get_global_summary
    from src.news_scanner import format_headlines_for_telegram

    now = datetime.now(IST).strftime("%d %b %Y %H:%M IST")
    regime_e = get_regime_emoji(regime)
    global_txt = get_global_summary(global_status)

    lines = [
        f"🕐 *MOMENTUM SCANNER* | {now}",
        f"",
        f"🏛 *Market Regime:* {regime_e} {regime}",
        f"🌍 *Global:* {global_txt}",
        f"",
        f"📊 *SECTOR PULSE:*",
    ]

    for sector, info in sorted(sector_status.items()):
        pct = info.get("change_pct", 0)
        emoji = "🔴" if info.get("bleeding") else ("🟡" if pct < 0 else "🟢")
        lines.append(f"  {emoji} {sector}: {pct:+.1f}%")

    if rotating_sectors:
        lines.append(f"")
        lines.append(f"🔄 *ROTATING UP:* {', '.join(rotating_sectors)}")

    if headlines:
        lines.append("")
        if headlines.get("world"):
            lines.append("🌍 *World:*")
            for h in headlines["world"][:3]:
                lines.append(f"  • {h[:MAX_MSG_LEN//10]}")
        if headlines.get("india"):
            lines.append("🇮🇳 *India:*")
            for h in headlines["india"][:5]:
                lines.append(f"  • {h[:MAX_MSG_LEN//10]}")

    return "\n".join(lines)


def build_signal_group_message(title: str, signals: list, plans: dict,
                                news_data: dict, symbol_info: dict) -> str:
    """Messages 2/3/4: Signal groups (momentum, reversal, FNO)."""
    if not signals:
        return f"*{title}*\n\n_No signals this scan_"

    lines = [f"*{title}*", ""]
    for i, sig in enumerate(signals[:5], 1):
        sym = sig.get("symbol", "")
        plan = plans.get(sym)
        news = news_data.get(sym)
        info = symbol_info.get(sym, {})
        sig["sector"] = sig.get("sector") or info.get("sector", "Unknown")
        sig["cap_type"] = sig.get("cap_type") or info.get("cap_type", "Large")
        card = format_signal_card(sig, plan, news, rank=i)
        lines.append(card)
        lines.append("─" * 30)

    return "\n".join(lines)


def build_footer_message(watchlist: list, overflow: list, global_status,
                          symbol_sector_map: dict) -> str:
    """Message 5: Footer with LOW/WATCHLIST, emoji guide, global summary."""
    from src.global_markets import get_global_summary
    lines = [
        "*📋 FOOTER*",
        "",
        "*🔥 EMOJI GUIDE:* 🔥🔥🔥🔥 HIGH | 🔥🔥🔥 MODERATE | 🔥🔥 LOW | 🔥 WATCHLIST",
        f"*🔵* Large cap | *🟡* Mid cap | *🔴* Small cap",
        "",
    ]

    if watchlist:
        lines.append("*⚠️ LOW / WATCHLIST:* (DO NOT TRADE — monitor only)")
        for sig in watchlist[:10]:
            sym = sig.get("symbol", "")
            conv = sig.get("conviction", "WATCHLIST")
            pattern = sig.get("pattern", "")
            sector = symbol_sector_map.get(sym, "Unknown")
            lines.append(f"  🔥 {sym} | {sector} | {pattern} | {conv}")

    if overflow:
        lines.append("")
        lines.append("*🗂️ SECTOR OVERFLOW:* (sector cap exceeded)")
        for sig in overflow[:5]:
            sym = sig.get("symbol", "")
            sector = symbol_sector_map.get(sym, "Unknown")
            lines.append(f"  📌 {sym} | {sector} | {sig.get('pattern', '')}")

    lines.append("")
    lines.append(f"🌍 *GLOBAL:* {get_global_summary(global_status)}")
    lines.append("_Not SEBI advice. Personal research only. Use stop losses._")

    return "\n".join(lines)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    # Test card formatting (no live Telegram)
    sig = {
        "symbol": "RELIANCE", "cap_type": "Large", "sector": "Energy",
        "conviction": "HIGH", "probability_band": "70-80%",
        "close": 1350.0, "vol_ratio": 2.1, "signal_type": "momentum",
        "pattern": "20D_HIGH", "q1": True, "q2": False, "q3": "EXPANDING",
    }
    card = format_signal_card(sig, rank=1)
    print(card)
