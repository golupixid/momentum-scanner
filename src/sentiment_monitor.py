"""
Sentiment monitor: tracks sector bleeding changes and regime deterioration.
Generates exit recommendations when conditions deteriorate post-signal.
"""
import logging
from src.sector_bleeding import get_bleeding_sectors
from src.market_regime import get_market_regime, is_bear_regime

logger = logging.getLogger(__name__)

REGIME_DETERIORATION_LEVELS = 2


def check_sentiment_exit(signal: dict, current_regime: str,
                           sector_status: dict) -> dict:
    """
    Check if a held signal should exit due to sentiment change.
    SENTIMENT_EXIT if: sector bleeding OR regime dropped 2 levels vs signal time.
    """
    result = {"exit": False, "reason": "", "type": "SENTIMENT_EXIT"}

    symbol = signal.get("symbol", "")
    sector = signal.get("sector", "Unknown")
    original_regime = signal.get("market_regime", "Neutral")

    # Check sector bleeding
    bleeding_sectors = get_bleeding_sectors(sector_status)
    if sector in bleeding_sectors:
        result["exit"] = True
        result["reason"] = f"Sector {sector} now bleeding"
        return result

    # Check regime deterioration (simplified: if now bear and was bull)
    regime_order = {
        "Strong Bull": 6, "Bull": 5, "Weak Bull": 4,
        "Neutral": 3, "Weak Bear": 2, "Strong Bear": 1
    }
    orig_level = regime_order.get(original_regime, 3)
    curr_level = regime_order.get(current_regime, 3)
    if (orig_level - curr_level) >= REGIME_DETERIORATION_LEVELS:
        result["exit"] = True
        result["reason"] = f"Regime dropped: {original_regime} → {current_regime}"

    return result


def monitor_active_signals(active_signals: list, current_regime: str,
                             sector_status: dict, daily_data: dict) -> list:
    """
    Check all active signals for exit conditions.
    Returns list of exit recommendations.
    """
    from src.risk_manager import check_structure_break, check_tier3_urgent
    alerts = []

    for sig in active_signals:
        sym = sig.get("symbol", "")
        sig_type = sig.get("signal_type", "")
        df = daily_data.get(sym)

        # Sentiment exit
        sent = check_sentiment_exit(sig, current_regime, sector_status)
        if sent["exit"]:
            alerts.append({
                "symbol": sym, "exit_type": "SENTIMENT_EXIT",
                "reason": sent["reason"], "urgency": "normal"
            })
            continue

        # Structure break (momentum only)
        if sig_type == "momentum" and df is not None:
            if check_structure_break(df):
                alerts.append({
                    "symbol": sym, "exit_type": "STRUCTURE_EXIT",
                    "reason": "Close below 20 EMA", "urgency": "normal"
                })
                continue

        # Tier 3 urgent
        if df is not None and check_tier3_urgent(df):
            alerts.append({
                "symbol": sym, "exit_type": "TREND_EXIT",
                "reason": "Price < 20EMA + RSI falling + high volume", "urgency": "urgent"
            })

    return alerts


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sig = {"symbol": "RELIANCE", "sector": "Energy", "market_regime": "Bull"}
    sector_status = {"Energy": {"bleeding": False}}
    result = check_sentiment_exit(sig, "Strong Bear", sector_status)
    print("Sentiment exit:", result)
