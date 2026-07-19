#!/usr/bin/env python3
"""Exécution UNIQUE du protocole FUNDING_XVENUE v0 — reports/FUNDING_XVENUE_PROTOCOL.md.

Tous les paramètres sont figés dans PARAMS (gel 2026-07-18). Aucun re-tuning
après lecture des résultats : FAIL sur un critère => NO_EDGE définitif v0.
Sortie : reports/FUNDING_XVENUE_V0_VERDICT.json + tableaux console.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "derivatives_backfill"
ENRICHED = ROOT / "data" / "enriched"
OUT = ROOT / "reports" / "FUNDING_XVENUE_V0_VERDICT.json"

PARAMS = {
    "coins": {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT"},
    "window": ["2024-01-01", "2026-06-28"],
    "lookback": 21,            # settlements (7 j)
    "theta_in_ann": 4.0,       # %/an
    "theta_out_ann": 1.0,      # %/an (= theta_in/4)
    "neighborhood": {"lookbacks": [9, 21, 42], "theta_ins": [2.0, 4.0, 6.0]},
    # coûts ×1 (bp du notional N par jambe ; capital requis = N à levier 2×)
    "fee_binance_bp": 5.0, "fee_hl_bp": 5.0, "fee_bybit_bp": 5.5,
    "slippage_bp": 2.0, "basis_rt_bp": 3.0, "drag_bp_month": 1.0,
    "liq_adverse_move": 0.35,  # par jambe, entre égalisations hebdo (168 h)
    "max_rt_per_year": 26.0,
    "floor_trailing_ann_x1": 3.0,   # %/an, critère 3
    "trailing_year": ["2025-07-01", "2026-06-28"],
    "stress_mult": 0.75,
    "concentration_max": 0.50,
}

ANN = 3 * 365 / 100.0  # bp/8h -> %/an


def load_funding(venue: str, name: str) -> pd.Series:
    df = pd.read_parquet(DATA / venue / "funding" / f"{name}.parquet")
    ts = pd.to_datetime(df["timestamp"], utc=True)
    return pd.Series(df["funding_rate"].values, index=ts).sort_index()


def build_differential(coin: str, sym: str, high_venue: str = "hyperliquid") -> pd.Series:
    """d_t en bp/8h par intervalle entre deux settlements Binance consécutifs.

    high_venue='hyperliquid' : somme des taux horaires HL dans (t-, t].
    high_venue='bybit'       : jointure sur settlements arrondis (même cadence 8h).
    """
    bn = load_funding("binance", sym)
    bn.index = bn.index.round("1h")
    lo, hi = pd.Timestamp(PARAMS["window"][0], tz="UTC"), pd.Timestamp(PARAMS["window"][1], tz="UTC") + pd.Timedelta("1d")
    bn = bn[(bn.index >= lo) & (bn.index <= hi)]
    if high_venue == "bybit":
        by = load_funding("bybit", sym)
        by.index = by.index.round("1h")
        j = pd.DataFrame({"hi": by, "lo": bn}).dropna()
        return (j["hi"] - j["lo"]) * 1e4
    hl = load_funding("hyperliquid", coin)
    bn = bn[bn.index >= hl.index.min()]
    cum = hl.cumsum()
    pos = cum.index.searchsorted(bn.index, side="right") - 1
    vals = np.where(pos >= 0, cum.values[np.maximum(pos, 0)], 0.0)
    win = np.diff(vals, prepend=0.0)
    d = pd.Series(win[1:] - bn.values[1:], index=bn.index[1:]) * 1e4
    gaps = d.index.to_series().diff().dt.total_seconds().div(3600).dropna()
    assert gaps.median() == 8.0, f"{coin}: cadence médiane {gaps.median()} != 8h"
    assert not d.isna().any(), f"{coin}: NaN dans le différentiel"
    return d


def run_rule(d: pd.Series, lookback: int, theta_in: float, theta_out: float,
             cost_mult: float, rt_cost_x1: float, stress_mult: float = 1.0) -> dict:
    """Règle figée §4 : hystérèse sur S annualisé, position décalée d'un settlement."""
    d = d * stress_mult
    s = d.rolling(lookback).mean() * ANN
    pos = np.zeros(len(d), dtype=int)
    p = 0
    for i, v in enumerate(s.values):
        if np.isnan(v):
            p = 0
        elif p == 0:
            p = int(np.sign(v)) if abs(v) >= theta_in else 0
        elif abs(v) <= theta_out or (np.sign(v) != p and abs(v) >= theta_in):
            p = int(np.sign(v)) if (np.sign(v) != p and abs(v) >= theta_in) else 0
        pos[i] = p
    pos_s = pd.Series(pos, index=d.index)
    held = pos_s.shift(1).fillna(0)
    accrual = held * d
    turns = pos_s.diff().abs().fillna(pos_s.abs())            # 1 = entrée/sortie, 2 = flip
    side_cost = rt_cost_x1 / 2.0 * cost_mult
    drag = PARAMS["drag_bp_month"] * (8.0 / 730.0) * cost_mult
    costs = turns * side_cost + held.abs() * drag
    net = accrual - costs
    n_entries = int(((pos_s != 0) & (pos_s.shift(1).fillna(0) == 0)).sum())
    n_flips = int(((pos_s * pos_s.shift(1)) < 0).sum())
    return {"net": net, "pos": pos_s, "held": held,
            "rt": n_entries + n_flips, "time_in_pos": float(held.abs().mean())}


