"""
Parallel processing orchestration. Target: <4 min per scan run.
Stage 1 Pre-fetch: W+D+H batches + sectors + global + corp actions
Stage 2 Filter: hard filters → ~150-250 pass
Stage 3 Process: indicators + signals via thread pool
Stage 4 Execute: execution plans HIGH+MOD only
Stage 5 Rank: top 5 per type | global dedup | sector cap
Stage 6 News: final 15 stocks only
Stage 7 Deliver: 5 Telegram messages
"""
import csv
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, date
from pathlib import Path
import pandas as pd
import pytz

logger = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")

# Execution plan errors that mean "exclude this signal from results"
_EXCLUDE_ERRORS = [
    "not 3% above", "not above T1",
    "too late", "Journey", "Invalid close",
    "Insufficient daily",
]

_DAILY_SHOWN_DIR    = Path(__file__).parent.parent / "data" / "daily_shown"
_CAUTION_SHOWN_FILE  = _DAILY_SHOWN_DIR / "caution_shown.csv"
_WATCHLIST_SHOWN_FILE = _DAILY_SHOWN_DIR / "watchlist_shown.csv"


def _load_shown_today(filepath: Path, symbol_col: str) -> tuple:
    """Returns (shown_set, today_rows). Rows from previous dates are discarded."""
    today = date.today().isoformat()
    shown, today_rows = set(), []
    if not filepath.exists():
        return shown, today_rows
    try:
        with open(filepath, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("date") == today:
                    shown.add(row.get(symbol_col, ""))
                    today_rows.append(dict(row))
    except Exception as e:
        logger.warning(f"Could not read {filepath.name}: {e}")
    return shown, today_rows


def _write_shown(filepath: Path, today_rows: list, new_rows: list):
    """Write today_rows + new_rows to filepath (replaces stale data from old dates)."""
    all_rows = today_rows + new_rows
    if not all_rows:
        return
    filepath.parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(all_rows[0].keys()))
        writer.writeheader()
        writer.writerows(all_rows)


def _plan_should_exclude(plan: dict) -> bool:
    """Return True if the execution plan's validation error means exclude the signal."""
    if not plan:
        return False
    err = plan.get("error", "")
    if not err:
        return False
    return any(kw.lower() in err.lower() for kw in _EXCLUDE_ERRORS)


def _log_base_condition_failures(passing_symbols: list, daily_data: dict) -> None:
    """Diagnose why MOM/REV raw signals are zero — counts each base-condition failure."""
    from src.indicators import add_daily_indicators, is_supertrend_bullish, is_volume_sufficient
    counts = {"no_data": 0, "below_ema20": 0, "low_vol": 0, "st_bearish": 0, "pass_base": 0}
    for sym in passing_symbols:
        df_raw = daily_data.get(sym)
        if df_raw is None or len(df_raw) < 22:
            counts["no_data"] += 1
            continue
        try:
            df = add_daily_indicators(df_raw)
            last = df.iloc[-1]
            close = float(last["Close"])
            ema20_val = last.get("ema20")
            ema20 = float(ema20_val) if ema20_val is not None and not pd.isna(ema20_val) else None
            if ema20 is None or close <= ema20:
                counts["below_ema20"] += 1
                continue
            if not is_volume_sufficient(df, 1.3):
                counts["low_vol"] += 1
                continue
            if not is_supertrend_bullish(df):
                counts["st_bearish"] += 1
                continue
            counts["pass_base"] += 1
        except Exception:
            counts["no_data"] += 1
    logger.info(
        f"MOM/REV base-condition breakdown ({len(passing_symbols)} symbols): "
        f"no_data={counts['no_data']} | below_EMA20={counts['below_ema20']} | "
        f"low_vol={counts['low_vol']} | supertrend_bearish={counts['st_bearish']} | "
        f"pass_all_base={counts['pass_base']}"
    )


