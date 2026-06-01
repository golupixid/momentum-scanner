"""
Layer 5 — Conviction Level Assignment.
Replaces 110-point fake scoring with binary/ternary logic.
Levels: HIGH (70-80%) | MODERATE (55-65%) | LOW (40-50%) | WATCHLIST (<40%)
"""
import logging
from src.indicators import get_q3_momentum

logger = logging.getLogger(__name__)

LEVELS = ["HIGH", "MODERATE", "LOW", "WATCHLIST"]
PROB_BANDS = {
    "HIGH": "70-80%",
    "MODERATE": "55-65%",
    "LOW": "40-50%",
    "WATCHLIST": "<40%",
}
EMOJIS = {
    "HIGH": "🔥🔥🔥🔥",
    "MODERATE": "🔥🔥🔥",
    "LOW": "🔥🔥",
    "WATCHLIST": "🔥",
}
LEVEL_ORDER = {"HIGH": 4, "MODERATE": 3, "LOW": 2, "WATCHLIST": 1}


def assign_conviction(gate_status: str, q1: bool, q2: bool, q3: str,
                       market_regime: str, sector_bleeding: bool,
                       is_strong_bear: bool) -> str:
    """
    Core conviction assignment.
    HIGH: weekly eligible + all 3 Q YES + Bull/Strong Bull + expanding 2+ bars
    MODERATE: 2 of 3 Q YES + Neutral or better + not bleeding + neutral/expanding
    LOW: 1 of 3 Q YES + not Strong Bear + not bleeding
    WATCHLIST: everything else that generated a signal
    """
    if gate_status == "EXCLUDED":
        return "WATCHLIST"

    q_count = sum([bool(q1), bool(q2), q3 in ("EXPANDING", "NEUTRAL")])
    is_bull = market_regime in ("Strong Bull", "Bull", "Weak Bull")
    is_neutral = market_regime == "Neutral"
    is_expanding = q3 == "EXPANDING"

    if (gate_status == "ELIGIBLE" and q1 and q2 and is_expanding
            and is_bull and not sector_bleeding):
        return "HIGH"

    if (q_count >= 2 and (is_bull or is_neutral)
            and not sector_bleeding and not is_strong_bear
            and gate_status in ("ELIGIBLE", "MARGINAL")):
        level = "MODERATE"
        if gate_status == "MARGINAL":
            return "MODERATE"  # CAP at MODERATE for marginal
        return level

    if (q_count >= 1 and not is_strong_bear and not sector_bleeding):
        return "LOW"

    return "WATCHLIST"


def apply_upgrades(level: str, upgrades: dict) -> str:
    """
    Apply conviction upgrade rules (max 1, no stacking, max HIGH).
    upgrades: {
      'sector_rotating': bool,
      'w_pattern_both': bool,
      'fno_long_buildup': bool,
      'strong_negative_news': bool,
    }
    """
    if level == "WATCHLIST":
        return level  # don't upgrade from WATCHLIST

    # Downgrade takes priority
    if upgrades.get("strong_negative_news"):
        return _downgrade(level)

    # Only one upgrade can apply
    if upgrades.get("sector_rotating") or upgrades.get("w_pattern_both") or upgrades.get("fno_long_buildup"):
        return _upgrade(level)

    return level


def _upgrade(level: str) -> str:
    idx = LEVELS.index(level)
    if idx > 0:
        return LEVELS[idx - 1]  # move up (lower index = higher conviction)
    return level  # already HIGH


def _downgrade(level: str) -> str:
    idx = LEVELS.index(level)
    if idx < len(LEVELS) - 1:
        return LEVELS[idx + 1]
    return level


def apply_marginal_cap(level: str, gate_status: str) -> str:
    """If weekly gate was MARGINAL, cap conviction at MODERATE."""
    if gate_status == "MARGINAL" and LEVEL_ORDER.get(level, 0) > LEVEL_ORDER["MODERATE"]:
        return "MODERATE"
    return level


def get_conviction_full(signal: dict, gate_status: str, market_regime: str,
                          sector_bleeding: bool, df_daily=None) -> dict:
    """
    Full conviction pipeline for a signal dict.
    Returns updated signal with conviction, probability, emoji.
    """
    q1 = signal.get("signal_type") == "momentum"
    q2 = signal.get("signal_type") == "reversal"
    q3 = get_q3_momentum(df_daily) if df_daily is not None else "NEUTRAL"

    is_strong_bear = market_regime == "Strong Bear"
    level = assign_conviction(gate_status, q1, q2, q3, market_regime,
                               sector_bleeding, is_strong_bear)

    upgrades = {
        "sector_rotating": signal.get("sector_rotating", False),
        "w_pattern_both": signal.get("w_both", False),
        "fno_long_buildup": signal.get("oi_pattern") == "LONG_BUILDUP",
        "strong_negative_news": signal.get("news_negative", False),
    }
    level = apply_upgrades(level, upgrades)
    level = apply_marginal_cap(level, gate_status)

    signal["conviction"] = level
    signal["probability_band"] = PROB_BANDS[level]
    signal["conviction_emoji"] = EMOJIS[level]
    signal["q1"] = q1
    signal["q2"] = q2
    signal["q3"] = q3

    return signal


def sort_by_conviction(signals: list) -> list:
    """Sort signals: HIGH first, then MODERATE, LOW, WATCHLIST."""
    return sorted(signals, key=lambda s: LEVEL_ORDER.get(s.get("conviction", "WATCHLIST"), 0), reverse=True)


if __name__ == "__main__":
    # Quick logic test
    level = assign_conviction("ELIGIBLE", True, True, "EXPANDING",
                               "Bull", False, False)
    print(f"Expected HIGH: {level}")

    level2 = assign_conviction("MARGINAL", True, False, "NEUTRAL",
                                "Neutral", False, False)
    print(f"Expected MODERATE: {level2}")

    level3 = assign_conviction("ELIGIBLE", False, False, "CONTRACTING",
                                "Strong Bear", True, True)
    print(f"Expected WATCHLIST: {level3}")