def annualized(net: pd.Series) -> float:
    if len(net) == 0:
        return 0.0
    years = len(net) * 8.0 / (24 * 365)
    return float(net.sum() / 100.0 / years)


def liq_check(coin_sym: str, pos: pd.Series) -> dict:
    """Pire mouvement adverse par jambe entre égalisations hebdo (closes 1h)."""
    px = pd.read_parquet(ENRICHED / f"{coin_sym}_1h_enriched.parquet",
                         columns=["datetime", "close"])
    close = pd.Series(px["close"].values,
                      index=pd.to_datetime(px["datetime"], utc=True)).sort_index()
    worst = 0.0
    in_pos = pos[pos != 0]
    if len(in_pos) == 0:
        return {"worst_weekly_move": 0.0, "liquidated": False}
    blocks = (pos != pos.shift(1)).cumsum()
    for _, seg in pos.groupby(blocks):
        if seg.iloc[0] == 0:
            continue
        start, end = seg.index[0], seg.index[-1] + pd.Timedelta("8h")
        window = close[(close.index >= start) & (close.index <= end)]
        if len(window) < 2:
            continue
        anchors = pd.date_range(start, end, freq="168h", tz="UTC")
        for a0, a1 in zip(anchors, list(anchors[1:]) + [end]):
            w = window[(window.index >= a0) & (window.index <= a1)]
            if len(w) < 2:
                continue
            move = float((w / w.iloc[0] - 1).abs().max())
            worst = max(worst, move)
    return {"worst_weekly_move": round(worst, 4),
            "liquidated": bool(worst >= PARAMS["liq_adverse_move"])}


