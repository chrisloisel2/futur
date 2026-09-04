#!/usr/bin/env python3
"""
scripts/replay_forward_window_before_after.py
─────────────────────────────────────────────────────────────────────────────
Rejoue la fenêtre forward 2026-09-03 15:43 -> 2026-09-04 18:19 UTC sur les
MÊMES décisions et les MÊMES horodatages de cycle, sous plusieurs régimes de
code, et compare. Sert à chiffrer l'effet des correctifs P0.1/P0.2/P0.3 sans
rien changer aux ledgers réels (tout est écrit dans un dossier scratch).

Trois scénarios :

  OLD              comportement d'avant l'audit : aucune porte de validation,
                   aucune porte score_net, aucune bande (tout delta >= 1 µ€
                   part), dénominateur de budget recalculé à chaque cycle.
  P03_ONLY         bande + dénominateur à cliquet activés, mais les portes de
                   capital DÉSACTIVÉES -- isole l'effet turnover sur le même
                   alpha, à décisions identiques.
  ALL_GATES        état livré : P0.1 + P0.2 + P0.3.

⚠ Causalité. À chaque cycle, seules les décisions dont l'horodatage est <= au
timestamp du cycle sont visibles. Rejouer avec le ledger complet donnerait au
portefeuille des décisions qui n'existaient pas encore.

Aucune écriture hors du dossier scratch. Ne modifie aucun état de production.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import src.institutional.live_alpha_lab.portfolio as portfolio_mod
from src.institutional.live_alpha_lab.eligibility import is_forward_eligible, load_validation_index
from src.institutional.live_alpha_lab.intents import (
    NOT_A_POSITION_ALPHA, build_intents, filter_negative_expected_value)
from src.institutional.live_alpha_lab.portfolio import aggregate, step
from src.institutional.live_alpha_lab.portfolio_config import ALL_PORTFOLIOS

LAB = ROOT / "reports" / "live_alpha_lab"

# ── préchargement des séries de marché ───────────────────────────────────
# `get_mark(instr, as_of)` = « dernière observation de derivatives_raw dont le
# timestamp est <= as_of », et `_latest_funding_rate` fait pareil sur le
# funding. Les deux relisent l'INTÉGRALITÉ des parquets d'open interest du
# symbole À CHAQUE APPEL : ~1 s par (instrument, cycle), soit ~1 h par
# scénario. On charge donc chaque série UNE fois, puis on répond par
# recherche dichotomique.
#
# Strictement équivalent : même source, même filtre `timestamp <= as_of`,
# même « dernière ligne retenue ». Le jeu de fichiers chargé (celui du as_of
# MAXIMAL) contient par construction tous ceux qu'un as_of antérieur aurait
# sélectionnés, et le filtre en mémoire refait exactement le même tri.
_SERIES: dict = {}


def _load_series(symbol: str, max_as_of):
    from src.institutional.live_alpha_lab.marks import _oi_base, eligible_files_for_as_of
    if symbol in _SERIES:
        return _SERIES[symbol]
    files = eligible_files_for_as_of(_oi_base(symbol), max_as_of)
    frames = []
    for f in files:
        for cols in (["timestamp", "mark_price", "open_interest", "funding_rate"],
                     ["timestamp", "mark_price", "open_interest"]):
            try:
                frames.append(pd.read_parquet(f, columns=cols))
                break
            except Exception:
                continue
    if not frames:
        _SERIES[symbol] = None
        return None
    df = pd.concat(frames, ignore_index=True)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.sort_values("timestamp").reset_index(drop=True)
    _SERIES[symbol] = df
    return df


def install_preloaded_market_reads(symbols, max_as_of) -> None:
    from src.institutional.live_alpha_lab.marks import MarkQuote
    for s in symbols:
        _load_series(s, max_as_of)

    def fast_mark(instrument, as_of=None):
        if instrument.endswith("_QUARTERLY"):
            return None
        df = _load_series(instrument.replace("_PERP", ""), max_as_of)
        if df is None or as_of is None:
            return None
        i = df["timestamp"].searchsorted(as_of, side="right") - 1
        if i < 0:
            return None
        row = df.iloc[i]
        price = float(row["mark_price"])
        oi = row.get("open_interest")
        liq = float(oi) * price if pd.notna(oi) and oi > 0 else None
        ts = pd.Timestamp(row["timestamp"])
        return MarkQuote(instrument=instrument.replace("_PERP", ""), price=price,
                         mark_source="DERIVATIVES_RAW_MARK", mark_timestamp=ts,
                         mark_age_ms=(as_of - ts).total_seconds() * 1000,
                         liquidity_notional=liq)

    def fast_funding(symbol, as_of):
        df = _load_series(symbol, max_as_of)
        if df is None or "funding_rate" not in df.columns:
            return None
        i = df["timestamp"].searchsorted(as_of, side="right") - 1
        if i < 0:
            return None
        v = df.iloc[i]["funding_rate"]
        return float(v) if pd.notna(v) else None

    portfolio_mod.get_mark = fast_mark
    portfolio_mod._latest_funding_rate = fast_funding


REGISTRY = ROOT / "configs" / "live_alpha_registry.yaml"
WINDOW_START = pd.Timestamp("2026-09-03T15:43:37+00:00")

# colonne d'horodatage de décision selon le schéma de chaque ledger
TS_COLUMNS = ("timestamp", "event_time", "date")


def _decision_ts(df: pd.DataFrame) -> pd.Series:
    for c in TS_COLUMNS:
        if c in df.columns:
            return pd.to_datetime(df[c], utc=True)
    raise KeyError("aucune colonne d'horodatage reconnue")


def load_forward(alpha_id: str) -> pd.DataFrame:
    p = LAB / alpha_id / "decisions.parquet"
    if not p.exists():
        return pd.DataFrame()
    df = pd.read_parquet(p)
    if "provenance" not in df.columns:
        return pd.DataFrame()
    return df[df["provenance"] == "FORWARD_LIVE"].copy()


def cycle_timestamps() -> list:
    """Les horodatages RÉELS des cycles de la fenêtre, relus de la courbe
    d'équity de production (jamais une grille régulière inventée)."""
    state = json.loads((LAB / "portfolios" / "P1_CONTROL" / "state.json").read_text())
    out = [pd.Timestamp(e["ts"]) for e in state["equity_curve"]]
    return [t for t in out if t >= WINDOW_START]


