"""Génère les REPORT.md (structure §4.2 du briefing : 1 Méthodologie, 2 Checklist,
3 Primary + perturbations, 4 Déclustering, 5 Capacité, 6 Fréquence/ETA, 7 Verdict)
pour les candidats qui n'ont qu'un RESULTS.json.

BTC_LEAD_ALT_CASCADE et LIQ_CASCADE_FAR_FROM_LOW ont des rapports rédigés à la main
(analyses spécifiques) et ne sont pas régénérés.
"""
from __future__ import annotations

import json
import os

BASE = "/home/qbee/futur/reports/edge_discovery/validation_2026-09"
HANDWRITTEN = {"BTC_LEAD_ALT_CASCADE", "LIQ_CASCADE_FAR_FROM_LOW"}

METHOD = {
    "cross_sectional": (
        "Panel quotidien reconstruit indépendamment depuis les barres 5 m "
        "`data_v2/normalized/perp_ohlcv` (close = dernière barre 5 m du jour UTC, dv = somme du "
        "quote_asset_volume) : **365 980 lignes, 312 symboles, 2020-01-01 → 2026-07-31**. "
        "Univers PIT à chaque rebalancement : âge de listing ≥ 30 j (`listings_calendar.parquet`, "
        "1 seul symbole en fallback), médiane causale 30 j du dollar-volume ≥ $1 M (fenêtre pleine "
        "exigée), `n_eligible ≥ 20`. Médiane 129 noms éligibles, min 0, max 258, première date "
        "éligible 2020-03-14 — l'univers croît réellement, il n'est pas copié à rebours. "
        "Sortie au dernier close disponible dans la fenêtre (un délisté n'est jamais retiré : "
        "pas de biais du survivant). Winsorisation 1 %/99 % sur la cross-section éligible complète. "
        "**Statistique de verdict = l'EXCESS sur le bras B** (univers éligible équipondéré), "
        "jamais le rendement contre zéro."),
    "liquidation": (
        "Population d'événements depuis `data/events/*_dataset.parquet`, filtrée sur "
        "`label_full == True`, horizon `fwd_4h`, à partir de 2022-01-01 UTC. Tout "
        "conditionnement utilise une règle de centile **causale** sur une fenêtre glissante de "
        "365 j (≥ 200 événements antérieurs exigés, sinon l'événement est écarté et compté) — "
        "jamais un centile in-sample. Chaque test est un contraste **bras A − bras B sur la même "
        "population**, jamais un « A > 0 »."),
    "positioning": (
        "Population d'événements `crowding_dataset.parquet` (`CROWD_WASHOUT`), horizon `fwd_4h`, "
        "à partir de 2022-01-01 UTC, avec conditionnement par centile causal 365 j. Contraste "
        "bras A − bras B sur la même population."),
    "relative_value": (
        "Même panel PIT que la famille cross-sectionnelle (voir "
        "`XSEC_MOMENTUM_HORIZON_EXTENSION`). La carte sectorielle est construite par le "
        "validateur (`_lib/sector_map_v5.py`, 10 secteurs + `OTHER`), jamais lue depuis un "
        "`sector_map.py` de découverte ; sa sensibilité est une perturbation obligatoire. "
        "BTC et ETH sont exclus du classement sectoriel (ce sont des paniers à eux seuls). "
        "Statistique de verdict = l'EXCESS sur l'univers éligible équipondéré."),
}

CHECKLIST = {
    "cross_sectional": [
        ("Causalité des features", "Toute fenêtre se termine à `d` inclus ou avant ; aucun close postérieur n'entre dans un signal. Le panel n'est jamais rempli par interpolation (`min_periods` = fenêtre pleine)."),
        ("Croissance d'univers / âge de listing", "PIT strict : `d >= onboard_ts + 30 j`, éligibilité recalculée à chaque date. n_eligible passe de 21 (2020) à 258 (2025-26)."),
        ("Délistages / renommages", "Sortie forcée au dernier close disponible dans la fenêtre de détention — un nom qui disparaît est réalisé, pas supprimé."),
        ("Unités", "Rendements en décimal → bps (×1e4). Dollar-volume en USDT bruts."),
        ("Bras A − bras B", "Appliqué : le verdict porte sur l'excess vs l'univers éligible équipondéré, pas sur le rendement brut."),
        ("Déclustering", "Appliqué aux 3 niveaux, voir §4."),
        ("Coûts", "14 bps (une jambe) / 28 bps (long-short), + stress à coût doublé et perturbation à +50 %."),
    ],
    "liquidation": [
        ("Causalité de la règle de seuil", "Centile calculé sur `[t − 365 j, t)` strictement antérieur ; les événements sans ≥ 200 antécédents sont écartés, jamais imputés."),
        ("Causalité des features", "Toutes les features de conditionnement sont des mesures antérieures à l'événement (as-of backward dans le dataset source)."),
        ("Unités", "`fwd_4h` en décimal → bps (×1e4) ; vérifié sur la moyenne de population."),
        ("Bras A − bras B", "Appliqué systématiquement sur la même population, avec Welch sur les moyennes d'épisode ET régression cluster-robuste au niveau événement."),
        ("Déclustering", "3 niveaux, voir §4. C'est le point critique de cette famille."),
        ("Double lecture", "Chaque résultat est produit en pondération épisode ET en pondération événement avec SE cluster-robuste — un candidat n'est retenu que si les deux tiennent."),
    ],
}
CHECKLIST["positioning"] = CHECKLIST["liquidation"]
CHECKLIST["relative_value"] = CHECKLIST["cross_sectional"]

