#!/usr/bin/env python3
"""
scripts/compare_enriched_distribution.py
─────────────────────────────────────────────────────────────────────────────
Compare un enriched reconstruit à des références valides (Phase 23.7).

Détecte : colonnes manquantes/en trop, features plates (std≈0), NaN masqués,
inf, ratio NaN anormal vs référence. But : repérer une feature cassée/inversée/
explosée avant de réintégrer l'actif dans l'univers.

Usage :
    python3 scripts/compare_enriched_distribution.py \
        --reference data/enriched/ADAUSDT_1h_enriched.parquet \
        --candidate data/enriched/BNBUSDT_1h_enriched.parquet \
        --out reports/BNBUSDT_enriched_distribution_check.md
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd


def _num_stats(df: pd.DataFrame) -> pd.DataFrame:
    num = df.select_dtypes("number")
    return pd.DataFrame({
        "mean": num.mean(), "std": num.std(),
        "nan": num.isna().mean(),
        "inf": np.isinf(num.to_numpy()).mean(axis=0),
    })


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reference", nargs="+", required=True)
    ap.add_argument("--candidate", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    cand = pd.read_parquet(args.candidate)
    refs = [pd.read_parquet(r) for r in args.reference]
    ref_cols = set().union(*[set(r.columns) for r in refs])
    cand_cols = set(cand.columns)

    missing = sorted(ref_cols - cand_cols)
    extra = sorted(cand_cols - ref_cols)

    cs = _num_stats(cand)
    ref_stats = [_num_stats(r) for r in refs]
    flat = sorted(cs[cs["std"].fillna(0) < 1e-12].index.tolist())
    high_nan = sorted(cs[cs["nan"] > 0.5].index.tolist())
    has_inf = sorted(cs[cs["inf"] > 0].index.tolist())

    # features plates UNIQUEMENT côté candidat (pas plates dans la réf) = vrai signal d'alerte
    ref_flat = set()
    for rs in ref_stats:
        ref_flat |= set(rs[rs["std"].fillna(0) < 1e-12].index)
    flat_vs_ref = sorted(set(flat) - ref_flat)

    # colonnes manquantes hors MTF (les variantes mtf_ sont un delta documenté)
    missing_non_mtf = [c for c in missing if not c.startswith("mtf_")]

    # ratio NaN candidat vs référence (médiane des refs)
    ref_nan = pd.concat([rs["nan"] for rs in ref_stats], axis=1).median(axis=1)
    common = [c for c in cs.index if c in ref_nan.index]
    nan_worse = sorted([c for c in common if cs.loc[c, "nan"] - ref_nan.get(c, 0) > 0.3])

    L = [f"# Distribution check — {Path(args.candidate).name}\n",
         f"- candidat : {cand.shape[1]} cols × {cand.shape[0]:,} rows",
         f"- référence(s) : {[Path(r).name for r in args.reference]} ({len(ref_cols)} cols union)",
         f"- colonnes manquantes vs réf : {len(missing)} (dont **non-MTF : {len(missing_non_mtf)}**)",
         f"- colonnes en trop : {len(extra)}",
         f"- features plates (absolu) : {len(flat)} — dont **plates SEULEMENT côté candidat : {len(flat_vs_ref)}**",
         f"- features >50% NaN : {len(high_nan)}",
         f"- features avec inf : {len(has_inf)}",
         f"- features NaN nettement pire que réf (+30pts) : {len(nan_worse)}\n",
         "## Échantillons\n",
         f"- manquantes non-MTF (≤30) : {missing_non_mtf[:30]}",
         f"- plates côté candidat seulement (≤30) : {flat_vs_ref[:30]}",
         f"- inf (≤30) : {has_inf[:30]}",
         f"- nan_worse (≤30) : {nan_worse[:30]}\n",
         ]
    verdict = ("PASS" if not has_inf and not nan_worse and len(missing_non_mtf) < 20
               and len(flat_vs_ref) < 20 else "REVIEW")
    L.append(f"## Verdict : **{verdict}**")
    L.append("(MTF lag-variants manquantes = delta documenté, non utilisé par les moteurs alpha)")
    if verdict != "PASS":
        L.append("(anomalies réelles candidat-only au-dessus du seuil — corriger avant réintégration)")

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text("\n".join(L))
    print("\n".join(L))
    print(f"\n→ {args.out}")


if __name__ == "__main__":
    main()