def _process_symbol(args) -> list:
    """
    Process one symbol: indicators + signal generation + conviction.
    Runs inline in ThreadPoolExecutor worker.
    """
    import pickle
    from src.indicators import add_daily_indicators
    from src.momentum_breakout import get_momentum_signals
    from src.reversal_breakout import get_reversal_signals
    from src.conviction import get_conviction_full

    (symbol, df_daily_bytes, df_weekly_bytes, df_hourly_bytes,
     cap_type, is_fno, gate_status, market_regime,
     sector_bleeding, oi_data, pcr) = args

    try:
        df_daily  = pickle.loads(df_daily_bytes)  if df_daily_bytes  else None
        df_weekly = pickle.loads(df_weekly_bytes) if df_weekly_bytes else None

        if df_daily is None or df_daily.empty:
            return []

        df_daily_ind = add_daily_indicators(df_daily)

        # CHANGE 1: skip momentum for stocks below 20W EMA (reversal-only candidates)
        momentum = ([] if gate_status == "REV_ONLY"
                    else get_momentum_signals(symbol, df_daily, df_weekly, cap_type))
        reversal = get_reversal_signals(symbol, df_daily, is_fno, oi_data, pcr)

        all_signals = momentum + reversal
        for sig in all_signals:
            get_conviction_full(sig, gate_status, market_regime,
                                sector_bleeding, df_daily_ind)
        return all_signals

    except Exception as e:
        logger.warning(f"Processing failed for {symbol}: {e}")
        return []


def run_parallel_processing(symbols: list, daily_data: dict, weekly_data: dict,
                              hourly_data: dict, symbol_info: dict,
                              fno_stocks: set, gate_results: dict,
                              market_regime: str, sector_status: dict,
                              oi_cache: dict = None,
                              max_workers: int = 4) -> list:
    """Stage 3: Signal processing. Returns flat list of signal dicts."""
    import pickle
    all_signals = []

    def process_one(sym):
        cap_type    = symbol_info.get(sym, {}).get("cap_type", "Large")
        is_fno      = sym in fno_stocks
        gate_result = gate_results.get("details", {}).get(sym, {})
        gate_status = gate_result.get("status", "EXCLUDED")
        sector      = symbol_info.get(sym, {}).get("sector", "Unknown")
        sect_bleed  = sector_status.get(sector, {}).get("bleeding", False)
        oi_data     = oi_cache.get(sym) if oi_cache else None
        pcr         = oi_data.get("pcr") if oi_data else None

        try:
            df_d = daily_data.get(sym)
            df_w = weekly_data.get(sym)
            df_h = hourly_data.get(sym)
            args = (
                sym,
                pickle.dumps(df_d) if df_d is not None else None,
                pickle.dumps(df_w) if df_w is not None else None,
                pickle.dumps(df_h) if df_h is not None else None,
                cap_type, is_fno, gate_status, market_regime,
                sect_bleed, oi_data, pcr,
            )
            return _process_symbol(args)
        except Exception as e:
            logger.warning(f"Failed {sym}: {e}")
            return []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(process_one, sym): sym for sym in symbols}
        for fut in as_completed(futures):
            sym = futures[fut]
            try:
                sigs = fut.result()
                for s in sigs:
                    s["symbol"]   = sym
                    s["sector"]   = symbol_info.get(sym, {}).get("sector", "Unknown")
                    s["cap_type"] = symbol_info.get(sym, {}).get("cap_type", "Large")
                all_signals.extend(sigs)
            except Exception as e:
                logger.warning(f"Future failed for {sym}: {e}")

    logger.info(f"Stage 3 complete: {len(all_signals)} signals from {len(symbols)} symbols")
    return all_signals


def run_execution_plans(signals: list, hourly_data: dict, daily_data: dict,
                         scan_time: datetime = None) -> dict:
    """Stage 4: Build execution plans for all signals (enables caution card detection)."""
    from src.execution_plan import build_execution_plan
    plans = {}
    for sig in signals:
        sym  = sig["symbol"]
        df_h = hourly_data.get(sym)
        df_d = daily_data.get(sym)
        plan = build_execution_plan(sym, sig, df_h, scan_time, df_daily=df_d)
        sig["rr"] = plan.get("rr", 0)
        plans[sym] = plan
    logger.info(f"Stage 4: Built {len(plans)} execution plans")
    return plans