def run_scenario(name: str, scratch: Path, cycles: list, portfolio: str,
                 apply_gates: bool, apply_band: bool) -> dict:
    reg = yaml.safe_load(REGISTRY.read_text())
    by_id = {a["alpha_id"]: a for a in reg["alphas"]}
    index = load_validation_index()
    config = ALL_PORTFOLIOS[portfolio]

    root = scratch / name
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    portfolio_mod.PORTFOLIO_DIR = root

    # bande neutralisée = comportement d'avant (tout delta part)
    real_band = portfolio_mod.no_trade_band_fraction
    if not apply_band:
        portfolio_mod.no_trade_band_fraction = lambda *_a, **_k: 0.0

    ledgers = {}
    for alpha_id in by_id:
        if alpha_id in NOT_A_POSITION_ALPHA:
            continue
        df = load_forward(alpha_id)
        if not df.empty:
            ledgers[alpha_id] = df

    blocked_by_validation, blocked_by_score_net = {}, 0
    state = None
    try:
        for ts in cycles:
            intents = []
            for alpha_id, df in ledgers.items():
                entry = by_id[alpha_id]
                if apply_gates:
                    v = is_forward_eligible(entry, index)
                    if not v.eligible:
                        blocked_by_validation[alpha_id] = v.reason.value
                        continue
                visible = df[_decision_ts(df) <= ts]      # causalité stricte
                if visible.empty:
                    continue
                if apply_gates:
                    stats = {}
                    intents.extend(build_intents(alpha_id, entry, visible, stats=stats))
                    blocked_by_score_net = max(blocked_by_score_net,
                                               stats.get("n_blocked_negative_ev", 0))
                else:
                    # chemin d'avant : la porte score_net n'existait pas
                    kept = visible
                    fn = portfolio_mod  # noqa: F841  (lisibilité)
                    from src.institutional.live_alpha_lab.intents import ADAPTERS
                    if alpha_id in ADAPTERS:
                        intents.extend(ADAPTERS[alpha_id](
                            alpha_id, entry.get("family"), entry.get("risk_bucket"),
                            entry.get("correlation_family"), kept))
            if len(state.equity_curve) % 20 == 0 if state else False:
                print(f"[replay]   {name}: cycle {len(state.equity_curve)}/{len(cycles)}", flush=True)
            hw = dict(state.alpha_denominator_high_water) if (state and apply_band) else None
            agg = aggregate(intents, config, set(), as_of=ts, denominator_high_water=hw)
            state = step(portfolio, config, agg, ts)
    finally:
        portfolio_mod.no_trade_band_fraction = real_band

    last = state.equity_curve[-1] if state and state.equity_curve else {}
    slippage = sum(abs(o["filled_quantity"] * (o["fill_price"] - o["mark_price_at_decision"]))
                   for o in (state.orders if state else [])
                   if o.get("mark_price_at_decision") and o.get("fill_price"))
    turn_class = dict(state.cumulative_turnover_by_class) if state else {}
    mech = turn_class.get("MECHANICAL_RESIZE", 0.0)
    return {
        "scenario": name,
        "n_cycles": len(cycles),
        "n_orders": len(state.orders) if state else 0,
        "turnover_usd": state.cumulative_turnover_usd if state else 0.0,
        "turnover_by_class": turn_class,
        "mechanical_turnover_usd": mech,
        "suppressed_turnover_usd": state.suppressed_turnover_usd if state else 0.0,
        "suppressed_order_count": state.suppressed_order_count if state else 0,
        "fees_usd": state.cumulative_fees_usd if state else 0.0,
        "slippage_usd": slippage,
        "funding_usd": state.cumulative_funding_usd if state else 0.0,
        "realized_pnl": state.cumulative_realized_pnl if state else 0.0,
        "unrealized_pnl": last.get("unrealized_pnl", 0.0),
        "equity": last.get("equity", config.capital_eur),
        "gross_pnl_before_frictions": (
            last.get("equity", config.capital_eur) - config.capital_eur
            + (state.cumulative_fees_usd if state else 0.0) + slippage),
        "n_positions_final": last.get("n_positions", 0),
        "blocked_by_validation": blocked_by_validation,
        "blocked_by_score_net_max_per_cycle": blocked_by_score_net,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--portfolio", default="P1_CONTROL")
    ap.add_argument("--scratch", default="/tmp/replay_before_after")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    cycles = cycle_timestamps()
    print(f"[replay] {len(cycles)} cycles réels, {cycles[0]} -> {cycles[-1]}", flush=True)
    symbols = set()
    for a in yaml.safe_load(REGISTRY.read_text())["alphas"]:
        df = load_forward(a["alpha_id"])
        for col in ("asset", "symbol"):
            if not df.empty and col in df.columns:
                symbols |= set(df[col].dropna().unique())
    print(f"[replay] préchargement de {len(symbols)} séries de marché…", flush=True)
    install_preloaded_market_reads(sorted(symbols), cycles[-1])
    print("[replay] préchargement terminé", flush=True)
    scratch = Path(args.scratch)
    results = []
    for name, gates, band in (("OLD", False, False),
                              ("P03_ONLY", False, True),
                              ("ALL_GATES", True, True)):
        print(f"[replay] scénario {name}…", flush=True)
        results.append(run_scenario(name, scratch, cycles, args.portfolio, gates, band))
        r = results[-1]
        print(f"[replay]   ordres={r['n_orders']:5d} turnover={r['turnover_usd']:>12,.0f} "
              f"frais={r['fees_usd']:>8,.2f} equity={r['equity']:>12,.2f}", flush=True)

    out = Path(args.out) if args.out else scratch / "RESULTS.json"
    out.write_text(json.dumps(results, indent=2, default=str))
    print(f"[replay] -> {out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
