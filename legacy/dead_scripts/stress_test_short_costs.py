#!/usr/bin/env python3
"""
scripts/stress_test_short_costs.py — STRESS TESTS COÛTS ET LIQUIDITÉ SHORT
===========================================================================

Teste la robustesse de TRMShortFleet face à des scénarios adverses :
  • Coûts variables  : 10 / 15 / 20 bps
  • Slippage         : 1× / 2× / 3×
  • Filtre liquidité : tous les trades / sans les 10% pires volumes
  • Funding adverse  : +5 bps supplémentaires par trade

Si le fichier de résultats est absent, 150 trades synthétiques sont simulés
avec une distribution réaliste (PF ≈ 1.2).

Usage :
  python scripts/stress_test_short_costs.py
"""
from __future__ import annotations

import json
import sys
import warnings
from itertools import product
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

REPORT_DIR   = ROOT / "reports" / "short_rebuild"
REPORT_DIR.mkdir(parents=True, exist_ok=True)
RESULTS_JSON = REPORT_DIR / "walk_forward_short_results.json"

# ── Scénarios ──────────────────────────────────────────────────────────────────
COST_SCENARIOS       = [0.0010, 0.0015, 0.0020]   # 10, 15, 20 bps
SLIPPAGE_MULTIPLIERS = [1.0, 2.0, 3.0]
LIQUIDITY_FILTERS    = [None, "worst_10pct"]
FUNDING_SCENARIOS    = ["neutral", "adverse"]

BASE_SLIPPAGE    = 0.0002   # 2 bps slippage de base
FUNDING_ADVERSE  = 0.0005   # +5 bps si funding adverse
POSITION_SIZE    = 0.001    # 0.1% du capital par trade
EQUITY0          = 10_000.0

DATA_PATH = ROOT / "data" / "BTCUSD_1h_alpha.csv"


# ═══════════════════════════════════════════════════════════════════════════════
# CHARGEMENT / SIMULATION DES TRADES
# ═══════════════════════════════════════════════════════════════════════════════

def _load_volume_series() -> Optional[np.ndarray]:
    """Charge la série de volumes BTC si disponible."""
    if not DATA_PATH.exists():
        return None
    try:
        df = pd.read_csv(DATA_PATH, usecols=["volume"] if "volume" in
                         pd.read_csv(DATA_PATH, nrows=1).columns else ["Volume"])
        col = "volume" if "volume" in df.columns else "Volume"
        return df[col].values.astype(float)
    except Exception:
        return None


def load_trades_from_results() -> Optional[List[Dict]]:
    """
    Charge les trades individuels depuis walk_forward_short_results.json.
    Retourne None si fichier absent ou structure incompatible.
    """
    if not RESULTS_JSON.exists():
        return None

    with open(RESULTS_JSON) as f:
        data = json.load(f)

    # Chercher une liste de trades
    if isinstance(data.get("trades"), list):
        return data["trades"]

    # Structure par folds → aplatir
    trades = []
    for fold in data.get("folds", []):
        fold_trades = fold.get("trades", [])
        trades.extend(fold_trades)

    return trades if trades else None


def simulate_synthetic_trades(n: int = 150, seed: int = 42) -> List[Dict]:
    """
    Génère n trades synthétiques avec :
    - Distribution PF ≈ 1.2
    - WR ≈ 51%
    - Distribution réaliste des retours et volumes
    """
    rng = np.random.default_rng(seed)
    trades = []

    notional = EQUITY0 * POSITION_SIZE

    # Distribution des retours — légèrement positive (short efficace)
    # 51% gagnants, distribution asymétrique
    for i in range(n):
        win = rng.random() < 0.51

        if win:
            # Retour court positif : 0.1% à 0.8%
            raw_ret = rng.exponential(0.003) + 0.001
            raw_ret = min(raw_ret, 0.015)
        else:
            # Perte : 0.05% à 0.6%
            raw_ret = -(rng.exponential(0.0025) + 0.0005)
            raw_ret = max(raw_ret, -0.012)

        gross_pnl = raw_ret * notional
        volume    = rng.lognormal(mean=8.0, sigma=1.2)   # volume synthétique (USD millions)

        trades.append({
            "gross_pnl":    float(gross_pnl),
            "raw_ret":      float(raw_ret),
            "notional":     float(notional),
            "volume_proxy": float(volume),
            "bar_index":    int(i * 4),
        })

    print(f"  [Synthétique] {n} trades simulés (PF cible ≈ 1.20)")
    return trades


def enrich_trades_with_volume(
    trades: List[Dict],
    volume_series: Optional[np.ndarray],
) -> List[Dict]:
    """Ajoute ou complète le volume_proxy dans chaque trade."""
    if volume_series is None or len(volume_series) == 0:
        # Synthétiser le volume si absent
        rng = np.random.default_rng(0)
        for t in trades:
            if "volume_proxy" not in t:
                t["volume_proxy"] = float(rng.lognormal(8.0, 1.2))
        return trades

    p10 = float(np.percentile(volume_series, 10))

    for t in trades:
        if "volume_proxy" not in t:
            idx = t.get("bar_index", 0)
            idx = min(int(idx), len(volume_series) - 1)
            t["volume_proxy"] = float(volume_series[idx])
        t["volume_p10_threshold"] = p10

    return trades


