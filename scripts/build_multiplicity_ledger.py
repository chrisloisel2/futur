#!/usr/bin/env python3
"""
scripts/build_multiplicity_ledger.py
─────────────────────────────────────────────────────────────────────────────
LEDGER DE MULTIPLICITÉ — combien de mécanismes le programme a réellement
testés, et ce que ça coûte aux candidats qui en sont sortis.

Pourquoi
────────
Deux candidats sont ressortis validés des campagnes edge_discovery
(LIQ_REPEAT_DENSITY 22,1 bps, BTC_LEAD_ALT_CASCADE 46,87 bps). Leurs t-stats
sont évalués contre le null d'UN essai, alors qu'ils sont le maximum de
centaines. Un programme qui teste assez de mécanismes finit mécaniquement par
en produire un qui a l'air significatif : sans compte d'essais, on ne peut pas
distinguer ce cas d'un vrai edge.

Les essais se comptent, ils ne se racontent pas. Ce script les compte à partir
des artefacts sur disque, en enregistrant POUR CHAQUE compte la méthode
d'extraction — un chiffre sans sa provenance ne vaut pas mieux qu'un souvenir.

Honnêteté du compte
───────────────────
Tous les workers n'exposent pas un compte machine-lisible. Ceux-là sont
marqués NON_EXTRACTIBLE et comptés pour ZÉRO, ce qui fait du total une BORNE
BASSE. Un compte sous-estimé produit un haircut sous-estimé : les verdicts de
déflation sont donc OPTIMISTES. Un candidat qui ne survit pas déjà à cette
borne basse ne survivra à rien.

Sortie : reports/edge_discovery/MULTIPLICITY_LEDGER.json
"""
from __future__ import annotations

import glob
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import yaml

from src.institutional.live_alpha_lab.multiplicity import (
    deflate, tstat_from_one_sided_bound, tstat_from_two_sided_ci,
)

OUT = ROOT / "reports" / "edge_discovery" / "MULTIPLICITY_LEDGER.json"
VALIDATION_REGISTRY = ROOT / "configs" / "validation_registry.yaml"
LIVE_REGISTRY = ROOT / "configs" / "live_alpha_registry.yaml"

# Ordre de préférence des clés : un compte explicitement déclaré par le worker
# vaut mieux qu'une longueur de liste, qui vaut mieux qu'une somme de verdicts.
_EXPLICIT_KEYS = ("n_mechanisms", "n_hypotheses", "n_tests", "mechanisms_tested")
_LIST_KEYS = ("hypotheses", "mechanisms", "results", "findings", "tests", "candidates")
_VERDICT_KEYS = ("verdict_counts", "verdicts")


def extract_count(d) -> tuple:
    if isinstance(d, dict):
        for k in _EXPLICIT_KEYS:
            if isinstance(d.get(k), int):
                return d[k], f"clé explicite `{k}`"
        for k in _LIST_KEYS:
            if isinstance(d.get(k), list):
                return len(d[k]), f"longueur de `{k}`"
        for k in _VERDICT_KEYS:
            if isinstance(d.get(k), dict):
                v = sum(x for x in d[k].values() if isinstance(x, int))
                if v:
                    return v, f"somme de `{k}`"
    if isinstance(d, list):
        return len(d), "longueur de la racine"
    return None, "NON_EXTRACTIBLE — aucun compte machine-lisible dans ce RESULTS.json"


def campaigns() -> list:
    out = []
    for camp_dir in sorted(Path(ROOT / "reports" / "edge_discovery").glob("alpha_hunt_*")):
        workers, total, unknown = [], 0, 0
        for wdir in sorted(camp_dir.glob("w*")):
            if not wdir.is_dir():
                continue
            res = wdir / "RESULTS.json"
            if not res.exists():
                workers.append({"worker": wdir.name, "n": None,
                                "method": "NON_EXTRACTIBLE — pas de RESULTS.json"})
                unknown += 1
                continue
            try:
                n, how = extract_count(json.loads(res.read_text()))
            except Exception as exc:
                n, how = None, f"NON_EXTRACTIBLE — {type(exc).__name__}"
            workers.append({"worker": wdir.name, "n": n, "method": how})
            if n:
                total += n
            else:
                unknown += 1
        out.append({"campaign": camp_dir.name, "n_workers": len(workers),
                    "n_mechanisms_counted": total,
                    "n_workers_not_extractable": unknown, "workers": workers})
    return out


