#!/usr/bin/env python3
"""
scripts/audit_decluster_versions.py
─────────────────────────────────────────────────────────────────────────────
LES MÊMES ALPHAS, RECOMPTÉS SOUS LES TROIS DÉFINITIONS D'ÉPISODE.

Pourquoi
────────
`decluster` v1 regroupe par lien simple, donc une stratégie qui tire
régulièrement sur un même symbole voit tous ses tirages fusionner en un
épisode unique. Trois alphas ont été déclarés « indécidables » sur cette base.
La question ouverte est simple : l'étaient-ils vraiment, ou l'appareil
était-il seulement plus myope qu'il n'avait besoin de l'être ?

Ce script ne réécrit RIEN. Il republie, côte à côte, ce que chaque définition
donne. Les colonnes v1 restent la référence historique — c'est sous elle que
tous les verdicts déjà publiés ont été rendus.

Les trois définitions
─────────────────────
  v1  lien simple    l'épisode s'étend tant que l'écart au point PRÉCÉDENT
                     reste sous la fenêtre. Chaîne indéfiniment.
  v2  lien complet   l'épisode s'étend tant que l'écart à son PROPRE DÉBUT
                     reste sous la fenêtre. Durée bornée, pas de chaînage.
  fx  fenêtres fixes découpage calendaire ancré sur l'époque. Pas de chaînage
                     non plus, et deux alphas partagent leurs bornes — mais la
                     phase est arbitraire.

Aucune n'est « la vraie ». v1 sous-estime toujours l'indépendance ; les
fenêtres fixes la surestiment quand deux décisions tombent de part et d'autre
d'une borne. Le lien complet est entre les deux et ne dépend pas d'une phase.
Publier les trois est la seule lecture qui ne cache pas le choix.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd

from src.institutional.live_alpha_lab.episodes import (
    DECLUSTER_V1,
    DECLUSTER_V2,
    DECLUSTER_V2_FIXED,
    DECLUSTER_VERSIONS,
    decluster_versioned,
)
from src.institutional.live_alpha_lab.outcomes import (
    COST_BPS_ROUNDTRIP_BASE,
    LABELABLE,
    load_outcomes,
)
from src.institutional.live_alpha_lab.preregistration import threshold_t

OUT = ROOT / "reports" / "live_alpha_lab" / "EPISODE_COUNTS_V1_V2.md"
JSON_OUT = ROOT / "reports" / "live_alpha_lab" / "EPISODE_COUNTS_V1_V2.json"

CLUSTER_WINDOW_HOURS = 24.0
# La cible opérationnelle : +15 bps nets, soit +29 bps d'excess avant coût.
TARGET_EXCESS_BPS = 15.0 + COST_BPS_ROUNDTRIP_BASE
VERSIONS = (DECLUSTER_V1, DECLUSTER_V2, DECLUSTER_V2_FIXED)
CONTROLS = ("PLACEBO_RANDOM_V1", "POSITIVE_CONTROL_ORACLE_V1")


def stats_for(df: pd.DataFrame, version: str, cross_sectional: bool,
              anchor: str = "dec") -> Optional[Dict[str, object]]:
    col = f"{anchor}_excess_bps"
    ok = df[(df[f"{anchor}_status"] == "OK") & df[col].notna()].copy()
    if ok.empty:
        return None
    ok["event_time"] = pd.to_datetime(ok["event_time"], utc=True)
    if cross_sectional:
        # Le déclencheur est commun à tout l'univers : un choc = une preuve,
        # quel que soit le nombre de symboles touchés.
        ok["_episode_symbol"] = "_ALL_"
        clustered = decluster_versioned(ok, "event_time", "_episode_symbol",
                                        CLUSTER_WINDOW_HOURS, version)
    else:
        clustered = decluster_versioned(ok, "event_time", "symbol",
                                        CLUSTER_WINDOW_HOURS, version)
    ep = clustered.groupby("cluster_id")[col].mean()
    n = int(len(ep))
    if n < 2:
        return {"n_episodes": n, "insufficient": True}
    sigma = float(ep.std(ddof=1))
    mean = float(ep.mean())
    half = 1.96 * sigma / np.sqrt(n)
    return {
        "n_episodes": n,
        "n_decisions": int(len(ok)),
        "decisions_per_episode": round(len(ok) / n, 2),
        "mean_excess_bps": round(mean, 2),
        "sigma_bps": round(sigma, 1),
        "ci95": [round(mean - half, 1), round(mean + half, 1)],
        "mde80_bps": round(2.802 * sigma / np.sqrt(n), 1),
        # « exclut la cible » = la borne haute de l'IC est sous +29 bps.
        "excludes_target": bool(mean + half < TARGET_EXCESS_BPS),
        "insufficient": False,
    }


def render(rows: List[Dict[str, object]]) -> str:
    out: List[str] = []
    out.append("# Les mêmes alphas, recomptés sous trois définitions d'épisode")
    out.append("")
    out.append("_Généré par `scripts/audit_decluster_versions.py`. Ne pas éditer à la main._")
    out.append("")
    out.append("Trois alphas ont été déclarés « indécidables » sous le decluster v1. La question")
    out.append("est de savoir s'ils l'étaient vraiment, ou si l'appareil était seulement plus")
    out.append("myope qu'il n'avait besoin de l'être. **Rien n'est réécrit ici** : v1 reste la")
    out.append("colonne de référence, puisque c'est sous elle que tous les verdicts déjà publiés")
    out.append("ont été rendus.")
    out.append("")
    out.append("## Les trois définitions")
    out.append("")
    out.append("| version | règle |")
    out.append("|---|---|")
    for version in VERSIONS:
        out.append("| `%s` | %s |" % (version, DECLUSTER_VERSIONS[version]))
    out.append("")
    out.append("Aucune n'est « la vraie ». v1 sous-estime toujours l'indépendance ; les fenêtres")
    out.append("fixes la surestiment quand deux décisions tombent de part et d'autre d'une borne ;")
    out.append("le lien complet est entre les deux et ne dépend d'aucune phase. Publier les trois")
    out.append("est la seule lecture qui ne cache pas le choix.")
    out.append("")
    out.append("## Épisodes, par alpha et par définition")
    out.append("")
    out.append("| alpha | décisions | v1 | v2 lien complet | v2 fenêtres fixes | gain v2/v1 |")
    out.append("|---|---|---|---|---|---|")
    for row in rows:
        v1 = row["by_version"].get(DECLUSTER_V1, {})
        v2 = row["by_version"].get(DECLUSTER_V2, {})
        fx = row["by_version"].get(DECLUSTER_V2_FIXED, {})
        n1 = v1.get("n_episodes", 0)
        gain = ("×%.1f" % (v2.get("n_episodes", 0) / n1)) if n1 else "—"
        tag = " ⚙️" if row["alpha_id"] in CONTROLS else ""
        out.append("| `%s`%s | %d | %d | **%d** | %d | %s |" % (
            row["alpha_id"], tag, v1.get("n_decisions", 0), n1,
            v2.get("n_episodes", 0), fx.get("n_episodes", 0), gain))
    out.append("")
    out.append("## Ce que ça change aux verdicts")
    out.append("")
    out.append("Rappel de la cible : **+29 bps d'excess** avant coût (= +15 bps nets). Un alpha")
    out.append("« tranche » quand la borne haute de son intervalle passe sous cette cible.")
    out.append("")
    out.append("| alpha | v1 : IC / verdict | v2 : IC / verdict | statut |")
    out.append("|---|---|---|---|")
    changed: List[str] = []
    for row in rows:
        if row["alpha_id"] in CONTROLS:
            continue
        v1 = row["by_version"].get(DECLUSTER_V1, {})
        v2 = row["by_version"].get(DECLUSTER_V2, {})
        if v1.get("insufficient", True) or v2.get("insufficient", True):
            out.append("| `%s` | n trop faible | n trop faible | ⚪ rien à lire |" % row["alpha_id"])
            continue
        def cell(v):
            return "[%+.1f, %+.1f] %s" % (v["ci95"][0], v["ci95"][1],
                                          "✅" if v["excludes_target"] else "🟠")
        moved = (not v1["excludes_target"]) and v2["excludes_target"]
        if moved:
            changed.append(row["alpha_id"])
        out.append("| `%s` | %s | %s | %s |" % (
            row["alpha_id"], cell(v1), cell(v2),
            "**tranche désormais**" if moved else
            ("inchangé — tranchait déjà" if v1["excludes_target"] else "inchangé — toujours indécidable")))
    out.append("")
    out.append("_✅ = l'intervalle exclut la cible de +29 bps. 🟠 = il ne l'exclut pas._")
    out.append("")
    if changed:
        out.append("**%d alpha(s) changent de statut sous v2 : %s.** Leur « indécidable » venait" % (
            len(changed), ", ".join("`%s`" % a for a in changed)))
        out.append("de la règle de comptage, pas de la donnée. C'est de la preuve qui existait")
        out.append("déjà et qu'on croyait ne pas avoir.")
    else:
        out.append("**Aucun alpha ne change de statut.** Les indécidables le restent sous les trois")
        out.append("définitions : leur intervalle n'exclut la cible sous aucune façon de compter.")
        out.append("Le gain de puissance de v2 est réel mais insuffisant, ce qui déplace la")
        out.append("conclusion — ce n'était pas la règle de comptage, c'est la quantité de")
        out.append("collecte.")
    out.append("")
    out.append("## Ce que ce recomptage ne fait pas")
    out.append("")
    out.append("**Il ne valide aucun alpha.** Passer de « indécidable » à « tranche » ne veut dire")
    out.append("qu'une chose : l'intervalle exclut désormais la cible. Dans tous les cas mesurés")
    out.append("ici, il l'exclut **par le bas** — c'est un refus mieux fondé, pas une découverte.")
    out.append("")
    out.append("**Il ne change aucun verdict déjà publié.** Le scoreboard, l'item A1 et le")
    out.append("contrôle positif restent calculés sous v1. Les deux colonnes coexistent pour")
    out.append("qu'on sache toujours sous quelle règle une décision a été prise.")
    out.append("")
    out.append("**Il ne rend pas v2 obligatoire pour la suite.** Le choix de la définition doit")
    out.append("être scellé AVANT de regarder un résultat, comme le reste — sinon choisir la")
    out.append("définition qui donne le plus d'épisodes est un essai de plus, non compté.")
    out.append("")
    return "\n".join(out) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--anchor", default="dec", choices=("dec", "evt"))
    args = parser.parse_args(argv)

    rows: List[Dict[str, object]] = []
    for alpha_id, spec in LABELABLE.items():
        df = load_outcomes(alpha_id)
        if df is None or df.empty:
            continue
        by_version = {}
        for version in VERSIONS:
            stats = stats_for(df, version, spec.cross_sectional, args.anchor)
            if stats is not None:
                by_version[version] = stats
        if by_version:
            rows.append({"alpha_id": alpha_id, "cross_sectional": spec.cross_sectional,
                         "by_version": by_version})
            print("%-32s %s" % (alpha_id, {v: by_version[v].get("n_episodes") for v in by_version}),
                  flush=True)

    OUT.write_text(render(rows), encoding="utf-8")
    JSON_OUT.write_text(json.dumps({
        "cluster_window_hours": CLUSTER_WINDOW_HOURS,
        "target_excess_bps": TARGET_EXCESS_BPS,
        "anchor": args.anchor, "alphas": rows,
    }, indent=2), encoding="utf-8")
    print("écrit -> %s" % OUT, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
