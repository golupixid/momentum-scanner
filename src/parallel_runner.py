"""
Parallel processing orchestration. Target: <4 min per scan run.
Stage 1 Pre-fetch (~100s): W+D+H batches parallel + sectors + global + corp actions
Stage 2 Filter (~5s): hard filters → ~150-200 pass
Stage 3 Process (~60s): ThreadPool news + ProcessPool indicators simultaneously
Stage 4 Execute (~15s): Execution plans HIGH+MOD only
Stage 5 Rank (~5s): Top 5 per type | sector cap
Stage 6 News (~15s): final 15 stocks only
Stage 7 Deliver (~10s): 5 Telegram messages
"""
import logging
import time
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
import pandas as pd
import pytz

logger = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")


def _process_symbol(args) -> list:
    """
    Process one symbol: indicators + signal generation + conviction.
    Runs in ProcessPoolExecutor worker.
    args: (symbol, daily_data_bytes, weekly_data_bytes, hourly_data_bytes,
           cap_type, is_fno, gate_status, market_regime, sector_bleeding, oi_data, pcr)
    """
    import pickle
    from src.indicators import add_daily_indicators, get_q3_momentum
    from src.momentum_breakout import get_momentum_signals
    from src.reversal_breakout import get_reversal_signals
    from src.conviction import get_conviction_full, apply_marginal_cap

    (symbol, df_daily_bytes, df_weekly_bytes, df_hourly_bytes,
     cap_type, is_fno, gate_status, market_regime,
     sector_bleeding, oi_data, pcr) = args

    try:
        df_daily = pickle.loads(df_daily_bytes) if df_daily_bytes else None
        df_weekly = pickle.loads(df_weekly_bytes) if df_weekly_bytes else None
        df_hourly = pickle.loads(df_hourly_bytes) if df_hourly_bytes else None

        if df_daily is None or df_daily.empty:
            return []

        df_daily_ind = add_daily_indicators(df_daily)

        # Momentum signals
        momentum = get_momentum_signals(symbol, df_daily, df_weekly, cap_type)

        # Reversal signals
        reversal = get_reversal_signals(symbol, df_daily, is_fno, oi_data, pcr)

        all_signals = momentum + reversal

        # Apply conviction
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
    """
    Stage 3: Run indicator + signal processing in thread pool.
    Returns flat list of all signal dicts.
    """
    import pickle
    all_signals = []

    def process_one(sym):
        cap_type = symbol_info.get(sym, {}).get("cap_type", "Large")
        is_fno = sym in fno_stocks
        gate_result = gate_results.get("details", {}).get(sym, {})
        gate_status = gate_result.get("status", "EXCLUDED")
        sector = symbol_info.get(sym, {}).get("sector", "Unknown")
        sect_bleeding = sector_status.get(sector, {}).get("bleeding", False)
        oi_data = oi_cache.get(sym) if oi_cache else None
        pcr = oi_data.get("pcr") if oi_data else None

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
                sect_bleeding, oi_data, pcr,
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
                    s["symbol"] = sym
                    s["sector"] = symbol_info.get(sym, {}).get("sector", "Unknown")
                    s["cap_type"] = symbol_info.get(sym, {}).get("cap_type", "Large")
                all_signals.extend(sigs)
            except Exception as e:
                logger.warning(f"Future failed for {sym}: {e}")

    logger.info(f"Stage 3 complete: {len(all_signals)} signals from {len(symbols)} symbols")
    return all_signals


def run_execution_plans(signals: list, hourly_data: dict,
                         scan_time: datetime = None) -> dict:
    """
    Stage 4: Build execution plans for HIGH and MODERATE signals only.
    Returns {symbol: plan_dict}.
    """
    from src.execution_plan import build_execution_plan
    plans = {}
    high_mod = [s for s in signals if s.get("conviction") in ("HIGH", "MODERATE")]
    for sig in high_mod:
        sym = sig["symbol"]
        df_h = hourly_data.get(sym)
        plan = build_execution_plan(sym, sig, df_h, scan_time)
        sig["rr"] = plan.get("rr", 0)
        plans[sym] = plan
    logger.info(f"Stage 4: Built {len(plans)} execution plans")
    return plans


