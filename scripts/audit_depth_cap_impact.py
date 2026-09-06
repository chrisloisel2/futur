#!/usr/bin/env python3
"""
scripts/audit_depth_cap_impact.py
─────────────────────────────────────────────────────────────────────────────
CE QUE LE PLAFOND DE PROFONDEUR REFUSE, ALPHA PAR ALPHA.

Le plafond du simulateur était adossé à l'open interest — un stock de
positions ouvertes, pas une profondeur de carnet. Il a mordu 1,0 % du temps.
La profondeur médiane au meilleur limite du frozen-50 est de ~1 048 $, et
ARUSDT cote 476 $ : un ordre de 7 400 $ sur ce nom ne pouvait pas être rempli
au prix supposé, et l'était quand même.

Ce script rejoue les notionnels réellement exécutés par les cinq portefeuilles
contre le nouveau plafond, et chiffre ce qui n'aurait pas pu être rempli AU PAS
OÙ IL A ÉTÉ DEMANDÉ.

⚠️ PRÉCISION IMPORTANTE SUR LE MOT « REFUSÉ »
    Le plafond ne détruit pas l'ordre : il le rend PARTIEL. Le reste est
    reporté sur les pas suivants, aux prix de ces pas — exactement ce que fait
    un vrai carnet. « Refusé » ici veut donc dire « n'aurait pas été rempli à ce
    pas-là, ni à ce prix-là », pas « perdu ». L'effet sur le PnL n'est pas une
    amputation, c'est un DÉCALAGE d'exécution : le portefeuille atteint sa
    cible plus tard et à un prix qui a bougé.

⚠️ Les chiffres vont EMPIRER, et c'est le but. Une capacité qui ne mord jamais
ne mesure rien ; ce qu'elle laissait passer était une fiction confortable.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd

from src.institutional.live_alpha_lab.execution_adapter import depth_notional
from src.institutional.live_alpha_lab.orders import (
    DEPTH_CAP_EFFECTIVE_FROM,
    MAX_FILL_FRACTION_OF_DEPTH,
)

PORTFOLIOS = ROOT / "reports" / "live_alpha_lab" / "portfolios"
OUT = ROOT / "reports" / "live_alpha_lab" / "DEPTH_CAP_IMPACT.md"
JSON_OUT = ROOT / "reports" / "live_alpha_lab" / "DEPTH_CAP_IMPACT.json"


def alphas_of(raw: str) -> List[str]:
    try:
        return sorted({str(x.get("alpha_id")) for x in json.loads(raw or "[]")})
    except Exception:
        return []


def load_deltas() -> pd.DataFrame:
    rows = []
    for path in sorted(PORTFOLIOS.glob("*/intent_ledger.parquet")):
        df = pd.read_parquet(path)
        df["portfolio_id"] = path.parent.name
        rows.append(df)
    if not rows:
        return pd.DataFrame()
    out = pd.concat(rows, ignore_index=True)
    out["notional_usd"] = pd.to_numeric(out["executed_delta"], errors="coerce").abs()
    out = out[out["notional_usd"] > 0].copy()
    out["alphas"] = out["alpha_intents"].map(alphas_of)
    out["depth_usd"] = out["instrument"].map(depth_notional)
    out["cap_usd"] = out["depth_usd"] * MAX_FILL_FRACTION_OF_DEPTH
    out["capped"] = out["cap_usd"].notna() & (out["notional_usd"] > out["cap_usd"])
    out["refused_usd"] = np.where(out["capped"], out["notional_usd"] - out["cap_usd"], 0.0)
    return out


def summarize(df: pd.DataFrame, label: str) -> Dict[str, object]:
    measurable = df[df["cap_usd"].notna()]
    n = int(len(measurable))
    return {
        "scope": label,
        "n_orders": int(len(df)),
        "n_measurable": n,
        "n_no_probe": int(len(df) - n),
        "n_capped": int(measurable["capped"].sum()),
        "pct_capped": round(100.0 * float(measurable["capped"].sum()) / max(n, 1), 1),
        "notional_usd": round(float(df["notional_usd"].sum()), 0),
        "refused_usd": round(float(df["refused_usd"].sum()), 0),
        "pct_notional_refused": round(
            100.0 * float(df["refused_usd"].sum()) / max(float(df["notional_usd"].sum()), 1e-9), 1),
        "median_order_usd": round(float(df["notional_usd"].median()), 0),
        "median_depth_usd": (round(float(measurable["depth_usd"].median()), 0) if n else None),
    }


def render(overall: Dict[str, object], by_alpha: List[Dict[str, object]],
           by_symbol: List[Dict[str, object]]) -> str:
    out: List[str] = []
    out.append("# Ce que le plafond de profondeur refuse")
    out.append("")
    out.append("_Généré par `scripts/audit_depth_cap_impact.py`. Ne pas éditer à la main._")
    out.append("")
    out.append("Le plafond du simulateur était adossé à l'**open interest** — un stock de")
    out.append("positions, pas une profondeur de carnet. Il mordait 1,0 % du temps. Il est")
    out.append("désormais adossé au notionnel affiché au **meilleur limite**, c'est-à-dire")
    out.append("exactement la taille pour laquelle le spread coté a été observé. Au-delà, le")
    out.append("modèle de coût (mid moins deux bps) ne repose plus sur rien.")
    out.append("")
    out.append("**Frontière de segment : `%s`.** Les ordres antérieurs gardent" % DEPTH_CAP_EFFECTIVE_FROM[:10])
    out.append("la règle sous laquelle ils ont été produits, et chaque ordre porte désormais sa")
    out.append("politique (`cap_policy`). Une série qui traverse cette date doit être segmentée —")
    out.append("mélanger deux régimes d'exécution dans une même courbe d'équité rendrait les deux")
    out.append("illisibles.")
    out.append("")
    out.append("## Ce que le passé aurait donné sous la nouvelle règle")
    out.append("")
    out.append("| grandeur | valeur |")
    out.append("|---|---|")
    out.append("| ordres rejoués | %d |" % overall["n_orders"])
    out.append("| dont mesurables (sonde de profondeur disponible) | %d |" % overall["n_measurable"])
    out.append("| sans sonde — **non plafonnés, pas « larges »** | %d |" % overall["n_no_probe"])
    out.append("| **ordres qui dépassent la profondeur** | **%d, soit %.1f %%** |" % (
        overall["n_capped"], overall["pct_capped"]))
    out.append("| notionnel exécuté | %s $ |" % f"{overall['notional_usd']:,.0f}".replace(",", " "))
    out.append("| **notionnel non remplissable au pas demandé** | **%s $, soit %.1f %%** |" % (
        f"{overall['refused_usd']:,.0f}".replace(",", " "), overall["pct_notional_refused"]))
    out.append("| ordre médian | %s $ |" % f"{overall['median_order_usd']:,.0f}".replace(",", " "))
    out.append("| profondeur médiane | %s $ |" % f"{overall['median_depth_usd']:,.0f}".replace(",", " "))
    out.append("")
    out.append("Le plafond passe de mordre **1,0 %%** des ordres à en mordre **%.1f %%**." % overall["pct_capped"])
    out.append("")
    out.append("## Par alpha")
    out.append("")
    out.append("| alpha | ordres | plafonnés | notionnel | reporté | % reporté |")
    out.append("|---|---|---|---|---|---|")
    for row in by_alpha:
        out.append("| `%s` | %d | %d (%.1f %%) | %s $ | %s $ | **%.1f %%** |" % (
            row["scope"], row["n_orders"], row["n_capped"], row["pct_capped"],
            f"{row['notional_usd']:,.0f}".replace(",", " "),
            f"{row['refused_usd']:,.0f}".replace(",", " "),
            row["pct_notional_refused"]))
    out.append("")
    out.append("## Les symboles où ça se joue")
    out.append("")
    out.append("| symbole | profondeur | ordre médian | ordres plafonnés | % du notionnel reporté |")
    out.append("|---|---|---|---|---|")
    for row in by_symbol[:15]:
        out.append("| %s | %s $ | %s $ | %d | **%.1f %%** |" % (
            row["scope"],
            f"{row['median_depth_usd']:,.0f}".replace(",", " ") if row["median_depth_usd"] else "—",
            f"{row['median_order_usd']:,.0f}".replace(",", " "),
            row["n_capped"], row["pct_notional_refused"]))
    out.append("")
    out.append("_Les 15 symboles au notionnel reporté le plus élevé._")
    out.append("")
    out.append("## Comment lire ça")
    out.append("")
    out.append("**« Reporté », pas « perdu ».** Le plafond rend l'ordre PARTIEL ; le reste se")
    out.append("remplit aux pas suivants, aux prix de ces pas. L'effet sur le PnL n'est donc pas")
    out.append("une amputation du notionnel mais un DÉCALAGE d'exécution : le portefeuille")
    out.append("atteint sa cible plus tard, à un prix qui a bougé entre-temps. Sur un signal à")
    out.append("horizon 4 h, un report de plusieurs pas consomme une part appréciable de")
    out.append("l'horizon — et c'est précisément ce que l'ancienne règle rendait invisible.")
    out.append("")
    out.append("**Les chiffres de PnL vont empirer, et c'est le but.** Ce qui passait avant était")
    out.append("une fiction : un ordre de plusieurs milliers de dollars sur un carnet qui en")
    out.append("affiche quelques centaines n'était pas rempli au mid moins deux bps. Toute mesure")
    out.append("d'edge net produite sous l'ancienne règle héritait de cette fiction.")
    out.append("")
    out.append("**L'ordre médian ne fait que %s $.** Le taux de report en notionnel (%.1f %%) est" % (
        f"{overall['median_order_usd']:,.0f}".replace(",", " "), overall["pct_notional_refused"]))
    out.append("donc porté par une minorité de gros ordres, pas par le flux courant. C'est une")
    out.append("bonne nouvelle pour la faisabilité et une mauvaise pour les mesures passées : ce")
    out.append("sont les grosses positions, celles qui pèsent le plus dans le PnL, qui étaient")
    out.append("les plus fictives.")
    out.append("")
    out.append("**Un symbole sans sonde n'est pas un symbole liquide.** Le plafond est fail-open")
    out.append("— aucune sonde, aucun plafond — parce qu'un plafond inventé serait pire qu'aucun.")
    out.append("Mais %d ordres tombent dans ce cas et leur capacité est INCONNUE, pas large." % overall["n_no_probe"])
    out.append("")
    out.append("**La profondeur au meilleur limite est une borne basse.** Un ordre valant trois")
    out.append("fois le meilleur limite ne paie pas forcément trois fois plus : les niveaux")
    out.append("suivants sont souvent proches. La vraie réponse demande un carnet L2 complet, que")
    out.append("`data/microstructure_reduced` ne capture que pour BTC, ETH et SOL. Ce plafond est")
    out.append("donc conservateur par construction — dans la direction où une porte doit l'être.")
    out.append("")
    return "\n".join(out) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)

    df = load_deltas()
    if df.empty:
        print("aucun intent ledger exploitable")
        return 2

    overall = summarize(df, "TOTAL")

    per_alpha: Dict[str, List[int]] = defaultdict(list)
    for idx, alphas in zip(df.index, df["alphas"]):
        for alpha in alphas:
            per_alpha[alpha].append(idx)
    by_alpha = sorted(
        (summarize(df.loc[rows], alpha) for alpha, rows in per_alpha.items()),
        key=lambda r: -r["pct_notional_refused"])

    by_symbol = sorted(
        (summarize(g, symbol) for symbol, g in df.groupby("instrument")),
        key=lambda r: -r["refused_usd"])

    OUT.write_text(render(overall, by_alpha, by_symbol), encoding="utf-8")
    JSON_OUT.write_text(json.dumps({
        "effective_from": DEPTH_CAP_EFFECTIVE_FROM,
        "depth_fraction": MAX_FILL_FRACTION_OF_DEPTH,
        "overall": overall, "by_alpha": by_alpha, "by_symbol": by_symbol[:30],
    }, indent=2), encoding="utf-8")
    print(json.dumps(overall, indent=2), flush=True)
    print("écrit -> %s" % OUT, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