def compute_proximity_watchlist(passing_symbols: list, daily_data: dict,
                                 all_signal_symbols: set) -> dict:
    """
    Compute MOMENTUM and REVERSAL watchlists:
    stocks within -3% of their breakout/reversal trigger that have NOT
    already appeared in today's top-5 signal groups.

    Scoring (0-100):
      Proximity:  within -1% → 50pts, -2% → 30pts, -3% → 10pts
      Vol ratio:  vol_ratio/2 × 30 points (capped at 30)
      RSI rising: 20 points if RSI is rising
    """
    from src.indicators import add_daily_indicators, is_rsi_rising

    def score(prox_abs: float, vol_ratio: float, rsi_r: bool) -> float:
        if prox_abs <= 1.0:
            p = 50
        elif prox_abs <= 2.0:
            p = 30
        else:
            p = 10
        v = min(30.0, (vol_ratio / 2.0) * 30.0)
        r = 20.0 if rsi_r else 0.0
        return p + v + r

    momentum_wl = []
    reversal_wl = []

    for sym in passing_symbols:
        if sym in all_signal_symbols:
            continue  # already in main signals
        df_raw = daily_data.get(sym)
        if df_raw is None or len(df_raw) < 22:
            continue
        try:
            df = add_daily_indicators(df_raw)
            last = df.iloc[-1]
            close = float(last["Close"])
            vol_ratio = float(last.get("vol_ratio", 1.0))
            rsi_r = is_rsi_rising(df, bars=1)

            # MOMENTUM: proximity to 20D high breakout
            if "high_20d" in df.columns and not pd.isna(last.get("high_20d")):
                trigger = float(last["high_20d"])
                if trigger > 0 and close < trigger:
                    prox = (trigger - close) / trigger * 100
                    if prox <= 3.0:
                        momentum_wl.append({
                            "symbol":   sym,
                            "trigger":  round(trigger, 2),
                            "close":    round(close, 2),
                            "prox_pct": round(-prox, 2),  # negative = below trigger
                            "score":    score(prox, vol_ratio, rsi_r),
                            "pattern":  "20D_HIGH",
                        })

            # REVERSAL: 9 EMA approaching 16 EMA from below (within -3%)
            if "ema9" in df.columns and "ema16" in df.columns:
                ema9  = float(last.get("ema9",  0) or 0)
                ema16 = float(last.get("ema16", 0) or 0)
                if ema16 > 0 and 0 < ema9 < ema16:
                    prox = (ema16 - ema9) / ema16 * 100
                    if prox <= 3.0:
                        reversal_wl.append({
                            "symbol":   sym,
                            "trigger":  round(ema16, 2),
                            "close":    round(close, 2),
                            "prox_pct": round(-prox, 2),
                            "score":    score(prox, vol_ratio, rsi_r),
                            "pattern":  "EMA_CROSS",
                        })
        except Exception as e:
            logger.debug(f"Watchlist compute failed for {sym}: {e}")

    momentum_wl.sort(key=lambda x: x["score"], reverse=True)
    reversal_wl.sort(key=lambda x: x["score"], reverse=True)

    logger.info(
        f"Proximity watchlist: MOM_wl={len(momentum_wl[:5])} "
        f"REV_wl={len(reversal_wl[:5])} (within -3% of trigger)"
    )
    return {"momentum_wl": momentum_wl[:5], "reversal_wl": reversal_wl[:5]}


