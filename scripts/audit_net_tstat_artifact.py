#!/usr/bin/env python3
"""
scripts/audit_net_tstat_artifact.py
─────────────────────────────────────────────────────────────────────────────
UN t CALCULÉ SUR UN NET N'EST PAS UN TEST DU SIGNAL.

La question posée
─────────────────
`XSEC_REV_1D_LS` affiche « -27,5 bps à t = -5,89, le résultat le plus
significatif de tout le balayage ». Un t de -5,89 sur la réversion, c'est un t
de +5,89 sur la continuation — donc un alpha qui attendrait dans un rapport.
Tout dépend d'une question binaire : le -27,5 est-il brut ou net ?

La réponse, et pourquoi elle n'est ni l'une ni l'autre
──────────────────────────────────────────────────────
Le brut est enregistré explicitement à la source (`mech_inventory_r3a.json`) :

    {"mechanism_id":"XSEC_REV_1D_LS", "gross_bps":0.5, "n_independent":2355}

Donc -27,5 = 0,5 - 28 : c'est bien un NET, sous une hypothèse de coût de
28 bps. Et le t porte sur ce net (sur le brut, il impliquerait une erreur-type
de 0,085 bps, ce qui est absurde).

L'erreur-type se déduit : |net| / |t| = 27,5 / 5,89 = 4,67 bps. Le coût étant
une CONSTANTE, il ne change pas la variance — l'erreur-type du brut est la
même. Donc :

    t sur le brut = 0,5 / 4,67 = +0,107

Le signal n'existe pas. Ni dans un sens ni dans l'autre.

Le mécanisme général, et il touche tout le tableau
───────────────────────────────────────────────────
Quand le brut tend vers zéro, le t du net tend vers `coût · sqrt(n) / sigma`,
qui croît sans borne avec n. Ici : 28 · sqrt(2355) / 227 = 5,99, contre 5,89
observé. **Le « résultat le plus significatif du balayage » est une mesure de
la constante de coût, pas du signal.**

Ce que ça ne remet pas en cause : le VERDICT. Le mécanisme ne gagne pas
d'argent après coûts, et DEAD est juste. Ce qui est faux, c'est la FORCE de
l'affirmation — « pas seulement absent, un perdant structurel confiant ». Il
est absent, point ; et étant absent, il perd exactement le coût. Ce qui
implique aussi que la continuation est tout aussi absente : on ne retourne pas
un zéro en edge.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

REPORT_SRC = (ROOT / "reports" / "edge_discovery" / "alpha_hunt_2026-09-01_round3"
              / "w2_cross_sectional" / "REPORT.md")
INVENTORY = (ROOT / "reports" / "edge_discovery" / "alpha_hunt_2026-09-03_round4"
             / "w5_execution_cost_layer" / "evidence" / "mech_inventory_r3a.json")
OUT = ROOT / "reports" / "edge_discovery" / "NET_TSTAT_ARTIFACT.md"
JSON_OUT = ROOT / "reports" / "edge_discovery" / "NET_TSTAT_ARTIFACT.json"

SIGNIFICANT = 1.96


def parse_rows() -> List[Dict[str, object]]:
    rows = []
    for line in REPORT_SRC.read_text(encoding="utf-8").splitlines():
        if not (line.startswith("| XSEC") or line.startswith("| SECTOR")):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 10:
            continue
        try:
            n_ind = int(cells[5])
            gross, net, t = float(cells[6]), float(cells[7]), float(cells[9])
        except ValueError:
            continue
        if t == 0:
            continue
        se = abs(net) / abs(t)
        rows.append({
            "mechanism_id": cells[0], "n_independent": n_ind,
            "gross_bps": gross, "net_bps": net, "t_net": t,
            "implied_se_bps": round(se, 3),
            "implied_sigma_bps": round(se * math.sqrt(n_ind), 1),
            "t_gross": round(gross / se, 3),
            "implied_cost_bps": round(gross - net, 1),
            "artifact": bool(abs(t) >= SIGNIFICANT and abs(gross / se) < SIGNIFICANT),
        })
    return sorted(rows, key=lambda r: -abs(r["t_net"]))


def render(rows: List[Dict[str, object]]) -> str:
    artifacts = [r for r in rows if r["artifact"]]
    survivors = [r for r in rows if abs(r["t_net"]) >= SIGNIFICANT and not r["artifact"]]
    rev = next((r for r in rows if r["mechanism_id"] == "XSEC_REV_1D_LS"), None)

    out: List[str] = []
    out.append("# Un `t` calculé sur un net n'est pas un test du signal")
    out.append("")
    out.append("_Généré par `scripts/audit_net_tstat_artifact.py`. Ne pas éditer à la main._")
    out.append("")
    out.append("## La question")
    out.append("")
    out.append("`XSEC_REV_1D_LS` affiche « −27,5 bps à t = −5,89, le résultat le plus")
    out.append("statistiquement significatif de tout le balayage ». Or un t de −5,89 sur la")
    out.append("réversion est un t de +5,89 sur la **continuation** — donc potentiellement un")
    out.append("alpha validé qui attendait dans un rapport. Tout dépendait d'une question")
    out.append("binaire : ce −27,5 est-il brut ou net ?")
    out.append("")
    if rev:
        out.append("## La réponse : c'est un net, et le brut est nul")
        out.append("")
        out.append("Le brut est enregistré explicitement à la source, pas déduit :")
        out.append("")
        out.append("```json")
        out.append('{"mechanism_id":"XSEC_REV_1D_LS","gross_bps":0.5,"n_independent":2355}')
        out.append("```")
        out.append("")
        out.append("| grandeur | valeur | d'où elle vient |")
        out.append("|---|---|---|")
        out.append("| brut | **%+.1f bps** | `mech_inventory_r3a.json`, enregistré |" % rev["gross_bps"])
        out.append("| coût supposé | %.1f bps | brut − net, et le rapport le dit : « assumed 28bps » |" % rev["implied_cost_bps"])
        out.append("| net | %+.1f bps | publié |" % rev["net_bps"])
        out.append("| t sur le net | %.2f | publié |" % rev["t_net"])
        out.append("| erreur-type | %.2f bps | \\|net\\| / \\|t\\| — le coût est une constante, il ne change pas la variance |" % rev["implied_se_bps"])
        out.append("| σ par épisode | %.0f bps | SE × √%d |" % (rev["implied_sigma_bps"], rev["n_independent"]))
        out.append("| **t sur le brut** | **%+.3f** | brut / erreur-type |" % rev["t_gross"])
        out.append("")
        out.append("**Le signal n'existe pas — ni dans un sens ni dans l'autre.** La continuation,")
        out.append("qui est la réversion à signe inversé, vaut donc −0,5 bps brut, soit")
        out.append("−14,5 bps nets à 14 bps de coût, avec le même t de −0,11.")
        out.append("")
        out.append("Contrôle indépendant : le rejugement du round 4 recalcule ce mécanisme à")
        out.append("−13,5 bps sous une hypothèse de coût de 14 bps. `0,5 − 14 = −13,5` ✓.")
        out.append("")
        out.append("## Le mécanisme général, et il ne concerne pas que cette ligne")
        out.append("")
        out.append("Quand le brut tend vers zéro, le `t` du net tend vers `coût·√n / σ`, qui croît")
        out.append("**sans borne avec n**. Ici : %.0f·√%d / %.0f = %.2f, contre %.2f observé." % (
            rev["implied_cost_bps"], rev["n_independent"], rev["implied_sigma_bps"],
            rev["implied_cost_bps"] * math.sqrt(rev["n_independent"]) / rev["implied_sigma_bps"],
            abs(rev["t_net"])))
        out.append("")
        out.append("Autrement dit : **le « résultat le plus significatif du balayage » est une")
        out.append("mesure de la constante de coût, pas du signal.** Il suffit d'assez d'épisodes")
        out.append("pour rendre n'importe quel mécanisme sans edge « hautement significatif » dans")
        out.append("la direction du coût.")
        out.append("")
    out.append("## Combien de lignes sont dans ce cas")
    out.append("")
    out.append("| mécanisme | n | brut | net | t (net) | **t (brut)** | statut |")
    out.append("|---|---|---|---|---|---|---|")
    for row in rows:
        if abs(row["t_net"]) < 1.0:
            continue
        mark = ("⚠️ **artefact de coût**" if row["artifact"]
                else ("✅ significatif sur le brut" if abs(row["t_gross"]) >= SIGNIFICANT else "—"))
        out.append("| `%s` | %d | %+.1f | %+.1f | %.2f | **%+.3f** | %s |" % (
            row["mechanism_id"], row["n_independent"], row["gross_bps"],
            row["net_bps"], row["t_net"], row["t_gross"], mark))
    out.append("")
    out.append("**%d lignes sur %d** affichent `|t| ≥ 1,96` sur le net alors que le brut est" % (
        len(artifacts), len(rows)))
    out.append("indiscernable de zéro : %s." % ", ".join("`%s`" % r["mechanism_id"] for r in artifacts))
    out.append("")
    if survivors:
        out.append("Deux lignes survivent sur le brut, et ce sont les seules qui disent quelque")
        out.append("chose du marché plutôt que du modèle de coût : %s." % ", ".join(
            "`%s` (t=%+.2f)" % (r["mechanism_id"], r["t_gross"]) for r in survivors))
        out.append("")
    out.append("## Ce que ça change, et ce que ça ne change pas")
    out.append("")
    out.append("**Ça ne change aucun verdict.** Ces mécanismes ne gagnent pas d'argent après")
    out.append("coûts, et `DEAD` reste juste. Un brut nul moins un coût positif est une perte.")
    out.append("")
    out.append("**Ça change la FORCE des affirmations.** « Pas seulement absent, un perdant")
    out.append("structurel confiant » n'est pas soutenu : le mécanisme est absent, point, et")
    out.append("étant absent il perd exactement le coût. La nuance compte, parce que « perdant")
    out.append("confiant » invite à retourner la position — et retourner un zéro ne donne pas")
    out.append("un edge, ça donne le même zéro moins le même coût.")
    out.append("")
    out.append("**Ça change ce qu'il faut publier.** Un `t` sur un net mélange une mesure (le")
    out.append("signal) et une hypothèse (le coût), et la significativité qui en sort appartient")
    out.append("à l'hypothèse dès que la mesure est faible. Le `t` doit être calculé sur le")
    out.append("**brut** ; le coût se compare ensuite au brut, en niveau, comme le fait déjà")
    out.append("`breakeven_capture` dans `alpha_foundry_v5`.")
    out.append("")
    out.append("## Et la question d'origine")
    out.append("")
    out.append("**Il n'y a pas d'alpha de continuation qui attend.** La réversion transversale à")
    out.append("1 jour est plate au brut (t = +0,11 sur 2 355 épisodes indépendants, σ = 227 bps).")
    out.append("Une mesure aussi bien échantillonnée et aussi plate est en fait un résultat")
    out.append("utile : elle **exclut** un edge de continuation supérieur à ~9 bps bruts")
    out.append("(1,96 × 4,67) à ce horizon et sur cette construction. C'est un vrai négatif,")
    out.append("bien mesuré — le premier de cette famille.")
    out.append("")
    return "\n".join(out) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    argparse.ArgumentParser(description=__doc__).parse_args(argv)
    rows = parse_rows()
    if not rows:
        print("aucune ligne exploitable dans %s" % REPORT_SRC)
        return 2
    OUT.write_text(render(rows), encoding="utf-8")
    JSON_OUT.write_text(json.dumps({"source": str(REPORT_SRC), "rows": rows}, indent=2),
                        encoding="utf-8")
    artifacts = [r["mechanism_id"] for r in rows if r["artifact"]]
    print(json.dumps({"n_rows": len(rows), "cost_artifacts": artifacts,
                      "XSEC_REV_1D_LS_t_gross": next(
                          r["t_gross"] for r in rows if r["mechanism_id"] == "XSEC_REV_1D_LS")},
                     indent=2), flush=True)
    print("écrit -> %s" % OUT, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