DECLUST = {
    "cross_sectional": "**L1** = position nom × rebalancement · **L2** = période de rebalancement non chevauchante · **L3 (inférence)** = mois calendaire. t cluster-robuste (Liang-Zeger) sur L3, block bootstrap à blocs mensuels (10 000 tirages).",
    "relative_value": "**L1** = position nom × rebalancement · **L2** = période de rebalancement · **L3 (inférence)** = mois calendaire. t cluster-robuste sur L3, block bootstrap mensuel (10 000 tirages).",
    "liquidation": "**L1** = même symbole, chaîne < 24 h · **L2** = jour calendaire UTC · **L3 (inférence)** = **épisode cross-symbole chaîné, gap < 4 h**. Une cascade market-wide touche des dizaines d'alts dans les mêmes minutes : les compter séparément surestime N d'un ordre de grandeur. t cluster-robuste sur L3, block bootstrap par épisode.",
}
DECLUST["positioning"] = DECLUST["liquidation"]


def fmt(v, nd=2):
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.{nd}f}"
    return str(v)


def main():
    n = 0
    for cid in sorted(os.listdir(BASE)):
        d = f"{BASE}/{cid}"
        rp, mp = f"{d}/RESULTS.json", f"{d}/REPORT.md"
        if cid in HANDWRITTEN or not os.path.isdir(d) or not os.path.exists(rp):
            continue
        if os.path.exists(mp):
            continue
        r = json.load(open(rp))
        fam = r["family"]
        yb = r.get("year_by_year") or {}
        years = " · ".join(f"{k} **{fmt(v,1)}**" for k, v in sorted(yb.items()))

        checks = "\n".join(f"| {a} | {b} |" for a, b in CHECKLIST[fam])
        cap_xsec = ("Perps Binance liquides sous plancher de dollar-volume causal ≥ $1 M ; un book "
                    "de $300k équipondéré reste très en deçà de toute participation problématique "
                    "(mesuré à 0,19 % de l'ADV au 5e centile pour la famille cross-sectionnelle).")
        cap_event = ("Mécanisme événementiel sur perps majeurs ; la contrainte est le nombre "
                     "d'événements, pas la profondeur de carnet. Non chiffré plus finement "
                     "(noté comme tel).")
        capacity = cap_xsec if fam in ("cross_sectional", "relative_value") else cap_event
        gross = r['validation_net_bps'] + 14 if r['validation_net_bps'] is not None else None
        n_pos = sum(1 for v in yb.values() if v and v > 0)
        tags = ', '.join(f'`{t}`' for t in r['secondary_tags']) or '—'
        md = f"""# {cid} — Rapport de validation indépendante

**Validateur :** Alpha Validation Factory wave 2, worker unique (session futur-49), 2026-09-03
**Réclamation testée :** {r['discovery_net_bps']} bps net (source : liste de mission / rapport de découverte).
**Discipline d'indépendance :** aucun script ni dossier `evidence/` de découverte n'a été ouvert.
Réimplémentation entière depuis la définition économique, harnais commun `../_lib/validation_lib.py`.

---

## 1. Méthodologie

{METHOD[fam]}

## 2. Checklist de vérification

| Contrôle | Résultat |
|---|---|
{checks}

## 3. Résultat primaire

| Grandeur | Valeur |
|---|---|
| gross / **net** / net stress | {fmt(gross)} / **{fmt(r['validation_net_bps'])}** / {fmt(r['validation_net_bps_stress28'])} bps |
| profit factor | {fmt(r['pf'], 3)} |
| **t cluster-robuste (L3)** | **{fmt(r['t_stat_declustered'], 3)}** |
| bootstrap CI95 | {r['bootstrap_ci95']} |
| bootstrap 5e centile | {fmt(r['bootstrap_p05'])} |
| années positives | {n_pos}/{len(yb)} |
| hors meilleure année | {fmt(r['ex_best_year_net_bps'])} bps |
| pire épisode | {fmt(r['worst_episode_bps'])} bps |
| drawdown cumulé max | {fmt(r['max_drawdown_bps_cumule'])} bps |

Année par année (net) : {years or '—'}

## 4. Déclustering

{DECLUST[fam]}

| Niveau | N |
|---|---|
| brut | {r['n_raw']} |
| L1 | {r['n_independent_L1']} |
| L2 | {r['n_independent_L2']} |
| **L3 (inférence)** | **{r['n_independent_L3']}** |

## 5. Capacité

{capacity}

## 6. Fréquence, N_required, ETA

| Champ | Valeur |
|---|---|
| taux historique (2 ans) | {r['historical_event_rate']} |
| taux récent (6 mois) | {r['recent_event_rate']} |
| taux conservateur | {r['conservative_event_rate']} |
| `n_required_statistical` | {r['n_required_statistical']} |
| `minimum_calendar_days` | {r['minimum_calendar_days']} |
| `eta_p50` | {r['eta_p50']} |
| **`eta_conservative`** | **{r['eta_conservative']}** |
| `confirmable_in_horizon` (< 3 ans) | **{r['confirmable_in_horizon']}** |

## 7. Verdict

# `{r['verdict']}`

Tags secondaires : {tags}
`sign_correction_required` : **{r['sign_correction_required']}**

{r['validation_caveats']}

**`recommended_next_step` : `{r['recommended_next_step']}`**

---

*Chiffres bruts complets, perturbations et contrôles de chevauchement : `RESULTS.json`.
Scripts ré-exécutables : `../_lib/`.*
"""
        open(mp, "w").write(md)
        n += 1
        print(f"  écrit {cid}/REPORT.md")
    print(f"{n} rapports générés")


if __name__ == "__main__":
    main()