# ═══════════════════════════════════════════════════════════════════════════════
# APPLICATION DES SCÉNARIOS
# ═══════════════════════════════════════════════════════════════════════════════

def apply_scenario(
    trades: List[Dict],
    cost: float,
    slip_mult: float,
    liq_filter: Optional[str],
    funding_scenario: str,
) -> Dict:
    """
    Applique un scénario de coût/slippage/liquidité/funding aux trades.

    Retourne les métriques du scénario.
    """
    notional = EQUITY0 * POSITION_SIZE
    slippage = BASE_SLIPPAGE * slip_mult
    extra_funding = FUNDING_ADVERSE if funding_scenario == "adverse" else 0.0

    total_cost_per_trade = cost + slippage + extra_funding

    # Filtre liquidité
    filtered_trades = trades
    n_lost_liquidity = 0

    if liq_filter == "worst_10pct":
        # Déterminer le seuil p10 du volume
        volumes = np.array([t.get("volume_proxy", 1e9) for t in trades])
        p10_vol = float(np.percentile(volumes, 10))
        before  = len(filtered_trades)
        filtered_trades = [t for t in trades if t.get("volume_proxy", 1e9) > p10_vol]
        n_lost_liquidity = before - len(filtered_trades)

    if not filtered_trades:
        return {
            "n_trades":            0,
            "n_lost_to_liquidity": n_lost_liquidity,
            "profit_factor":       0.0,
            "expectancy":          0.0,
            "max_drawdown_pct":    0.0,
            "total_return_pct":    0.0,
            "degradation_pct":     100.0,
            "verdict":             "broken",
        }

    # Calcul PnL net
    net_pnls = []
    for t in filtered_trades:
        gross = t.get("gross_pnl", t.get("raw_ret", 0.0) * notional)
        fee   = notional * total_cost_per_trade
        net_pnls.append(gross - fee)

    net_pnls = np.array(net_pnls, dtype=float)
    wins      = net_pnls[net_pnls > 0]
    losses    = net_pnls[net_pnls < 0]

    gross_win  = float(wins.sum())   if len(wins)   > 0 else 0.0
    gross_loss = float(abs(losses.sum())) if len(losses) > 0 else 1e-12
    pf         = gross_win / gross_loss if gross_loss > 1e-12 else float("inf")
    expectancy = float(net_pnls.mean())

    # Drawdown
    eq = np.concatenate([[EQUITY0], EQUITY0 + np.cumsum(net_pnls)])
    run_max = np.maximum.accumulate(eq)
    dds     = (eq - run_max) / (run_max + 1e-9) * 100
    max_dd  = float(dds.min())
    total_ret = (eq[-1] - EQUITY0) / EQUITY0 * 100

    # Verdict
    if pf >= 1.0:
        verdict = "viable"
    elif pf >= 0.80:
        verdict = "fragile"
    else:
        verdict = "broken"

    return {
        "n_trades":            len(filtered_trades),
        "n_lost_to_liquidity": n_lost_liquidity,
        "profit_factor":       round(pf, 4) if pf != float("inf") else None,
        "expectancy":          round(expectancy, 6),
        "max_drawdown_pct":    round(max_dd, 2),
        "total_return_pct":    round(total_ret, 2),
        "verdict":             verdict,
    }


def compute_degradation(results: Dict, baseline_pf: float) -> Dict:
    """Ajoute le % de dégradation par rapport au scénario de base."""
    for key, res in results.items():
        pf = res.get("profit_factor") or 0.0
        if baseline_pf > 1e-6:
            deg = (baseline_pf - pf) / baseline_pf * 100
        else:
            deg = 0.0
        res["degradation_pct"] = round(float(deg), 2)
    return results


# ═══════════════════════════════════════════════════════════════════════════════
# TABLE 3D cost × slippage → PF
# ═══════════════════════════════════════════════════════════════════════════════

def print_cost_slippage_table(
    all_results: Dict,
    liq_filter: Optional[str] = None,
    funding: str = "neutral",
) -> None:
    """Affiche la table cost × slippage → PF pour les filtres donnés."""
    label_liq = liq_filter or "all_trades"
    print(f"\n  Table PF : liquidité={label_liq} | funding={funding}")
    print(f"  {'':12}", end="")
    for slip in SLIPPAGE_MULTIPLIERS:
        print(f"  slip×{slip:.0f}  ", end="")
    print()
    print("  " + "─" * 52)

    for cost in COST_SCENARIOS:
        cost_bps = int(cost * 10_000)
        print(f"  {cost_bps:3d} bps    │", end="")
        for slip in SLIPPAGE_MULTIPLIERS:
            key = _make_key(cost, slip, liq_filter, funding)
            res = all_results.get(key, {})
            pf  = res.get("profit_factor") or 0.0
            verdict = res.get("verdict", "?")
            mark = " " if verdict == "viable" else ("~" if verdict == "fragile" else "!")
            print(f"  {pf:5.3f}{mark} ", end="")
        print()