def main() -> None:
    p = PARAMS
    rt_hl = 2 * (p["fee_binance_bp"] + p["slippage_bp"]) + 2 * (p["fee_hl_bp"] + p["slippage_bp"]) + p["basis_rt_bp"]
    rt_by = 2 * (p["fee_binance_bp"] + p["slippage_bp"]) + 2 * (p["fee_bybit_bp"] + p["slippage_bp"]) + p["basis_rt_bp"]

    diffs = {c: build_differential(c, s) for c, s in p["coins"].items()}
    common = None
    for d in diffs.values():
        common = d.index if common is None else common.intersection(d.index)
    assert len(common) >= 2500, f"couverture insuffisante : {len(common)} settlements communs"
    diffs = {c: d.reindex(common) for c, d in diffs.items()}

    per_coin, nets_x1, nets_x2 = {}, {}, {}
    for coin, d in diffs.items():
        r1 = run_rule(d, p["lookback"], p["theta_in_ann"], p["theta_out_ann"], 1.0, rt_hl)
        r2 = run_rule(d, p["lookback"], p["theta_in_ann"], p["theta_out_ann"], 2.0, rt_hl)
        years = len(d) * 8.0 / (24 * 365)
        liq = liq_check(p["coins"][coin], r1["pos"])
        nets_x1[coin], nets_x2[coin] = r1["net"], r2["net"]
        per_coin[coin] = {
            "net_ann_x1": round(annualized(r1["net"]), 3),
            "net_ann_x2": round(annualized(r2["net"]), 3),
            "gross_ann": round(annualized((r1["pos"].shift(1).fillna(0) * d)), 3),
            "rt_per_year": round(r1["rt"] / years, 1),
            "time_in_pos": round(r1["time_in_pos"], 3),
            **liq,
        }

    agg_x1 = pd.concat(nets_x1.values(), axis=1).mean(axis=1)
    agg_x2 = pd.concat(nets_x2.values(), axis=1).mean(axis=1)

    sub_x2 = {str(y): round(annualized(agg_x2[agg_x2.index.year == y]), 3)
              for y in (2024, 2025, 2026)}
    t0, t1 = (pd.Timestamp(t, tz="UTC") for t in p["trailing_year"])
    trail_x1 = annualized(agg_x1[(agg_x1.index > t0) & (agg_x1.index <= t1 + pd.Timedelta("1d"))])
    trail_x2 = annualized(agg_x2[(agg_x2.index > t0) & (agg_x2.index <= t1 + pd.Timedelta("1d"))])

    daily = agg_x1.resample("1d").sum()
    total = float(agg_x1.sum())
    conc = float(daily.rolling(30).sum().max() / total) if total > 0 else float("inf")

    stress = {c: annualized(run_rule(d, p["lookback"], p["theta_in_ann"], p["theta_out_ann"],
                                     1.0, rt_hl, p["stress_mult"])["net"])
              for c, d in diffs.items()}
    stress_agg = float(np.mean(list(stress.values())))

    grid = {}
    for lb in p["neighborhood"]["lookbacks"]:
        for th in p["neighborhood"]["theta_ins"]:
            cell_x1 = np.mean([annualized(run_rule(d, lb, th, th / 4, 1.0, rt_hl)["net"]) for d in diffs.values()])
            cell_x2 = np.mean([annualized(run_rule(d, lb, th, th / 4, 2.0, rt_hl)["net"]) for d in diffs.values()])
            grid[f"lb{lb}_th{th}"] = {"x1": round(float(cell_x1), 3), "x2": round(float(cell_x2), 3)}
    n_cells_pos = sum(1 for v in grid.values() if v["x2"] > 0)
    worst_cell_x1 = min(v["x1"] for v in grid.values())

    # — secondaire Binance↔Bybit (non gating) —
    bybit = {}
    for coin, sym in p["coins"].items():
        try:
            db = build_differential(coin, sym, high_venue="bybit")
            rb = run_rule(db, p["lookback"], p["theta_in_ann"], p["theta_out_ann"], 2.0, rt_by)
            bybit[coin] = {"net_ann_x2": round(annualized(rb["net"]), 3), "n": len(db)}
        except Exception as e:  # noqa: BLE001 — secondaire, jamais bloquant
            bybit[coin] = {"error": str(e)}

    # — secondaire corrélations moteurs existants (non gating) —
    corr = {}
    for name in ("v12_equity_daily", "basis_term_equity_daily"):
        try:
            eq = pd.read_parquet(ROOT / "reports" / "liq_cascade" / f"{name}.parquet")
            ser = pd.Series(eq["equity"].values,
                            index=pd.to_datetime(eq["date"], utc=True)).sort_index()
            j = pd.DataFrame({"a": daily, "b": ser.pct_change()}).dropna()
            corr[name] = round(float(j["a"].corr(j["b"])), 3) if len(j) > 60 else None
        except Exception as e:  # noqa: BLE001
            corr[name] = f"error: {e}"

    crit = {
        "P1_full_x2_2of3": sum(1 for v in per_coin.values() if v["net_ann_x2"] > 0) >= 2,
        "P2_subperiods_x2": all(v > 0 for v in sub_x2.values()),
        "P3_trailing": trail_x1 >= p["floor_trailing_ann_x1"] and trail_x2 > 0,
        "P4_no_liquidation": not any(v["liquidated"] for v in per_coin.values()),
        "P5_churn": all(v["rt_per_year"] <= p["max_rt_per_year"] for v in per_coin.values()),
        "P6_concentration": conc <= p["concentration_max"],
        "P7_stress": stress_agg >= 0,
        "P8_neighborhood": n_cells_pos >= 6 and worst_cell_x1 >= -2.0,
    }
    verdict = "PASS" if all(crit.values()) else "NO_EDGE"

    report = {
        "test": "FUNDING_XVENUE_V0", "date": "2026-07-18",
        "protocol": "reports/FUNDING_XVENUE_PROTOCOL.md",
        "params": {k: v for k, v in p.items() if k != "coins"},
        "rt_cost_bp_x1": {"binance_hl": rt_hl, "binance_bybit": rt_by},
        "n_settlements": len(common),
        "per_coin": per_coin,
        "aggregate": {"net_ann_x1": round(annualized(agg_x1), 3),
                      "net_ann_x2": round(annualized(agg_x2), 3),
                      "subperiods_x2": sub_x2,
                      "trailing_year": {"x1": round(trail_x1, 3), "x2": round(trail_x2, 3)},
                      "concentration_30d": round(conc, 3) if np.isfinite(conc) else None,
                      "stress_x075_x1": round(stress_agg, 3)},
        "neighborhood": grid,
        "criteria": crit, "verdict": verdict,
        "secondary_bybit_x2": bybit,
        "secondary_corr_daily": corr,
    }
    OUT.write_text(json.dumps(report, indent=2))

    print(f"=== FUNDING_XVENUE_V0 — verdict : {verdict} ===")
    for coin, v in per_coin.items():
        print(f"{coin}: net ×1 {v['net_ann_x1']:+.2f}%/an  ×2 {v['net_ann_x2']:+.2f}%/an  "
              f"brut {v['gross_ann']:+.2f}  RT/an {v['rt_per_year']}  "
              f"tps en pos {v['time_in_pos']:.0%}  pire move hebdo {v['worst_weekly_move']:.1%}")
    print(f"agrégat ×2 par période : {sub_x2} | année glissante ×1 {trail_x1:+.2f}%/an ×2 {trail_x2:+.2f}%/an")
    print(f"stress ×0,75 (×1) : {stress_agg:+.2f}%/an | concentration 30 j : {conc:.0%}" if np.isfinite(conc)
          else f"stress ×0,75 (×1) : {stress_agg:+.2f}%/an | concentration : n/a (total ≤ 0)")
    print(f"voisinage : {n_cells_pos}/9 cellules ×2 > 0, pire cellule ×1 {worst_cell_x1:+.2f}%/an")
    for k, v in crit.items():
        print(f"  {'PASS' if v else 'FAIL'}  {k}")
    print(f"-> {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
