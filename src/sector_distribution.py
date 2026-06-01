"""
Layer 7 — Sector Distribution Cap.
Max 2 stocks from same sector per signal type per run.
3rd+ from same sector → Watchlist section (labelled 'sector overflow').
Rotation priority: rotating sectors ranked first within same conviction level.
"""
import logging
from collections import defaultdict

logger = logging.getLogger(__name__)

MAX_PER_SECTOR = 2
TOP_N = 5  # top signals to output per signal type


def apply_sector_cap(signals: list, symbol_sector_map: dict,
                      rotating_sectors: set = None) -> dict:
    """
    Cap signals by sector. Returns {'top': [...], 'overflow': [...]}.
    - top: up to MAX_PER_SECTOR per sector, then top TOP_N overall
    - overflow: signals beyond the sector cap (labelled as overflow)
    Rotating sectors ranked first within same conviction level.
    """
    rotating_sectors = rotating_sectors or set()

    # Sort: rotating sectors first, then by conviction level
    def sort_key(s):
        sector = symbol_sector_map.get(s.get("symbol", ""), "Unknown")
        rotating_boost = 0 if sector in rotating_sectors else 1
        conviction_order = {"HIGH": 0, "MODERATE": 1, "LOW": 2, "WATCHLIST": 3}
        conv = conviction_order.get(s.get("conviction", "WATCHLIST"), 3)
        rr = -s.get("rr", 0.0)  # negative for descending sort
        return (rotating_boost, conv, rr)

    sorted_signals = sorted(signals, key=sort_key)

    sector_count = defaultdict(int)
    top = []
    overflow = []

    for sig in sorted_signals:
        sym = sig.get("symbol", "")
        sector = symbol_sector_map.get(sym, "Unknown")
        if sector_count[sector] < MAX_PER_SECTOR:
            sector_count[sector] += 1
            top.append(sig)
        else:
            sig["overflow"] = True
            overflow.append(sig)

    # Final top-N
    top_5 = top[:TOP_N]
    remaining = top[TOP_N:] + overflow

    return {"top": top_5, "overflow": remaining}


def split_signals_by_type(all_signals: list) -> dict:
    """
    Split signals into momentum, reversal, and fno categories.
    Returns {'momentum': [...], 'reversal': [...], 'fno': [...]}
    """
    momentum, reversal, fno = [], [], []
    for sig in all_signals:
        sig_type = sig.get("signal_type", "momentum")
        if sig_type == "momentum":
            momentum.append(sig)
        elif sig_type == "reversal":
            reversal.append(sig)
        elif sig_type == "fno":
            fno.append(sig)
        else:
            momentum.append(sig)
    return {"momentum": momentum, "reversal": reversal, "fno": fno}


def get_watchlist_signals(signals: list, min_conviction: str = "LOW") -> list:
    """Return signals with conviction LOW or WATCHLIST for the footer section."""
    order = {"HIGH": 4, "MODERATE": 3, "LOW": 2, "WATCHLIST": 1}
    threshold = order.get(min_conviction, 2)
    return [s for s in signals if order.get(s.get("conviction", "WATCHLIST"), 0) <= threshold]


if __name__ == "__main__":
    # Quick test
    signals = [
        {"symbol": "A", "sector": "IT", "conviction": "HIGH", "rr": 2.5, "signal_type": "momentum"},
        {"symbol": "B", "sector": "IT", "conviction": "MODERATE", "rr": 1.8, "signal_type": "momentum"},
        {"symbol": "C", "sector": "IT", "conviction": "LOW", "rr": 1.5, "signal_type": "momentum"},
        {"symbol": "D", "sector": "Banking", "conviction": "HIGH", "rr": 2.0, "signal_type": "momentum"},
    ]
    sector_map = {"A": "IT", "B": "IT", "C": "IT", "D": "Banking"}
    result = apply_sector_cap(signals, sector_map)
    print("Top:", [s["symbol"] for s in result["top"]])
    print("Overflow:", [s["symbol"] for s in result["overflow"]])