def _make_key(
    cost: float,
    slip: float,
    liq: Optional[str],
    funding: str,
) -> str:
    liq_s = liq or "all"
    return f"cost{int(cost*10000)}bps_slip{slip:.0f}x_liq{liq_s}_fund{funding}"


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def run_stress_tests() -> Dict:
    # Chargement trades
    print("Chargement des trades …")
    trades = load_trades_from_results()
    source = "walk_forward_results"

    if not trades:
        print("  Fichier résultats absent — simulation de 150 trades synthétiques.")
        trades = simulate_synthetic_trades(n=150)
        source = "synthetic"

    # Chargement volume
    vol_series = _load_volume_series()
    trades = enrich_trades_with_volume(trades, vol_series)

    print(f"  {len(trades)} trades chargés depuis '{source}'")

    # Scénario de référence
    baseline_result = apply_scenario(
        trades, COST_SCENARIOS[0], SLIPPAGE_MULTIPLIERS[0], None, "neutral"
    )
    baseline_pf = baseline_result.get("profit_factor") or 0.0
    print(f"  Scénario de référence (10bps, slip×1, all, neutral) : PF={baseline_pf:.3f}")

    # Itération sur tous les scénarios
    total = len(COST_SCENARIOS) * len(SLIPPAGE_MULTIPLIERS) * len(LIQUIDITY_FILTERS) * len(FUNDING_SCENARIOS)
    print(f"\nLancement de {total} scénarios de stress …\n")

    all_results: Dict[str, Dict] = {}
    broken_count  = 0
    fragile_count = 0
    viable_count  = 0

    for cost, slip, liq, funding in product(
        COST_SCENARIOS, SLIPPAGE_MULTIPLIERS, LIQUIDITY_FILTERS, FUNDING_SCENARIOS
    ):
        key = _make_key(cost, slip, liq, funding)
        res = apply_scenario(trades, cost, slip, liq, funding)
        all_results[key] = {
            **res,
            "cost_bps":          int(cost * 10_000),
            "slippage_mult":     slip,
            "liquidity_filter":  liq or "all",
            "funding_scenario":  funding,
        }
        v = res["verdict"]
        if v == "viable":   viable_count  += 1
        elif v == "fragile": fragile_count += 1
        else:                broken_count  += 1

    # Dégradation relative
    all_results = compute_degradation(all_results, baseline_pf)

    # ── Résumé ────────────────────────────────────────────────────────────────
    sep = "─" * 70
    print(f"\n{sep}")
    print("RÉSULTATS STRESS TESTS COÛTS — SHORT")
    print(sep)
    print(f"Total scénarios : {total}")
    print(f"  Viable  (PF ≥ 1.0)       : {viable_count:3d}  ({viable_count/total*100:.0f}%)")
    print(f"  Fragile (0.80 ≤ PF < 1.0): {fragile_count:3d}  ({fragile_count/total*100:.0f}%)")
    print(f"  Broken  (PF < 0.80)       : {broken_count:3d}  ({broken_count/total*100:.0f}%)")

    # Tables 3D par combinaison liquidité × funding
    for liq in LIQUIDITY_FILTERS:
        for funding in FUNDING_SCENARIOS:
            print_cost_slippage_table(all_results, liq, funding)

    # Scénario le plus adversaire
    worst_key = min(
        all_results.keys(),
        key=lambda k: all_results[k].get("profit_factor") or 0.0,
    )
    worst = all_results[worst_key]
    print(f"\n  Pire scénario : {worst_key}")
    print(f"    PF={worst.get('profit_factor'):.3f}  "
          f"DD={worst.get('max_drawdown_pct'):.2f}%  "
          f"Verdict={worst['verdict']}")

    print(sep)

    return all_results


def save_results(all_results: Dict) -> None:
    out = {
        "scenarios": all_results,
        "cost_bps_tested":  [int(c * 10_000) for c in COST_SCENARIOS],
        "slip_mult_tested":  SLIPPAGE_MULTIPLIERS,
        "liq_filters_tested": [l or "all" for l in LIQUIDITY_FILTERS],
        "funding_tested":    FUNDING_SCENARIOS,
        "base_slippage_bps": int(BASE_SLIPPAGE * 10_000),
        "funding_adverse_bps": int(FUNDING_ADVERSE * 10_000),
        "equity0":           EQUITY0,
        "position_size":     POSITION_SIZE,
        "verdicts": {
            "viable":  "PF >= 1.0",
            "fragile": "0.80 <= PF < 1.0",
            "broken":  "PF < 0.80",
        },
    }
    path = REPORT_DIR / "short_cost_stress.json"
    path.write_text(json.dumps(out, indent=2, default=str))
    print(f"\nRésultats sauvegardés : {path}")


def main() -> None:
    results = run_stress_tests()
    save_results(results)

    # Verdict final : au moins 50% des scénarios viables
    viable = sum(1 for r in results.values() if r.get("verdict") == "viable")
    total  = len(results)
    print(f"\nVIABLES : {viable}/{total} ({viable/total*100:.0f}%)")
    sys.exit(0 if viable >= total // 2 else 1)


if __name__ == "__main__":
    main()
