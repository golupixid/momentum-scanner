"""Quick functional test of all core modules."""
import sys

def test_universe():
    from src.universe import load_universe, load_fno_stocks, get_universe_batches
    df = load_universe()
    fno = load_fno_stocks()
    batches = get_universe_batches(50)
    assert len(df) > 400, f"Universe too small: {len(df)}"
    assert len(fno) > 50, f"FNO list too small: {len(fno)}"
    assert len(batches) > 5
    print(f"  Universe: {len(df)} stocks, FNO: {len(fno)}, Batches: {len(batches)}")


def test_conviction():
    from src.conviction import assign_conviction, apply_upgrades
    l1 = assign_conviction("ELIGIBLE", True, True, "EXPANDING", "Bull", False, False)
    assert l1 == "HIGH", f"Expected HIGH got {l1}"
    l2 = assign_conviction("MARGINAL", True, False, "NEUTRAL", "Neutral", False, False)
    assert l2 == "MODERATE", f"Expected MODERATE got {l2}"
    l3 = assign_conviction("ELIGIBLE", False, False, "CONTRACTING", "Strong Bear", True, True)
    assert l3 == "WATCHLIST", f"Expected WATCHLIST got {l3}"
    # Upgrade test
    l4 = apply_upgrades("MODERATE", {"sector_rotating": True})
    assert l4 == "HIGH", f"Expected HIGH after upgrade got {l4}"
    print(f"  Conviction: HIGH={l1}, MODERATE={l2}, WATCHLIST={l3}, upgrade={l4}")


def test_signal_registry():
    from src.signal_registry import dedup_signals
    test_signals = [
        {"symbol": "RELIANCE_T", "signal_type": "momentum", "pattern": "20D_HIGH_T"},
        {"symbol": "TCS_T", "signal_type": "momentum", "pattern": "BB_BREAKOUT_T"},
    ]
    unique = dedup_signals(test_signals)
    assert len(unique) == 2, f"Expected 2 unique signals, got {len(unique)}"
    print(f"  Signal registry dedup: {len(unique)}/2 passed")


def test_sector_cap():
    from src.sector_distribution import apply_sector_cap
    signals = [
        {"symbol": "A", "conviction": "HIGH", "rr": 2.5, "signal_type": "momentum"},
        {"symbol": "B", "conviction": "HIGH", "rr": 2.0, "signal_type": "momentum"},
        {"symbol": "C", "conviction": "MODERATE", "rr": 1.8, "signal_type": "momentum"},
    ]
    smap = {"A": "IT", "B": "IT", "C": "IT"}
    result = apply_sector_cap(signals, smap)
    assert len(result["top"]) == 2, f"Expected 2 top, got {len(result['top'])}"
    assert len(result["overflow"]) == 1, f"Expected 1 overflow, got {len(result['overflow'])}"
    print(f"  Sector cap: top={len(result['top'])}, overflow={len(result['overflow'])}")


def test_risk_manager():
    from src.risk_manager import check_exit_conditions
    from datetime import date
    today = date.today().strftime("%Y-%m-%d")  # use today so expiry doesn't trigger
    sig = {"entry": 1000, "sl": 960, "t1": 1080, "t2": 1120,
           "pattern": "20D_HIGH", "scan_date": today}
    r1 = check_exit_conditions(sig, 955.0)
    assert r1["exit"] and r1["type"] == "SL_HIT", f"Expected SL_HIT: {r1}"
    r2 = check_exit_conditions(sig, 1085.0)
    assert r2["exit"] and r2["type"] == "T1_HIT", f"Expected T1_HIT: {r2}"
    r3 = check_exit_conditions(sig, 1050.0)
    assert not r3["exit"], f"Expected no exit at 1050: {r3}"
    print(f"  Risk manager: SL={r1['type']}, T1={r2['type']}, hold={not r3['exit']}")


def test_telegram_card():
    from src.telegram_bot import format_signal_card
    sig = {
        "symbol": "RELIANCE", "cap_type": "Large", "sector": "Energy",
        "conviction": "HIGH", "probability_band": "70-80%",
        "close": 1350.0, "vol_ratio": 2.1, "signal_type": "momentum",
        "pattern": "20D_HIGH", "q1": True, "q2": False, "q3": "EXPANDING",
    }
    card = format_signal_card(sig, rank=1)
    assert "RELIANCE" in card
    assert "HIGH CONVICTION" in card
    assert "1350" in card
    print(f"  Telegram card: {len(card)} chars, RELIANCE present, HIGH conviction")


def test_weekly_reporter():
    from src.weekly_reporter import build_weekly_report
    msgs = build_weekly_report()
    assert len(msgs) == 8, f"Expected 8 messages, got {len(msgs)}"
    print(f"  Weekly reporter: {len(msgs)} messages built")


def test_indicators_logic():
    import pandas as pd
    import numpy as np
    from src.indicators import (add_daily_indicators, is_supertrend_bullish,
                                  get_q3_momentum, is_rsi_rising)
    # Create synthetic bullish data
    n = 100
    close = pd.Series([100 + i * 0.5 + np.sin(i/10) for i in range(n)])
    high = close + 2
    low = close - 2
    volume = pd.Series([1_000_000] * n)
    df = pd.DataFrame({"Close": close, "High": high, "Low": low, "Volume": volume})
    df = add_daily_indicators(df)
    assert "ema9" in df.columns
    assert "rsi" in df.columns
    assert "macd_hist" in df.columns
    assert "supertrend_dir" in df.columns
    assert "high_20d" in df.columns
    print(f"  Indicators: all columns present. RSI={df['rsi'].iloc[-1]:.1f}, "
          f"Supertrend={'bull' if is_supertrend_bullish(df) else 'bear'}")


if __name__ == "__main__":
    tests = [
        ("Universe", test_universe),
        ("Conviction", test_conviction),
        ("Signal Registry", test_signal_registry),
        ("Sector Cap", test_sector_cap),
        ("Risk Manager", test_risk_manager),
        ("Telegram Card", test_telegram_card),
        ("Weekly Reporter", test_weekly_reporter),
        ("Indicators", test_indicators_logic),
    ]

    passed = 0
    failed = 0
    for name, fn in tests:
        print(f"\n[TEST] {name}")
        try:
            fn()
            print(f"  PASS")
            passed += 1
        except Exception as e:
            print(f"  FAIL: {e}")
            failed += 1

    print(f"\n{'='*40}")
    print(f"Results: {passed}/{len(tests)} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)