def full_scan_pipeline(scan_time: datetime = None) -> dict:
    """
    Full scan pipeline — stages 1-7.
    Returns final result dict for Telegram delivery.
    """
    from src.universe import load_universe, load_fno_stocks
    from src.global_markets import check_global_bleeding
    from src.data_fetcher import fetch_all_weekly, fetch_all_daily, fetch_all_hourly, fetch_index_data
    from src.nse_fetcher import derive_market_regime, derive_sector_status
    from src.filters import apply_all_filters
    from src.weekly_gate import apply_weekly_gate
    from src.sector_distribution import apply_sector_cap, split_signals_by_type, get_watchlist_signals
    from src.signal_registry import dedup_signals, register_signal
    from src.news_scanner import fetch_market_headlines, fetch_news_for_signals
    from src.research_journal import record_signal

    t0 = time.time()
    scan_time = scan_time or datetime.now(IST)
    logger.info(f"=== SCAN START {scan_time.strftime('%H:%M IST')} ===")

    # ── Stage 1: Pre-fetch ──────────────────────────────────────────────────
    universe = load_universe()
    fno_stocks = load_fno_stocks()
    symbol_info = universe.set_index("symbol").to_dict("index")
    symbol_sector_map = dict(zip(universe["symbol"], universe["sector"]))
    all_symbols = universe["symbol"].tolist()

    nifty50_path = Path(__file__).parent.parent / "data" / "universe" / "nifty50.csv"
    import pandas as _pd
    nifty50_syms = _pd.read_csv(nifty50_path)["symbol"].tolist() if nifty50_path.exists() else all_symbols[:50]

    # Fetch W + D + H in parallel (each is sequential batches internally)
    with ThreadPoolExecutor(max_workers=4) as ex:
        f_weekly  = ex.submit(fetch_all_weekly, all_symbols)
        f_daily   = ex.submit(fetch_all_daily,  all_symbols)
        f_hourly  = ex.submit(fetch_all_hourly, all_symbols)
        f_global  = ex.submit(check_global_bleeding)
        f_headlines = ex.submit(fetch_market_headlines)

        weekly_data   = f_weekly.result()
        daily_data    = f_daily.result()
        hourly_data   = f_hourly.result()
        global_status = f_global.result()
        headlines     = f_headlines.result()

    # Regime and sector from downloaded stock data (no separate index API call)
    market_regime = derive_market_regime(daily_data, nifty50_syms)
    sector_status = derive_sector_status(daily_data, symbol_sector_map)

    logger.info(
        f"Stage 1 done: {time.time()-t0:.0f}s | regime={market_regime} | "
        f"data: W={len(weekly_data)} D={len(daily_data)} H={len(hourly_data)} symbols"
    )

    # ── Stage 2: Filter ─────────────────────────────────────────────────────
    gate_results = apply_weekly_gate(all_symbols, weekly_data)
    passing_gate = gate_results["passing"]
    logger.info(
        f"Weekly gate: {len(gate_results['eligible'])} eligible, "
        f"{len(gate_results['marginal'])} marginal, "
        f"{len(gate_results['excluded'])} excluded → {len(passing_gate)} to filters"
    )

    filter_report = apply_all_filters(
        passing_gate, daily_data, weekly_data,
        symbol_sector_map, sector_status, global_status,
    )
    passing_symbols = filter_report["passing"]
    logger.info(f"Stage 2 done: {len(passing_symbols)} pass all filters")

    # ── Stage 3: Process ────────────────────────────────────────────────────
    all_signals = run_parallel_processing(
        passing_symbols, daily_data, weekly_data, hourly_data,
        symbol_info, fno_stocks, gate_results,
        market_regime, sector_status,
    )

    # Dedup
    all_signals = dedup_signals(all_signals)
    logger.info(f"Stage 3 done: {len(all_signals)} unique signals")

    # ── Stage 4: Execution plans ─────────────────────────────────────────────
    plans = run_execution_plans(all_signals, hourly_data, scan_time)

    # ── Stage 5: Rank + sector cap ──────────────────────────────────────────
    signal_groups = split_signals_by_type(all_signals)
    rotating_sectors = []  # TODO: implement rotation detection

    final = {}
    overflow_all = []
    for group_name, group_sigs in signal_groups.items():
        result = apply_sector_cap(group_sigs, symbol_sector_map, set(rotating_sectors))
        final[group_name] = result["top"]
        overflow_all.extend(result["overflow"])

    watchlist_sigs = get_watchlist_signals(all_signals)

    # ── Stage 6: News for final 15 ──────────────────────────────────────────
    final_symbols = list(set(
        [s["symbol"] for s in final.get("momentum", [])] +
        [s["symbol"] for s in final.get("reversal", [])] +
        [s["symbol"] for s in final.get("fno", [])]
    ))[:15]
    news_data = fetch_news_for_signals(final_symbols)

    # Apply news downgrade
    for group_sigs in final.values():
        for sig in group_sigs:
            news = news_data.get(sig["symbol"], {})
            if news.get("negative"):
                sig["news_negative"] = True

    # Record to journal
    for group_sigs in final.values():
        for sig in group_sigs:
            record_signal(sig, plans.get(sig["symbol"], {}), {
                "market_regime": market_regime,
                "global_status": "BLEEDING" if global_status.bleeding else "OK",
            })
            register_signal(sig, plans.get(sig["symbol"]))

    elapsed = time.time() - t0
    logger.info(f"=== SCAN COMPLETE {elapsed:.0f}s ===")

    return {
        "market_regime": market_regime,
        "sector_status": sector_status,
        "global_status": global_status,
        "rotating_sectors": rotating_sectors,
        "headlines": headlines,
        "momentum": final.get("momentum", []),
        "reversal": final.get("reversal", []),
        "fno": final.get("fno", []),
        "watchlist": watchlist_sigs[:10],
        "overflow": overflow_all[:5],
        "plans": plans,
        "news_data": news_data,
        "symbol_sector_map": symbol_sector_map,
        "symbol_info": symbol_info,
        "elapsed_seconds": int(elapsed),
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    results = full_scan_pipeline()
    print(f"\nScan complete in {results['elapsed_seconds']}s")
    print(f"Momentum: {len(results['momentum'])} | Reversal: {len(results['reversal'])} | FNO: {len(results['fno'])}")
