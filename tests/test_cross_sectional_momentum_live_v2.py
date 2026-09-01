"""tests/test_cross_sectional_momentum_live_v2.py — CROSS_SECTIONAL_MOMENTUM_LIVE_V2
(Live Alpha Lab). CHALLENGER to CROSS_SECTIONAL_MOMENTUM_LIVE_V1 (frozen-50
universe) — same mechanism, dynamic liquid-alt universe. This file tests
ONLY the V2 module (tests/test_cross_sectional_momentum_live.py covers V1
separately, untouched).

Covers: universe.py's exchangeInfo candidate filter (PERPETUAL/USDT/TRADING/
COIN boundaries, malformed-input handling, the sanity cap), signal.py's
causal trailing-return/liquidity correctness (no lookahead), cross-sectional
top-bucket selection boundaries (liquidity filter at V2's OWN $2M threshold,
bucket-size rounding, ranking direction), weekly-rebalance-date extraction,
the full build_weekly_decisions pipeline (long-only enforcement, only-
selected-rows-appear, empty-input handling throughout), and fail-closed
behavior (unknown alpha_id, blocked alpha_id) for the runner script.

Does NOT hit the network (no exchangeInfo/klines REST calls — universe.py's
filter and signal.py's math are both pure functions over data structures
passed in directly).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from src.institutional.engines.cross_sectional_momentum_live_v2.universe import (
    MAX_SANE_CANDIDATE_COUNT, MIN_LISTING_AGE_DAYS, build_pit_eligibility_log,
    candidate_symbols_from_exchange_info, first_price_date_per_symbol,
    historical_reinclusion_candidates, mask_pre_eligibility, onboard_ts_map,
    resolve_dynamic_liquid_universe, resolve_onboard_dates, summarize_pit_log,
    write_pit_universe_log)
from src.institutional.engines.cross_sectional_momentum_live_v2.signal import (
    LOOKBACK_DAYS, MIN_LIQUIDITY_USD, TOP_FRACTION, build_weekly_decisions,
    select_top_bucket, trailing_liquidity_usd, trailing_return,
    weekly_rebalance_dates)


# ── universe.py — dynamic exchangeInfo candidate filter ────────────────────

def _sym(symbol, contract_type="PERPETUAL", quote="USDT", status="TRADING",
         underlying="COIN"):
    return {"symbol": symbol, "contractType": contract_type, "quoteAsset": quote,
            "status": status, "underlyingType": underlying}


def test_universe_accepts_trading_usdt_perpetual_coin():
    info = {"symbols": [_sym("BTCUSDT")]}
    out = resolve_dynamic_liquid_universe(info)
    assert out == ["BTCUSDT"]


def test_universe_excludes_non_perpetual_contract_type():
    info = {"symbols": [_sym("BTCUSDT_250926", contract_type="CURRENT_QUARTER")]}
    assert resolve_dynamic_liquid_universe(info) == []


def test_universe_excludes_non_usdt_quote():
    info = {"symbols": [_sym("BTCUSDC", quote="USDC"), _sym("BTCUSD_PERP", quote="USD1")]}
    assert resolve_dynamic_liquid_universe(info) == []


def test_universe_excludes_non_trading_status():
    info = {"symbols": [_sym("MKRUSDT", status="SETTLING"), _sym("NEWUSDT", status="PENDING_TRADING")]}
    assert resolve_dynamic_liquid_universe(info) == []


def test_universe_excludes_non_coin_underlying():
    """Binance's USDM futures exchangeInfo also lists tokenized equities and
    basket/index underlyings on the same endpoint (verified live 2026-09-01:
    EQUITY/CN_EQUITY/HK_EQUITY/KR_EQUITY/COMMODITY/PREMARKET/INDEX) -- none
    of these are eligible for a crypto cross-sectional momentum universe."""
    info = {"symbols": [
        _sym("BTCDOMUSDT", underlying="INDEX"),
        _sym("AAPLUSDT", underlying="EQUITY"),
        _sym("GOLDUSDT", underlying="COMMODITY"),
    ]}
    assert resolve_dynamic_liquid_universe(info) == []


def test_universe_excludes_non_ascii_ticker():
    """Verified live 2026-09-01: a handful of Binance USDM perpetuals carry
    non-ASCII (CJK) vanity ticker names. This alpha reuses V1's
    klines_source.py READ-ONLY, which does not percent-encode the symbol in
    its request URL (Python's http.client then raises UnicodeEncodeError) --
    rather than depend on that shared, frozen, un-modifiable-here module's
    caught-exception fallback, universe.py excludes non-ASCII tickers
    upfront, explicitly."""
    info = {"symbols": [_sym("BTCUSDT"), _sym("币安USDT")]}
    assert resolve_dynamic_liquid_universe(info) == ["BTCUSDT"]


def test_universe_mixed_batch_keeps_only_eligible():
    info = {"symbols": [
        _sym("BTCUSDT"), _sym("ETHUSDT"),
        _sym("BTCDOMUSDT", underlying="INDEX"),
        _sym("MKRUSDT", status="SETTLING"),
        _sym("BTCUSDC", quote="USDC"),
        _sym("BTCUSDT_250926", contract_type="CURRENT_QUARTER"),
    ]}
    assert resolve_dynamic_liquid_universe(info) == ["BTCUSDT", "ETHUSDT"]


def test_universe_sorted_and_deduplicated():
    info = {"symbols": [_sym("SOLUSDT"), _sym("BTCUSDT"), _sym("BTCUSDT")]}
    assert resolve_dynamic_liquid_universe(info) == ["BTCUSDT", "SOLUSDT"]


def test_universe_empty_input():
    assert resolve_dynamic_liquid_universe({}) == []
    assert resolve_dynamic_liquid_universe({"symbols": []}) == []
    assert resolve_dynamic_liquid_universe(None) == []


def test_universe_malformed_entry_skipped_not_crashed():
    info = {"symbols": [_sym("BTCUSDT"), "not_a_dict", {"symbol": "INCOMPLETE"}, None]}
    assert resolve_dynamic_liquid_universe(info) == ["BTCUSDT"]


def test_candidate_symbols_returns_raw_dicts():
    info = {"symbols": [_sym("BTCUSDT")]}
    out = candidate_symbols_from_exchange_info(info)
    assert len(out) == 1
    assert out[0]["symbol"] == "BTCUSDT"


def test_universe_sanity_cap_raises():
    info = {"symbols": [_sym(f"SYM{i}USDT") for i in range(MAX_SANE_CANDIDATE_COUNT + 1)]}
    with pytest.raises(RuntimeError, match="MAX_SANE_CANDIDATE_COUNT"):
        resolve_dynamic_liquid_universe(info)


# ── universe.py — PIT eligibility fix (2026-09-01 bug fix) ─────────────────
# Bug: the original build resolved candidates from LIVE exchangeInfo once
# per run and applied TODAY's TRADING status retroactively to every
# historical rebalance -- survivorship bias (delisted symbols invisible
# forever) plus no explicit, onboard_ts-based listing-age gate. These tests
# cover the fix: historical delisted-symbol reinclusion, onboard_ts
# resolution (calendar + fallback), the explicit age/history/liquidity gate
# and its rejection reasons, the masking that feeds signal.py, and the
# queryable per-rebalance audit log.

def _cal(rows):
    """rows: list of (symbol, onboard_ts_str_or_None, status)."""
    return pd.DataFrame({
        "symbol": [r[0] for r in rows],
        "onboard_ts": [pd.Timestamp(r[1], tz="UTC") if r[1] else pd.NaT for r in rows],
        "status": [r[2] for r in rows],
        "source": ["test"] * len(rows),
    })


def test_historical_reinclusion_splits_fetchable_vs_no_data():
    cal = _cal([
        ("DEADUSDT", "2021-01-01", "DELISTED"),
        ("GONEUSDT", None, "DELISTED_NO_DATA"),
        ("LIVEUSDT", "2020-01-01", "TRADING"),   # not DELISTED -- not a reinclusion candidate
    ])
    fetchable, no_data = historical_reinclusion_candidates(cal)
    assert fetchable == ["DEADUSDT"]
    assert no_data == ["GONEUSDT"]


def test_historical_reinclusion_excludes_already_live_symbols():
    cal = _cal([("BTCUSDT", "2019-09-01", "DELISTED")])   # pathological but defensive
    fetchable, _ = historical_reinclusion_candidates(cal, exclude={"BTCUSDT"})
    assert fetchable == []


def test_historical_reinclusion_rejects_non_usdt_and_non_ascii():
    cal = _cal([("DEADBUSD", "2021-01-01", "DELISTED"), ("币安USDT", "2021-01-01", "DELISTED")])
    fetchable, _ = historical_reinclusion_candidates(cal)
    assert fetchable == []


def test_historical_reinclusion_empty_calendar():
    assert historical_reinclusion_candidates(pd.DataFrame()) == ([], [])


def test_onboard_ts_map_only_known_dates():
    cal = _cal([("A", "2020-01-01", "TRADING"), ("B", None, "DELISTED_NO_DATA")])
    m = onboard_ts_map(cal)
    assert set(m) == {"A"}


def test_first_price_date_per_symbol():
    idx = pd.date_range("2024-01-01", periods=10, freq="D", tz="UTC")
    close = pd.DataFrame({"A": [np.nan, np.nan, 1.0, 1.0, 1.0, 1, 1, 1, 1, 1],
                           "B": [1.0] * 10}, index=idx)
    out = first_price_date_per_symbol(close)
    assert out["A"] == idx[2]
    assert out["B"] == idx[0]


def test_resolve_onboard_dates_prefers_calendar_over_fallback():
    cal = _cal([("A", "2019-01-01", "TRADING")])
    idx = pd.date_range("2024-01-01", periods=3, freq="D", tz="UTC")
    close = pd.DataFrame({"A": [1.0, 1, 1]}, index=idx)   # first-price-data would say 2024
    out = resolve_onboard_dates(["A"], cal, close)
    row = out[out["symbol"] == "A"].iloc[0]
    assert row["onboard_ts"] == pd.Timestamp("2019-01-01", tz="UTC")
    assert row["onboard_source"] == "listings_calendar"


def test_resolve_onboard_dates_falls_back_when_absent_from_calendar():
    cal = _cal([])
    idx = pd.date_range("2024-03-01", periods=3, freq="D", tz="UTC")
    close = pd.DataFrame({"NEWUSDT": [np.nan, 1.0, 1.0]}, index=idx)
    out = resolve_onboard_dates(["NEWUSDT"], cal, close)
    row = out[out["symbol"] == "NEWUSDT"].iloc[0]
    assert row["onboard_ts"] == idx[1]
    assert row["onboard_source"] == "first_price_data_fallback"


def test_resolve_onboard_dates_unknown_when_no_data_anywhere():
    out = resolve_onboard_dates(["GHOSTUSDT"], pd.DataFrame(), pd.DataFrame({"OTHER": [1.0]}))
    row = out.iloc[0]
    assert pd.isna(row["onboard_ts"])
    assert row["onboard_source"] == "unknown"


def _pit_panel():
    """OLDUSDT: continuous real data since 2020-01-01 (well past any age gate).
    NEWUSDT: real data only from 2024-06-01 onward (simulates a symbol that
    genuinely listed then -- Binance has NO data before a real listing)."""
    idx = pd.date_range("2020-01-01", "2024-12-31", freq="D", tz="UTC")
    rng = np.random.default_rng(0)
    close = pd.DataFrame(index=idx)
    close["OLDUSDT"] = 100.0 + np.cumsum(rng.normal(0, 1, len(idx)))
    close["NEWUSDT"] = np.nan
    mask = idx >= pd.Timestamp("2024-06-01", tz="UTC")
    close.loc[mask, "NEWUSDT"] = 50.0 + np.cumsum(rng.normal(0, 1, int(mask.sum())))
    vol = pd.DataFrame(index=idx)
    vol["OLDUSDT"] = 5_000_000.0
    vol["NEWUSDT"] = np.nan
    vol.loc[mask, "NEWUSDT"] = 5_000_000.0
    cal = _cal([("OLDUSDT", "2020-01-01", "TRADING"), ("NEWUSDT", "2024-06-01", "TRADING")])
    onboard_df = resolve_onboard_dates(["OLDUSDT", "NEWUSDT"], cal, close)
    return close, vol, onboard_df


def test_pit_eligibility_log_excludes_not_yet_listed_symbol_from_old_rebalance():
    """Core regression test for the bug: a symbol whose real price history
    starts in 2024 must NEVER appear eligible for a 2021 rebalance."""
    close, vol, onboard_df = _pit_panel()
    d_2021 = pd.Timestamp("2021-01-04", tz="UTC")   # Monday, long before NEWUSDT existed
    log = build_pit_eligibility_log([d_2021], ["OLDUSDT", "NEWUSDT"], onboard_df, close, vol,
                                     min_listing_age_days=30, min_liquidity_usd=2_000_000.0,
                                     liquidity_window=30, lookback=7)
    row = log[log["symbol"] == "NEWUSDT"].iloc[0]
    assert row["eligible"] == False
    assert row["reason"] == "not_yet_listed"
    assert log[log["symbol"] == "OLDUSDT"].iloc[0]["eligible"] == True


def test_pit_eligibility_log_insufficient_listing_age_reason():
    close, vol, onboard_df = _pit_panel()
    d_just_after_listing = pd.Timestamp("2024-06-10", tz="UTC")   # 9 days old, < 30d gate
    log = build_pit_eligibility_log([d_just_after_listing], ["NEWUSDT"], onboard_df, close, vol,
                                     min_listing_age_days=30, min_liquidity_usd=2_000_000.0,
                                     liquidity_window=30, lookback=7)
    row = log.iloc[0]
    assert row["eligible"] == False
    assert row["reason"] == f"insufficient_listing_age_{MIN_LISTING_AGE_DAYS}d"


def test_pit_eligibility_log_eligible_once_age_history_liquidity_satisfied():
    close, vol, onboard_df = _pit_panel()
    d_late = pd.Timestamp("2024-12-30", tz="UTC")   # ~7 months after listing
    log = build_pit_eligibility_log([d_late], ["NEWUSDT"], onboard_df, close, vol,
                                     min_listing_age_days=30, min_liquidity_usd=2_000_000.0,
                                     liquidity_window=30, lookback=7)
    row = log.iloc[0]
    assert row["eligible"] == True
    assert row["reason"] is None


def test_pit_eligibility_log_insufficient_liquidity_reason():
    close, vol, onboard_df = _pit_panel()
    vol["OLDUSDT"] = 500_000.0   # below the $2M floor
    d = pd.Timestamp("2024-01-01", tz="UTC")
    log = build_pit_eligibility_log([d], ["OLDUSDT"], onboard_df, close, vol,
                                     min_listing_age_days=30, min_liquidity_usd=2_000_000.0,
                                     liquidity_window=30, lookback=7)
    row = log.iloc[0]
    assert row["eligible"] == False
    assert row["reason"] == "insufficient_liquidity_$2m_30d"


def test_pit_eligibility_log_no_price_history_reason_on_data_gap():
    close, vol, onboard_df = _pit_panel()
    # blow a hole in OLDUSDT's data right before the target date -- old
    # enough by onboard_ts, but the trailing windows can't be computed.
    d = pd.Timestamp("2023-06-05", tz="UTC")
    close.loc[pd.Timestamp("2023-05-01", tz="UTC"):pd.Timestamp("2023-06-04", tz="UTC"), "OLDUSDT"] = np.nan
    vol.loc[pd.Timestamp("2023-05-01", tz="UTC"):pd.Timestamp("2023-06-04", tz="UTC"), "OLDUSDT"] = np.nan
    log = build_pit_eligibility_log([d], ["OLDUSDT"], onboard_df, close, vol,
                                     min_listing_age_days=30, min_liquidity_usd=2_000_000.0,
                                     liquidity_window=30, lookback=7)
    row = log.iloc[0]
    assert row["eligible"] == False
    assert row["reason"] == "no_price_history_before_rebalance_date"


def test_pit_eligibility_log_unknown_onboard_never_eligible():
    close, vol, onboard_df = _pit_panel()
    onboard_df = pd.DataFrame([{"symbol": "GHOSTUSDT", "onboard_ts": pd.NaT, "onboard_source": "unknown"}])
    log = build_pit_eligibility_log([pd.Timestamp("2024-12-30", tz="UTC")], ["GHOSTUSDT"], onboard_df,
                                     close, vol, min_listing_age_days=30, min_liquidity_usd=2_000_000.0,
                                     liquidity_window=30, lookback=7)
    assert log.iloc[0]["reason"] == "not_yet_listed"
    assert log.iloc[0]["eligible"] == False


def test_pit_eligibility_log_delisted_symbol_excluded_after_data_stops():
    """Item 2 du mandat "PHASE FORWARD TRUTH V2" : "asset delisté -> impossible".
    Un symbole réellement délisté n'a par construction plus de nouvelles
    données de prix après sa date de délisting (Binance arrête de publier des
    klines) -- ce mécanisme détecte donc le délisting SANS dépendre de la
    fiabilité du champ `status` du calendrier (double garantie). Vérifié en
    conditions réelles sur RNDRUSDT (délisté, réellement plus de données
    fraîches dans reports/live_alpha_lab/CROSS_SECTIONAL_MOMENTUM_LIVE_V2/
    pit_universe_log.parquet depuis mi-2026) -- reproduit ici en synthétique
    pour que la régression soit testée sans dépendre de données live."""
    idx = pd.date_range("2020-01-01", "2023-02-01", freq="D", tz="UTC")   # s'arrête net -> "délisté"
    rng = np.random.default_rng(1)
    close = pd.DataFrame(index=idx)
    close["DELISTEDUSDT"] = 10.0 + np.cumsum(rng.normal(0, 1, len(idx)))
    vol = pd.DataFrame(index=idx)
    vol["DELISTEDUSDT"] = 5_000_000.0
    cal = _cal([("DELISTEDUSDT", "2020-01-01", "DELISTED")])
    onboard_df = resolve_onboard_dates(["DELISTEDUSDT"], cal, close)

    d_while_trading = pd.Timestamp("2022-06-01", tz="UTC")
    d_after_delisting = pd.Timestamp("2024-01-01", tz="UTC")   # bien après la dernière donnée réelle
    log = build_pit_eligibility_log([d_while_trading, d_after_delisting], ["DELISTEDUSDT"], onboard_df,
                                     close, vol, min_listing_age_days=30, min_liquidity_usd=2_000_000.0,
                                     liquidity_window=30, lookback=7)
    row_trading = log[log["rebalance_date"] == d_while_trading].iloc[0]
    row_after = log[log["rebalance_date"] == d_after_delisting].iloc[0]
    assert row_trading["eligible"] == True     # éligible PENDANT sa vie réelle
    assert row_after["eligible"] == False      # plus éligible APRÈS -- jamais rétroactivement retiré du passé, juste absent du futur
    assert row_after["reason"] == "no_price_history_before_rebalance_date"


def test_pit_eligibility_log_rename_treated_as_separate_identities_not_aliased():
    """Item 2 du mandat : "rename -> mapping explicite". Vérifié en conditions
    réelles (load_listing_calendar()) : RNDRUSDT (status=DELISTED,
    onboard 2023-02-03) et RENDERUSDT (status=TRADING, onboard 2024-07-26)
    sont deux entrées SÉPARÉES du calendrier, jamais aliasées -- c'est le bon
    comportement PIT (au moment de chaque rebalance historique, c'était deux
    instruments réellement distincts avec des historiques de prix distincts,
    contrairement au cas du collecteur LIVE où fusionner l'ancien/nouveau nom
    a du sens pour une position continue). Reproduit ici en synthétique."""
    idx = pd.date_range("2022-01-01", "2025-01-01", freq="D", tz="UTC")
    rng = np.random.default_rng(2)
    close = pd.DataFrame(index=idx)
    close["OLDNAMEUSDT"] = np.nan
    close["NEWNAMEUSDT"] = np.nan
    old_mask = (idx >= pd.Timestamp("2022-01-01", tz="UTC")) & (idx < pd.Timestamp("2023-06-01", tz="UTC"))
    new_mask = idx >= pd.Timestamp("2024-01-01", tz="UTC")
    close.loc[old_mask, "OLDNAMEUSDT"] = 5.0 + np.cumsum(rng.normal(0, 1, int(old_mask.sum())))
    close.loc[new_mask, "NEWNAMEUSDT"] = 50.0 + np.cumsum(rng.normal(0, 1, int(new_mask.sum())))
    vol = pd.DataFrame(index=idx)
    vol["OLDNAMEUSDT"] = 5_000_000.0
    vol["NEWNAMEUSDT"] = 5_000_000.0
    cal = _cal([("OLDNAMEUSDT", "2022-01-01", "DELISTED"), ("NEWNAMEUSDT", "2024-01-01", "TRADING")])
    onboard_df = resolve_onboard_dates(["OLDNAMEUSDT", "NEWNAMEUSDT"], cal, close)

    d_old_era = pd.Timestamp("2023-03-01", tz="UTC")     # OLDNAMEUSDT tradait encore, NEWNAMEUSDT pas encore
    d_new_era = pd.Timestamp("2024-06-01", tz="UTC")     # inverse
    log = build_pit_eligibility_log([d_old_era, d_new_era], ["OLDNAMEUSDT", "NEWNAMEUSDT"], onboard_df,
                                     close, vol, min_listing_age_days=30, min_liquidity_usd=2_000_000.0,
                                     liquidity_window=30, lookback=7)
    old_at_old_era = log[(log["symbol"] == "OLDNAMEUSDT") & (log["rebalance_date"] == d_old_era)].iloc[0]
    new_at_old_era = log[(log["symbol"] == "NEWNAMEUSDT") & (log["rebalance_date"] == d_old_era)].iloc[0]
    old_at_new_era = log[(log["symbol"] == "OLDNAMEUSDT") & (log["rebalance_date"] == d_new_era)].iloc[0]
    new_at_new_era = log[(log["symbol"] == "NEWNAMEUSDT") & (log["rebalance_date"] == d_new_era)].iloc[0]
    assert old_at_old_era["eligible"] == True and new_at_old_era["eligible"] == False
    assert old_at_new_era["eligible"] == False and new_at_new_era["eligible"] == True
    # les deux identités gardent leur PROPRE onboard_ts, jamais fusionné
    assert old_at_old_era["onboard_ts"] != new_at_old_era["onboard_ts"]


def test_pit_eligibility_size_varies_over_time_not_flat_copy_backward():
    """The bug's signature symptom: eligible_universe_size flat/identical
    across all history (today's count copy-pasted backward). The fix must
    show it growing as more symbols clear their listing-age gate."""
    close, vol, onboard_df = _pit_panel()
    d_2020 = pd.Timestamp("2020-06-01", tz="UTC")     # only OLDUSDT could possibly be eligible
    d_2024 = pd.Timestamp("2024-12-30", tz="UTC")     # both eligible
    log = build_pit_eligibility_log([d_2020, d_2024], ["OLDUSDT", "NEWUSDT"], onboard_df, close, vol,
                                     min_listing_age_days=30, min_liquidity_usd=2_000_000.0,
                                     liquidity_window=30, lookback=7)
    summary = {pd.Timestamp(r["rebalance_date"]): r["eligible_universe_size"]
               for r in summarize_pit_log(log)}
    assert summary[d_2020] == 1
    assert summary[d_2024] == 2
    assert summary[d_2020] < summary[d_2024]


def test_mask_pre_eligibility_nans_out_pre_age_gate_rows_only():
    close, vol, onboard_df = _pit_panel()
    masked_close, masked_vol = mask_pre_eligibility(close, vol, onboard_df, min_listing_age_days=30)
    cutoff = pd.Timestamp("2024-06-01", tz="UTC") + pd.Timedelta(days=30)
    assert masked_close.loc[masked_close.index < cutoff, "NEWUSDT"].notna().sum() == 0
    assert masked_close.loc[masked_close.index >= cutoff, "NEWUSDT"].notna().sum() > 0
    # OLDUSDT (onboard 2020-01-01) loses exactly its first 30 days (its own
    # age gate) but is otherwise untouched for the rest of the window.
    old_cutoff = pd.Timestamp("2020-01-01", tz="UTC") + pd.Timedelta(days=30)
    assert masked_close.loc[masked_close.index < old_cutoff, "OLDUSDT"].notna().sum() == 0
    assert (masked_close.loc[masked_close.index >= old_cutoff, "OLDUSDT"].notna().sum()
            == close.loc[close.index >= old_cutoff, "OLDUSDT"].notna().sum())


def test_mask_pre_eligibility_unknown_onboard_masks_entire_column():
    idx = pd.date_range("2024-01-01", periods=5, freq="D", tz="UTC")
    close = pd.DataFrame({"GHOSTUSDT": [1.0] * 5}, index=idx)
    vol = pd.DataFrame({"GHOSTUSDT": [5_000_000.0] * 5}, index=idx)
    onboard_df = pd.DataFrame([{"symbol": "GHOSTUSDT", "onboard_ts": pd.NaT, "onboard_source": "unknown"}])
    masked_close, masked_vol = mask_pre_eligibility(close, vol, onboard_df, min_listing_age_days=30)
    assert masked_close["GHOSTUSDT"].notna().sum() == 0
    assert masked_vol["GHOSTUSDT"].notna().sum() == 0


def test_pit_masking_feeds_correctly_into_build_weekly_decisions_no_lookahead():
    """Integration: once masked, signal.py's OWN (untouched) pipeline must
    never rank NEWUSDT at a rebalance before its real listing+age gate."""
    close, vol, onboard_df = _pit_panel()
    masked_close, masked_vol = mask_pre_eligibility(close, vol, onboard_df, min_listing_age_days=30)
    dec = build_weekly_decisions(masked_close, masked_vol, min_liquidity_usd=1_000_000.0,
                                  top_fraction=1.0, lookback=7, liquidity_window=30,
                                  rebalance_weekday=0)
    early_new = dec[(dec["symbol"] == "NEWUSDT") & (dec["event_time"] < pd.Timestamp("2024-06-01", tz="UTC"))]
    assert early_new.empty


def test_write_and_summarize_pit_log_roundtrip(tmp_path):
    close, vol, onboard_df = _pit_panel()
    log = build_pit_eligibility_log([pd.Timestamp("2024-12-30", tz="UTC")], ["OLDUSDT", "NEWUSDT"],
                                     onboard_df, close, vol, min_listing_age_days=30,
                                     min_liquidity_usd=2_000_000.0, liquidity_window=30, lookback=7)
    out_path = tmp_path / "pit_universe_log.parquet"
    write_pit_universe_log(log, out_path)
    assert out_path.exists()
    reread = pd.read_parquet(out_path)
    assert len(reread) == len(log)

    summary = summarize_pit_log(log)
    assert len(summary) == 1
    assert summary[0]["eligible_universe_size"] == len(summary[0]["selected_universe"])
    assert set(summary[0]["selected_universe"]) | set(summary[0]["rejected_symbols"]) == {"OLDUSDT", "NEWUSDT"}


def test_summarize_pit_log_empty_input():
    assert summarize_pit_log(pd.DataFrame()) == []


def test_build_pit_eligibility_log_empty_input():
    out = build_pit_eligibility_log([], [], pd.DataFrame(), pd.DataFrame(), pd.DataFrame())
    assert out.empty
    assert list(out.columns) == ["rebalance_date", "symbol", "onboard_ts", "onboard_source",
                                  "age_days", "tret_7d", "liquidity_usd_30d", "eligible", "reason"]


# ── signal.py — causal math (same shape as V1, own MIN_LIQUIDITY_USD) ──────

def test_min_liquidity_usd_differs_from_v1_by_design():
    """V2's threshold is a deliberate, documented judgment call (2x V1's
    $1M floor, see signal.py docstring) -- not accidentally identical."""
    from src.institutional.engines.cross_sectional_momentum_live.signal import (
        MIN_LIQUIDITY_USD as V1_MIN_LIQUIDITY_USD)
    assert MIN_LIQUIDITY_USD == pytest.approx(2_000_000.0)
    assert MIN_LIQUIDITY_USD != V1_MIN_LIQUIDITY_USD


def test_trailing_return_basic():
    close = pd.Series([100.0, 101, 102, 103, 104, 105, 106, 110.0])
    tret = trailing_return(close, lookback=7)
    assert tret.iloc[:7].isna().all()
    assert tret.iloc[7] == pytest.approx(110.0 / 100.0 - 1.0)


def test_trailing_return_no_lookahead():
    close = pd.Series([100.0, 101, 102, 103, 104, 105, 106, 107, 108, 109])
    r_before = trailing_return(close, lookback=7)
    close_future_spike = close.copy()
    close_future_spike.iloc[-1] = 999.0
    r_after = trailing_return(close_future_spike, lookback=7)
    pd.testing.assert_series_equal(r_before.iloc[:-1], r_after.iloc[:-1])


def test_trailing_return_empty_input():
    out = trailing_return(pd.Series([], dtype="float64"), lookback=7)
    assert out.empty


def test_trailing_liquidity_requires_full_window():
    vol = pd.Series([1_000_000.0] * 40)
    liq = trailing_liquidity_usd(vol, window=30)
    assert liq.iloc[:29].isna().all()
    assert not pd.isna(liq.iloc[29])


def test_trailing_liquidity_no_lookahead():
    vol = pd.Series([1.0] * 35)
    l_before = trailing_liquidity_usd(vol, window=30)
    vol_spike = vol.copy()
    vol_spike.iloc[-1] = 1e9
    l_after = trailing_liquidity_usd(vol_spike, window=30)
    pd.testing.assert_series_equal(l_before.iloc[:-1], l_after.iloc[:-1])


def test_trailing_liquidity_empty_input():
    out = trailing_liquidity_usd(pd.Series([], dtype="float64"), window=30)
    assert out.empty


# ── select_top_bucket ───────────────────────────────────────────────────────

def test_select_top_bucket_filters_illiquid_at_v2_threshold():
    """At V2's $2M floor, a name at $1.5M (which would have PASSED V1's $1M
    floor) is correctly excluded here -- the whole point of the higher
    threshold."""
    tret = pd.Series({"A": 0.10, "B": 0.20, "C": 0.30})
    liq = pd.Series({"A": 2_500_000.0, "B": 1_500_000.0, "C": 2_500_000.0})
    picked = select_top_bucket(tret, liq, min_liquidity_usd=MIN_LIQUIDITY_USD, top_fraction=1.0)
    assert set(picked["symbol"]) == {"A", "C"}
    assert picked["n_eligible"].iloc[0] == 2


def test_select_top_bucket_ranks_descending_and_picks_top():
    tret = pd.Series({"A": 0.01, "B": 0.05, "C": 0.20, "D": 0.15, "E": 0.02})
    liq = pd.Series({k: 5_000_000.0 for k in tret.index})
    picked = select_top_bucket(tret, liq, min_liquidity_usd=1_000_000.0, top_fraction=0.20)
    assert len(picked) == 1
    assert picked["symbol"].iloc[0] == "C"
    assert picked["n_eligible"].iloc[0] == 5


def test_select_top_bucket_ceil_rounding_on_much_larger_cohort():
    """V2's whole premise is a much larger eligible cohort than V1's ~49 —
    sanity-check the same ceil-rounding rule scales correctly at n=101."""
    tret = pd.Series({f"S{i}": float(i) for i in range(101)})
    liq = pd.Series({f"S{i}": 5_000_000.0 for i in range(101)})
    picked = select_top_bucket(tret, liq, min_liquidity_usd=1_000_000.0, top_fraction=TOP_FRACTION)
    # ceil(0.20 * 101) == 21
    assert len(picked) == 21
    assert picked["symbol"].iloc[0] == "S100"   # highest tret first


def test_select_top_bucket_minimum_one_when_nonempty():
    tret = pd.Series({"ONLY": 0.05})
    liq = pd.Series({"ONLY": 5_000_000.0})
    picked = select_top_bucket(tret, liq, min_liquidity_usd=1_000_000.0, top_fraction=0.20)
    assert len(picked) == 1
    assert picked["symbol"].iloc[0] == "ONLY"


def test_select_top_bucket_excludes_nan_return():
    tret = pd.Series({"A": np.nan, "B": 0.10})
    liq = pd.Series({"A": 5_000_000.0, "B": 5_000_000.0})
    picked = select_top_bucket(tret, liq, min_liquidity_usd=1_000_000.0, top_fraction=1.0)
    assert list(picked["symbol"]) == ["B"]


def test_select_top_bucket_empty_when_nothing_eligible():
    tret = pd.Series({"A": 0.10, "B": 0.20})
    liq = pd.Series({"A": 100.0, "B": 200.0})
    picked = select_top_bucket(tret, liq, min_liquidity_usd=MIN_LIQUIDITY_USD, top_fraction=0.20)
    assert picked.empty
    assert list(picked.columns) == ["symbol", "tret_7d", "liquidity_usd_30d",
                                     "pct_rank", "rank_in_bucket", "n_eligible"]


def test_select_top_bucket_empty_input():
    picked = select_top_bucket(pd.Series(dtype="float64"), pd.Series(dtype="float64"))
    assert picked.empty


# ── weekly_rebalance_dates ──────────────────────────────────────────────────

def test_weekly_rebalance_dates_picks_only_target_weekday():
    dates = pd.date_range("2026-01-01", "2026-01-31", freq="D", tz="UTC")
    mondays = weekly_rebalance_dates(dates, weekday=0)
    assert all(d.weekday() == 0 for d in mondays)
    assert len(mondays) == 4


def test_weekly_rebalance_dates_empty_input():
    assert weekly_rebalance_dates(pd.DatetimeIndex([]), weekday=0) == []


# ── build_weekly_decisions (full pipeline) ──────────────────────────────────

def _make_panel(n_days=60, symbols=("A", "B", "C", "D", "E"), start="2026-01-01", seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=n_days, freq="D", tz="UTC")
    close = pd.DataFrame(
        {s: 100.0 * np.cumprod(1 + rng.normal(0, 0.01, n_days)) for s in symbols}, index=idx)
    vol = pd.DataFrame({s: 5_000_000.0 for s in symbols}, index=idx)
    return close, vol


def test_build_weekly_decisions_empty_panel():
    out = build_weekly_decisions(pd.DataFrame(), pd.DataFrame())
    assert out.empty
    assert list(out.columns) == ["event_time", "symbol", "tret_7d", "liquidity_usd_30d",
                                  "pct_rank", "rank_in_bucket", "n_eligible_universe", "direction"]


def test_build_weekly_decisions_long_only():
    close, vol = _make_panel()
    out = build_weekly_decisions(close, vol, min_liquidity_usd=1_000_000.0, top_fraction=0.40)
    if not out.empty:
        assert (out["direction"] == "LONG").all()


def test_build_weekly_decisions_only_on_rebalance_weekday():
    close, vol = _make_panel(n_days=40)
    out = build_weekly_decisions(close, vol, min_liquidity_usd=1_000_000.0,
                                  top_fraction=1.0, rebalance_weekday=0)
    if not out.empty:
        assert all(pd.Timestamp(t).weekday() == 0 for t in out["event_time"].unique())


def test_build_weekly_decisions_respects_v2_liquidity_threshold():
    close, vol = _make_panel(symbols=("A", "B"))
    vol["B"] = 1_500_000.0   # would pass V1's $1M floor, must fail V2's $2M floor
    out = build_weekly_decisions(close, vol, min_liquidity_usd=MIN_LIQUIDITY_USD,
                                  top_fraction=1.0, liquidity_window=30)
    assert "B" not in set(out["symbol"]) if not out.empty else True


def test_build_weekly_decisions_no_symbols_outside_input_panel_appear():
    close, vol = _make_panel(symbols=("A", "B", "C"))
    out = build_weekly_decisions(close, vol, min_liquidity_usd=1_000_000.0, top_fraction=0.5)
    if not out.empty:
        assert set(out["symbol"]).issubset({"A", "B", "C"})


def test_build_weekly_decisions_insufficient_history_yields_no_rows():
    close, vol = _make_panel(n_days=5)
    out = build_weekly_decisions(close, vol)
    assert out.empty


# ── runner script: fail-closed registry checks ──────────────────────────────

def test_universe_hash_deterministic():
    from scripts.run_cross_sectional_momentum_live_v2_shadow import universe_hash
    a = universe_hash(["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    b = universe_hash(["SOLUSDT", "BTCUSDT", "ETHUSDT"])   # order-independent
    assert a == b
    c = universe_hash(["BTCUSDT", "ETHUSDT"])
    assert a != c


def test_check_registry_freeze_fails_closed_for_unknown_alpha():
    from scripts.run_cross_sectional_momentum_live_v2_shadow import check_registry_freeze
    with pytest.raises(RuntimeError, match="absent"):
        check_registry_freeze("NOT_A_REAL_ALPHA_ID")


def test_check_registry_freeze_passes_for_signal_shadow_entry():
    from scripts.run_cross_sectional_momentum_live_v2_shadow import check_registry_freeze
    # CROSS_SECTIONAL_MOMENTUM_LIVE_V2 must be SIGNAL_SHADOW in the registry
    # for this test to pass -- i.e. this also guards against the registry
    # entry accidentally regressing to a non-writable operational_status.
    check_registry_freeze("CROSS_SECTIONAL_MOMENTUM_LIVE_V2")


def test_check_registry_freeze_fails_closed_for_data_blocked_alpha():
    from scripts.run_cross_sectional_momentum_live_v2_shadow import check_registry_freeze
    # CROSS_SECTIONAL_MOMENTUM_PIT_V1 is explicitly DATA_BLOCKED -- untouched
    # sibling entry, never expected to flip to SIGNAL_SHADOW by this script.
    with pytest.raises(RuntimeError, match="status="):
        check_registry_freeze("CROSS_SECTIONAL_MOMENTUM_PIT_V1")


def test_check_registry_freeze_v1_entry_untouched():
    """V1's own entry must still be exactly SIGNAL_SHADOW and readable —
    this test would fail if V2's build had accidentally mutated V1's block."""
    from scripts.run_cross_sectional_momentum_live_v2_shadow import check_registry_freeze
    check_registry_freeze("CROSS_SECTIONAL_MOMENTUM_LIVE_V1")