def validated_candidates() -> list:
    """Les candidats validés, avec leur t-stat — déclaré s'il existe, sinon
    dérivé d'un intervalle bootstrap. La dérivation est recoupée sur le seul
    candidat qui porte les deux (BTC_LEAD_ALT_CASCADE : 3,327 dérivé contre
    3,315 déclaré), donc elle est vérifiée et non supposée."""
    v = yaml.safe_load(VALIDATION_REGISTRY.read_text())
    live = {a["alpha_id"]: a for a in yaml.safe_load(LIVE_REGISTRY.read_text())["alphas"]}
    out = []
    for c in v.get("candidates", []):
        if c.get("current_status") != "VALIDATED_FOR_FORWARD":
            continue
        t, src = c.get("t_stat_declustered"), "t_stat_declustered (déclaré)"
        if t is None:
            mean = c.get("validation_net_bps")
            frozen = live.get(c.get("frozen_alpha_id") or "", {})
            p05 = frozen.get("expected_bootstrap_p05_bps")
            ci = frozen.get("expected_bootstrap_ci95_bps")
            if mean is not None and p05 is not None:
                t, src = tstat_from_one_sided_bound(mean, p05), "dérivé du p05 bootstrap"
            elif mean is not None and isinstance(ci, str) and "," in ci:
                lo, hi = (float(x) for x in ci.strip("[] ").split(","))
                t, src = tstat_from_two_sided_ci(mean, lo, hi), "dérivé de l'IC95 bootstrap"
            else:
                src = ("AUCUN t-stat ni IC bootstrap conservé — la déflation est "
                       "impossible pour ce candidat, ce qui est en soi un défaut "
                       "de traçabilité, pas une absence de risque")
        out.append({"candidate_id": c["candidate_id"],
                    "frozen_alpha_id": c.get("frozen_alpha_id"),
                    "validation_net_bps": c.get("validation_net_bps"),
                    "n_validation_independent": c.get("n_validation_independent"),
                    "tstat": round(t, 4) if t else None, "tstat_source": src})
    return out


def main() -> int:
    camps = campaigns()
    total = sum(c["n_mechanisms_counted"] for c in camps)
    unknown = sum(c["n_workers_not_extractable"] for c in camps)
    cands = validated_candidates()

    for c in cands:
        c["deflation"] = deflate(c["tstat"], max(total, 1)) if c["tstat"] else None

    ledger = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "n_mechanisms_total_LOWER_BOUND": total,
        "n_workers_not_extractable": unknown,
        "why_lower_bound": (
            f"{unknown} worker(s) n'exposent aucun compte machine-lisible et sont "
            f"comptés pour ZÉRO. Le vrai total est strictement supérieur, donc le "
            f"haircut appliqué est sous-estimé et les verdicts ci-dessous sont "
            f"OPTIMISTES."),
        "campaigns": camps,
        "validated_candidates": cands,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(ledger, indent=2, ensure_ascii=False))

    print(f"[multiplicité] {total} mécanismes comptés (BORNE BASSE, {unknown} workers "
          f"non extractibles) sur {len(camps)} campagnes")
    for c in cands:
        d = c["deflation"]
        if d is None:
            print(f"  {c['candidate_id']:28s} t=?      {c['tstat_source'][:60]}")
            continue
        verdict = "SURVIT" if d["survives"] else "NE SURVIT PAS"
        print(f"  {c['candidate_id']:28s} t={d['observed_tstat']:.3f} "
              f"seuil={d['expected_max_null_tstat']:.3f} "
              f"déflaté={d['deflated_tstat']:+.3f} p={d['p_deflated']:.3f} -> {verdict}")
    print(f"[multiplicité] -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
