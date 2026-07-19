#!/usr/bin/env python3
"""
scripts/experiment_log.py — Journal des expériences ML
=======================================================

Enregistre chaque test dans experiments.yaml pour éviter l'optimisation
cachée sur données OOS et garder une trace des décisions.

Usage :
  # Ajouter une expérience
  from scripts.experiment_log import log_experiment, show_experiments
  log_experiment(
      features_version="cvd_v1+oi_v1+basis_v1",
      model="LightGBM_simple",
      split="train<2022 cal=H2-2022",
      r2_oos=0.031,
      pf_oos=0.94,
      n_trades=127,
      decision="reject",
      reason="PF<1.0 sur 2022-2025",
  )

  # Lister les expériences
  python scripts/experiment_log.py --list
  python scripts/experiment_log.py --last 5
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

try:
    import yaml
    _YAML = True
except ImportError:
    _YAML = False

ROOT      = Path(__file__).resolve().parent.parent
LOG_FILE  = ROOT / "reports" / "experiments.yaml"
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)


def _load() -> list:
    if not LOG_FILE.exists():
        return []
    try:
        if _YAML:
            import yaml
            data = yaml.safe_load(LOG_FILE.read_text()) or []
        else:
            data = json.loads(LOG_FILE.read_text()) if LOG_FILE.suffix == ".json" else []
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save(experiments: list) -> None:
    if _YAML:
        import yaml
        LOG_FILE.write_text(yaml.dump(experiments, default_flow_style=False, allow_unicode=True))
    else:
        LOG_FILE.with_suffix(".json").write_text(json.dumps(experiments, indent=2, default=str))


def log_experiment(
    features_version: str,
    model:            str,
    split:            str,
    pf_oos:           float,
    n_trades:         int,
    decision:         str,   # "reject" | "incubate" | "promote" | "retire"
    reason:           str,
    r2_oos:           Optional[float] = None,
    wr_oos:           Optional[float] = None,
    assets:           Optional[str]   = None,
    years:            Optional[str]   = None,
    notes:            Optional[str]   = None,
) -> str:
    """Enregistre une expérience. Retourne l'experiment_id."""
    now   = datetime.now(timezone.utc)
    stamp = now.strftime("%Y%m%d_%H%M")
    h     = hashlib.md5(f"{features_version}{model}{stamp}".encode()).hexdigest()[:6]
    eid   = f"EXP-{stamp}-{h}"

    valid_decisions = {"reject", "incubate", "promote", "retire"}
    if decision not in valid_decisions:
        raise ValueError(f"decision doit être dans {valid_decisions}")

    entry = {
        "experiment_id":    eid,
        "timestamp":        now.isoformat(),
        "features_version": features_version,
        "model":            model,
        "split":            split,
        "assets":           assets or "TOP_10",
        "years":            years or "all",
        "r2_oos":           round(r2_oos, 4) if r2_oos is not None else None,
        "pf_oos":           round(pf_oos, 3),
        "wr_oos":           round(wr_oos, 3) if wr_oos is not None else None,
        "n_trades":         n_trades,
        "decision":         decision,
        "reason":           reason,
        "notes":            notes or "",
    }

    experiments = _load()
    experiments.append(entry)
    _save(experiments)

    print(f"  ✓ Experiment enregistré : {eid}")
    print(f"    Decision : {decision.upper()}  PF={pf_oos:.3f}  n={n_trades}")
    return eid


