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

_CONVICTION_ORDER = {"HIGH": 4, "MODERATE": 3, "LOW": 2, "WATCHLIST": 1}


def dedup_signals_within_type(signals: list) -> list:
    """
    Fix 3: Within each signal_type category, keep only the highest conviction
    signal per symbol. If two signals share symbol AND signal_type, only the
    best one survives (higher conviction wins; tie-break: higher vol_ratio).
    """
    seen = {}  # (symbol, signal_type) → best signal so far
    for sig in signals:
        key = (sig.get("symbol", ""), sig.get("signal_type", ""))
        existing = seen.get(key)
        if existing is None:
            seen[key] = sig
        else:
            curr_level = _CONVICTION_ORDER.get(sig.get("conviction", "WATCHLIST"), 0)
            best_level = _CONVICTION_ORDER.get(existing.get("conviction", "WATCHLIST"), 0)
            if curr_level > best_level:
                seen[key] = sig
            elif curr_level == best_level:
                # Tie-break: prefer higher vol_ratio
                if sig.get("vol_ratio", 0) > existing.get("vol_ratio", 0):
                    seen[key] = sig
    result = list(seen.values())
    removed = len(signals) - len(result)
    if removed:
        logger.info(f"Dedup within type: removed {removed} duplicate symbol+type entries")
    return result


def apply_sector_cap(signals: list, symbol_sector_map: dict,
                      rotating_sectors: set = None) -> dict:
    """
    Cap signals by sector. Returns {'top': [...], 'overflow': [...]}.
    - top: up to MAX_PER_SECTOR per sector, then top TOP_N overall
    - overflow: signals beyond the sector cap
    Rotating sectors ranked first within same conviction level.
    """
    rotating_sectors = rotating_sectors or set()

    def sort_key(s):
        sector = symbol_sector_map.get(s.get("symbol", ""), "Unknown")
        rotating_boost = 0 if sector in rotating_sectors else 1
        conv = _CONVICTION_ORDER.get(s.get("conviction", "WATCHLIST"), 0)
        rr = s.get("rr", 0.0)
        return (rotating_boost, -conv, -rr)

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


def get_watchlist_signals(signals: list, excluded_symbols: set = None) -> list:
    """
    Return watchlist candidates — all signals not already selected in the top-5
    groups, regardless of conviction level. These are next-best candidates to
    monitor for the next scan.
    Deduplication: each symbol appears at most once (highest conviction wins).
    """
    excluded_symbols = excluded_symbols or set()

    # All signals not already in the top-5 selections
    candidates = [
        s for s in signals
        if s.get("symbol", "") not in excluded_symbols
    ]

    # Dedup by symbol: keep highest conviction, then best vol_ratio on tie
    seen = {}
    for s in sorted(candidates,
                    key=lambda x: (_CONVICTION_ORDER.get(x.get("conviction", "WATCHLIST"), 0),
                                   x.get("vol_ratio", 0)),
                    reverse=True):
        sym = s.get("symbol", "")
        if sym and sym not in seen:
            seen[sym] = s

    removed = len(candidates) - len(seen)
    if removed:
        logger.debug(f"Watchlist dedup: removed {removed} duplicate entries")

    return list(seen.values())


if __name__ == "__main__":
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