def full_scan_pipeline(scan_time: datetime = None, real_run: bool = False) -> dict:
    """
    Full scan pipeline — stages 1-7.
    real_run=True → write signals to registry (GitHub Actions / --real-run only).
    real_run=False → read-only test; Telegram still sent, registry not written.
    """
    from src.universe import load_universe, load_fno_stocks
    from src.global_markets import check_global_bleeding
    from src.data_fetcher import fetch_all_weekly, fetch_all_daily, fetch_all_hourly
    from src.nse_fetcher import derive_market_regime, derive_sector_status
    from src.filters import apply_all_filters
    from src.weekly_gate import apply_weekly_gate
    from src.sector_distribution import (apply_sector_cap, split_signals_by_type,
                                          get_watchlist_signals, dedup_signals_within_type)
    from src.signal_registry import dedup_signals, register_signal, cleanup_and_get_blocked
    from src.fno_signals import build_oi_cache_for_fno
    from src.news_scanner import fetch_market_headlines, fetch_news_for_signals
    from src.research_journal import record_signal

    t0 = time.time()
    scan_time = scan_time or datetime.now(IST)
    logger.info(f"=== SCAN START {scan_time.strftime('%H:%M IST')} | real_run={real_run} ===")

    # ── Stage 1: Pre-fetch ──────────────────────────────────────────────────
    universe      = load_universe()
    fno_stocks    = load_fno_stocks()
    symbol_info   = universe.set_index("symbol").to_dict("index")
    symbol_sector = dict(zip(universe["symbol"], universe["sector"]))
    all_symbols   = universe["symbol"].tolist()

    nifty50_path = Path(__file__).parent.parent / "data" / "universe" / "nifty50.csv"
    import pandas as _pd
    nifty50_syms = _pd.read_csv(nifty50_path)["symbol"].tolist() if nifty50_path.exists() else all_symbols[:50]

    logger.info(f"Universe: {len(all_symbols)} stocks | FNO: {len(fno_stocks)} stocks")

    with ThreadPoolExecutor(max_workers=4) as ex:
        f_weekly    = ex.submit(fetch_all_weekly, all_symbols)
        f_daily     = ex.submit(fetch_all_daily,  all_symbols)
        f_hourly    = ex.submit(fetch_all_hourly, all_symbols)
        f_global    = ex.submit(check_global_bleeding)
        f_headlines = ex.submit(fetch_market_headlines)
        weekly_data   = f_weekly.result()
        daily_data    = f_daily.result()
        hourly_data   = f_hourly.result()
        global_status = f_global.result()
        headlines     = f_headlines.result()

    market_regime = derive_market_regime(daily_data, nifty50_syms)
    sector_status = derive_sector_status(daily_data, symbol_sector)

    w_pct = len(weekly_data) / max(len(all_symbols), 1) * 100
    d_pct = len(daily_data)  / max(len(all_symbols), 1) * 100
    logger.info(
        f"Stage 1 done: {time.time()-t0:.0f}s | regime={market_regime} | "
        f"W={len(weekly_data)}/{len(all_symbols)}({w_pct:.0f}%) "
        f"D={len(daily_data)}/{len(all_symbols)}({d_pct:.0f}%) "
        f"H={len(hourly_data)}/{len(all_symbols)}"
    )

    # ── Pre-load daily shown files (read at scan start, before any signal processing) ──
    caution_shown_today, caution_shown_rows = _load_shown_today(_CAUTION_SHOWN_FILE, "symbol")
    _, watchlist_shown_rows = _load_shown_today(_WATCHLIST_SHOWN_FILE, "symbol")
    mom_shown_today = {r["symbol"] for r in watchlist_shown_rows if r.get("category") == "momentum"}
    rev_shown_today = {r["symbol"] for r in watchlist_shown_rows if r.get("category") == "reversal"}
    logger.info(
        f"Daily shown pre-load: caution={len(caution_shown_today)} symbols "
        f"({sorted(caution_shown_today) or 'none'}) | "
        f"watchlist MOM={len(mom_shown_today)} REV={len(rev_shown_today)} symbols"
    )

    # Registry cleanup (read-only in test mode)
    blocked_symbols = cleanup_and_get_blocked(daily_data, real_run=real_run)
    logger.info(f"Registry: {len(blocked_symbols)} symbols blocked")

    # Build FNO OI cache
    fno_in_universe = fno_stocks & set(all_symbols)
    oi_cache = build_oi_cache_for_fno(fno_in_universe, daily_data)
    logger.info(f"FNO OI: {len(oi_cache)} stocks cached")

    # ── Stage 2: Filter ─────────────────────────────────────────────────────
    gate_results  = apply_weekly_gate(all_symbols, weekly_data)
    passing_gate  = gate_results["passing"]

    # FIX 7: Re-insert excluded FNO stocks as MARGINAL so FNO signals can fire.
    excluded_fno = [s for s in gate_results.get("excluded", [])
                    if s in fno_stocks and s in daily_data]
    if excluded_fno:
        logger.info(f"FNO gate bypass: {len(excluded_fno)} excluded FNO stocks re-added as MARGINAL")
        for sym in excluded_fno:
            gate_results["details"][sym]["status"] = "MARGINAL"
        passing_gate = list(passing_gate) + excluded_fno

    # CHANGE 1: Re-insert excluded non-FNO stocks for reversal-only processing.
    # Reversal stocks may be below 20W EMA — they are recovering from weakness.
    excluded_rev = [s for s in gate_results.get("excluded", [])
                    if s not in fno_stocks and s in daily_data]
    if excluded_rev:
        logger.info(
            f"Reversal gate bypass: {len(excluded_rev)} stocks below 20W EMA "
            f"added for reversal-only signal detection"
        )
        for sym in excluded_rev:
            gate_results["details"][sym]["status"] = "REV_ONLY"
        passing_gate = list(passing_gate) + excluded_rev

    logger.info(
        f"Weekly gate: {len(gate_results['eligible'])} eligible, "
        f"{len(gate_results['marginal'])} marginal, "
        f"{len(gate_results['excluded'])} excluded "
        f"→ {len(passing_gate)} to filters (incl FNO+REV bypasses)"
    )

    filter_report   = apply_all_filters(
        passing_gate, daily_data, weekly_data, symbol_sector, sector_status, global_status,
    )
    passing_symbols = [s for s in filter_report["passing"] if s not in blocked_symbols]
    logger.info(
        f"Stage 2 done: {len(passing_symbols)} pass all filters "
        f"({len(filter_report['passing']) - len(passing_symbols)} blocked by registry)"
    )

    # ── Stage 3: Process signals ─────────────────────────────────────────────
    all_signals = run_parallel_processing(
        passing_symbols, daily_data, weekly_data, hourly_data,
        symbol_info, fno_stocks, gate_results, market_regime, sector_status,
        oi_cache=oi_cache,
    )

    all_signals = dedup_signals_within_type(all_signals)
    all_signals = dedup_signals(all_signals)

    signal_groups = split_signals_by_type(all_signals)
    logger.info(
        f"Stage 3 done: {len(all_signals)} unique signals | "
        f"MOM_raw={len(signal_groups.get('momentum', []))} "
        f"REV_raw={len(signal_groups.get('reversal', []))} "
        f"FNO_raw={len(signal_groups.get('fno', []))}"
    )

    # Diagnostic: when MOM or REV are zero, explain why base conditions fail
    if len(signal_groups.get("momentum", [])) == 0 or len(signal_groups.get("reversal", [])) == 0:
        _log_base_condition_failures(passing_symbols, daily_data)

    # ── Stage 4: Execution plans ─────────────────────────────────────────────
    plans = run_execution_plans(all_signals, hourly_data, daily_data, scan_time)

    # Filter signals via execution plan validation; collect T1 1-3% failures as caution candidates
    caution_pool = {"momentum": [], "reversal": []}
    validated_signals = []
    excluded_count = 0
    for s in all_signals:
        sym   = s.get("symbol", "")
        stype = s.get("signal_type", "")
        conv  = s.get("conviction", "")
        plan  = plans.get(sym)
        if _plan_should_exclude(plan):
            err = plan.get("error", "") if plan else "no plan built"
            # T1 between 1-3%: caution candidate for MOM/REV only (not FNO)
            if plan and "not 3% above" in err and stype in ("momentum", "reversal"):
                t1_val = plan.get("t1", 0)
                el, eh = plan.get("entry_low", 0), plan.get("entry_high", 0)
                em = (el + eh) / 2.0
                if em > 0 and t1_val > 0:
                    t1_pct = (t1_val - em) / em * 100
                    if 1.0 <= t1_pct < 3.0:
                        s["_caution_t1_pct"] = round(t1_pct, 2)
                        s["_caution_plan"] = plan
                        caution_pool[stype].append(s)
            logger.info(f"Plan excluded {sym} ({stype}/{conv}): {err}")
            excluded_count += 1
        else:
            validated_signals.append(s)
    if excluded_count:
        logger.info(f"Stage 4: excluded {excluded_count} signals via execution plan validation")
    all_signals = validated_signals
    signal_groups = split_signals_by_type(all_signals)

    # ── Stage 5: Rank + sector cap (global cross-category dedup) ─────────────
    # Priority: FNO first → Momentum second → Reversal last.
    rotating_sectors = []
    final: dict = {}
    overflow_all = []
    selected_symbols: set = set()

    for group_name in ("fno", "momentum", "reversal"):
        group_sigs = signal_groups.get(group_name, [])
        eligible   = [s for s in group_sigs if s["symbol"] not in selected_symbols]
        removed    = len(group_sigs) - len(eligible)
        if removed:
            logger.info(f"Global dedup: {group_name} removed {removed} cross-category duplicates")
        result = apply_sector_cap(eligible, symbol_sector, set(rotating_sectors))
        final[group_name] = result["top"]
        selected_symbols.update(s["symbol"] for s in result["top"])
        overflow_all.extend(result["overflow"])

    main_signal_symbols = set(s["symbol"] for group in final.values() for s in group)

    logger.info(
        f"Stage 5 done: MOM={len(final.get('momentum', []))} "
        f"REV={len(final.get('reversal', []))} "
        f"FNO={len(final.get('fno', []))} overflow={len(overflow_all)}"
    )

    # ── CHANGE 2: Fill empty slots with caution candidates (T1 1-3%, MOM/REV only) ──
    caution_final = {"momentum": [], "reversal": []}
    caution_selected = set(selected_symbols)  # never overlap with main signals
    for group_name in ("momentum", "reversal"):
        empty_slots = max(0, 5 - len(final.get(group_name, [])))
        if empty_slots > 0 and caution_pool.get(group_name):
            # Exclude caution stocks already shown today (no-repeat same day)
            pool_sorted = sorted(
                [c for c in caution_pool[group_name]
                 if c["symbol"] not in caution_shown_today],
                key=lambda x: x.get("_caution_t1_pct", 0), reverse=True,
            )
            for candidate in pool_sorted:
                if candidate["symbol"] not in caution_selected:
                    caution_final[group_name].append(candidate)
                    caution_selected.add(candidate["symbol"])
                    if len(caution_final[group_name]) >= empty_slots:
                        break
    if any(caution_final.values()):
        logger.info(
            f"Caution cards: MOM={len(caution_final['momentum'])} "
            f"REV={len(caution_final['reversal'])} (T1 1-3%, not written to registry)"
        )
        if real_run:
            _today = date.today().isoformat()
            new_caution_rows = [
                {"symbol": s["symbol"], "signal_type": s.get("signal_type", ""), "date": _today}
                for group in caution_final.values() for s in group
            ]
            _write_shown(_CAUTION_SHOWN_FILE, caution_shown_rows, new_caution_rows)

    # ── Proximity watchlist for footer ───────────────────────────────────────
    proximity_wl = compute_proximity_watchlist(passing_symbols, daily_data, main_signal_symbols)

    # Filter watchlist by already-shown today per category (no-repeat same day)
    filtered_mom_wl = [s for s in proximity_wl["momentum_wl"] if s["symbol"] not in mom_shown_today]
    filtered_rev_wl = [s for s in proximity_wl["reversal_wl"] if s["symbol"] not in rev_shown_today]
    if real_run:
        _today = date.today().isoformat()
        new_wl_rows = (
            [{"symbol": s["symbol"], "category": "momentum", "date": _today}
             for s in filtered_mom_wl[:5]] +
            [{"symbol": s["symbol"], "category": "reversal", "date": _today}
             for s in filtered_rev_wl[:5]]
        )
        if new_wl_rows:
            _write_shown(_WATCHLIST_SHOWN_FILE, watchlist_shown_rows, new_wl_rows)
    proximity_wl = {"momentum_wl": filtered_mom_wl[:5], "reversal_wl": filtered_rev_wl[:5]}

    # ── Stage 6: News for final 15 ──────────────────────────────────────────
    final_symbols = list(set(
        [s["symbol"] for s in final.get("momentum", [])] +
        [s["symbol"] for s in final.get("reversal", [])] +
        [s["symbol"] for s in final.get("fno", [])]
    ))[:15]
    news_data = fetch_news_for_signals(final_symbols)

    for group_sigs in final.values():
        for sig in group_sigs:
            if news_data.get(sig["symbol"], {}).get("negative"):
                sig["news_negative"] = True

    # Record to journal + registry (real_run only)
    for group_sigs in final.values():
        for sig in group_sigs:
            record_signal(sig, plans.get(sig["symbol"], {}), {
                "market_regime": market_regime,
                "global_status": "BLEEDING" if global_status.bleeding else "OK",
            })
            if real_run:
                register_signal(sig, plans.get(sig["symbol"]))

    if not real_run:
        logger.info("TEST MODE: signals NOT written to active_signals.csv (no --real-run)")

    elapsed = time.time() - t0
    logger.info(f"=== SCAN COMPLETE {elapsed:.0f}s ===")

    return {
        "market_regime":    market_regime,
        "sector_status":    sector_status,
        "global_status":    global_status,
        "rotating_sectors": rotating_sectors,
        "headlines":        headlines,
        "momentum":         final.get("momentum", []),
        "reversal":         final.get("reversal", []),
        "fno":              final.get("fno", []),
        "momentum_caution": caution_final.get("momentum", []),
        "reversal_caution": caution_final.get("reversal", []),
        "momentum_wl":      proximity_wl["momentum_wl"],
        "reversal_wl":      proximity_wl["reversal_wl"],
        "plans":            plans,
        "news_data":        news_data,
        "symbol_sector_map": symbol_sector,
        "symbol_info":      symbol_info,
        "elapsed_seconds":  int(elapsed),
        "daily_data":       daily_data,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    results = full_scan_pipeline()
    print(f"\nScan complete in {results['elapsed_seconds']}s")
    print(f"MOM:{len(results['momentum'])} REV:{len(results['reversal'])} FNO:{len(results['fno'])}")