def show_experiments(last: int = 0, decision_filter: Optional[str] = None) -> None:
    experiments = _load()
    if not experiments:
        print("  Aucune expérience enregistrée.")
        return

    if decision_filter:
        experiments = [e for e in experiments if e.get("decision") == decision_filter]
    if last > 0:
        experiments = experiments[-last:]

    print(f"\n{'─'*90}")
    print(f"  {'ID':<25} {'Modèle':<20} {'PF':>6} {'n':>5} {'R²':>6} {'Decision':<12} Raison")
    print(f"{'─'*90}")
    for e in experiments:
        r2 = f"{e.get('r2_oos','?'):>6.4f}" if e.get("r2_oos") is not None else "     —"
        d  = e.get("decision","?").upper()
        print(f"  {e.get('experiment_id','?'):<25} {e.get('model','?'):<20} "
              f"{e.get('pf_oos',0):>6.3f} {e.get('n_trades',0):>5} {r2} "
              f"{d:<12} {e.get('reason','')[:30]}")
    print(f"{'─'*90}")
    print(f"  {len(experiments)} expérience(s)  |  fichier : {LOG_FILE.relative_to(ROOT)}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--list",     action="store_true")
    parser.add_argument("--last",     type=int, default=0)
    parser.add_argument("--decision", default=None, help="Filtrer par décision")
    parser.add_argument("--log",      action="store_true",
                        help="Mode interactif : enregistrer une expérience")
    args = parser.parse_args()

    if args.list or not args.log:
        show_experiments(last=args.last, decision_filter=args.decision)
        return

    # Mode interactif (si --log)
    print("=== Enregistrement d'une expérience ===")
    log_experiment(
        features_version=input("features_version : ").strip(),
        model=input("model : ").strip(),
        split=input("split (ex: train<2022 cal=H2-2022) : ").strip(),
        pf_oos=float(input("pf_oos : ").strip()),
        n_trades=int(input("n_trades : ").strip()),
        decision=input("decision (reject/incubate/promote/retire) : ").strip(),
        reason=input("reason : ").strip(),
        r2_oos=float(v) if (v := input("r2_oos (vide=None) : ").strip()) else None,
        notes=input("notes (optionnel) : ").strip() or None,
    )


if __name__ == "__main__":
    main()


# ── Enregistrement automatique des expériences déjà réalisées ─────────────────

_KNOWN_EXPERIMENTS = [
    dict(
        features_version="OHLCV+techniques (base)",
        model="TRMFleetV4_BL",
        split="train<H1-2025 cal=H2-2025",
        assets="BTC+ETH+BNB+SOL",
        years="2022-2025",
        r2_oos=0.022,
        pf_oos=0.638,
        wr_oss=0.423,
        n_trades=317,
        decision="incubate",
        reason="PF<1 mais V3 améliore (+7.4pts). Alpha insuffisant avec features OHLCV.",
    ),
    dict(
        features_version="OHLCV+regime_oversample+ReturnPredictor_gate",
        model="TRMFleetV4_V3",
        split="train<H1-2025 cal=H2-2025 NO_LONG_mult=2",
        assets="BTC+ETH+BNB+SOL",
        years="2022-2025",
        r2_oos=0.031,
        pf_oos=0.841,
        wr_oss=0.493,
        n_trades=554,
        decision="incubate",
        reason="V3>BL +7.4pts mais PF<1. STOP: R²<0.05 sur micro features Ridge.",
    ),
    dict(
        features_version="OHLCV+CVD+OI_delta+basis (Ridge OOS test)",
        model="ReturnPredictor_Ridge",
        split="train<=2022 OOS>=2023",
        assets="BTC+ETH+SOL+BNB",
        years="2023-2025",
        r2_oos=-0.024,
        pf_oos=0.0,
        n_trades=0,
        decision="reject",
        reason="R² OOS négatif (-0.024). Features micro n'apportent pas d'alpha linéaire.",
    ),
]


def _seed_known_experiments() -> None:
    """Pré-charge les expériences connues si le fichier est vide."""
    if LOG_FILE.exists() and LOG_FILE.stat().st_size > 100:
        return
    existing = _load()
    if not existing:
        for exp in _KNOWN_EXPERIMENTS:
            try:
                log_experiment(**{k: v for k, v in exp.items() if k != "wr_oss"})
            except Exception:
                pass


_seed_known_experiments()
