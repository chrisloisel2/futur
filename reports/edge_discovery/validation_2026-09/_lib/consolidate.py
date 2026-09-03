"""Consolide les JSON bruts des expériences en livrables du briefing §4 :
un `RESULTS.json` par candidat + le `WAVE2_SCOREBOARD.md`.

Les verdicts sont appliqués MÉCANIQUEMENT depuis les critères préenregistrés — aucun
verdict n'est saisi à la main, pour qu'ils restent reproductibles depuis les chiffres.
"""
from __future__ import annotations

import json
import os

BASE = "/home/qbee/futur/reports/edge_discovery/validation_2026-09"
RAW = f"{BASE}/_lib/out"


def load(name):
    p = f"{RAW}/{name}"
    return json.load(open(p)) if os.path.exists(p) else {}


v3, v5, v2 = load("v3_raw.json"), load("v5_raw.json"), load("v2_raw.json")
ewc, ef1, ef2 = (load("event_weighted_cluster.json"), load("event_family_raw.json"),
                 load("event_family2_raw.json"))
ffl_cmp, ffl_gap = load("v2_ffl_compare.json"), load("v2_ffl_gap_sensitivity.json")


def verdict_xsec(g: dict) -> tuple[str, list[str]]:
    """Critères des preregs cross-sectionnels : t_L3 >= 1.645 ET bootstrap p05 > 0 ET
    net28 > 0 ET >= 4/7 années. t < 1.0 -> REJECTED ; 1.0 <= t < 1.645 -> NEEDS_MORE_RESEARCH."""
    t = g.get("t_stat_declustered")
    tags = []
    if t is None:
        return "IMPLEMENTATION_BLOCKED", tags
    if g.get("net_bps_stress28", 0) <= 0:
        tags.append("COST_FRAGILE")
    if g.get("n_years_positive", 0) < 4:
        tags.append("REGIME_DEPENDENT")
    if not g.get("confirmable_in_horizon", False):
        tags.append("UNCONFIRMABLE_IN_HORIZON")
    if t >= 1.645 and g.get("bootstrap_p05", -1) > 0 and g.get("net_bps", 0) > 0:
        return "VALIDATED_FOR_FORWARD", tags
    if t < 1.0:
        return "REJECTED", tags
    return "NEEDS_MORE_RESEARCH", tags


def base_fields(cid, family, disc, g, report, next_step, verdict, tags, caveats,
                overlap=None, extra=None):
    out = {
        "candidate_id": cid,
        "family": family,
        "verdict": verdict,
        "validated_for_forward": verdict == "VALIDATED_FOR_FORWARD",
        "confirmable_in_horizon": g.get("confirmable_in_horizon"),
        "sign_correction_required": False,
        "secondary_tags": tags,
        "discovery_net_bps": disc,
        "validation_net_bps": g.get("net_bps"),
        "validation_net_bps_stress28": g.get("net_bps_stress28"),
        "pf": g.get("pf"),
        "n_raw": g.get("n_raw"),
        "n_independent_L1": g.get("n_independent_L1"),
        "n_independent_L2": g.get("n_independent_L2"),
        "n_independent_L3": g.get("n_independent_L3"),
        "n_validation_independent": g.get("n_independent_L3"),
        "l3_definition": g.get("l3_definition"),
        "t_stat_declustered": g.get("t_stat_declustered"),
        "bootstrap_ci95": g.get("bootstrap_ci95"),
        "bootstrap_p05": g.get("bootstrap_p05"),
        "year_by_year": g.get("year_by_year"),
        "ex_best_year_net_bps": g.get("ex_best_year_net_bps"),
        "worst_episode_bps": g.get("worst_episode_bps"),
        "max_drawdown_bps_cumule": g.get("max_drawdown_bps_cumule"),
        "historical_event_rate": g.get("historical_event_rate"),
        "recent_event_rate": g.get("recent_event_rate"),
        "conservative_event_rate": g.get("conservative_event_rate"),
        "n_required_statistical": g.get("n_required_statistical"),
        "minimum_calendar_days": g.get("minimum_calendar_days"),
        "eta_p50": g.get("eta_p50"),
        "eta_conservative": g.get("eta_conservative"),
        "overlap_with_existing_live": overlap,
        "validation_caveats": caveats,
        "recommended_next_step": next_step,
        "validation_report": report,
        "validated_by": "Alpha Validation Factory wave 2, worker unique (session futur-49), 2026-09-03",
    }
    if extra:
        out.update(extra)
    return out


CANDIDATES = []

# ── #5 BTC_LEAD_ALT_CASCADE ────────────────────────────────────────────────
g = v2["BTC_LEAD_ALT_CASCADE"]["T1_shock_alone"]
ew = ewc["BTC_LEAD_ALT_CASCADE"]
CANDIDATES.append(base_fields(
    "BTC_LEAD_ALT_CASCADE", "liquidation", 33.0, g,
    f"{BASE}/BTC_LEAD_ALT_CASCADE/REPORT.md", "FREEZE_AND_LAUNCH_SHADOW",
    "VALIDATED_FOR_FORWARD", ["UNCONFIRMABLE_IN_HORIZON"],
    "Passe les 5 critères préenregistrés dans les DEUX conventions de pondération "
    "(épisode t=3.32 ; événement t_cluster=1.85). SE naïve surestimée x2.2 -> preuve 12x plus "
    "mince que publiée (259 épisodes vs N_indep 3097 réclamé), mais l'edge survit. 2025 est "
    "la seule année négative (-40.0 bps) et c'est la plus récente complète : à surveiller en "
    "priorité. Hors 2024 (meilleure année) t tombe à 1.41. ETA de reconfirmation ~9.7 ans -> "
    "monitoring forward sur survie signe/coût/mécanisme, pas sur significativité fraîche.",
    overlap={"LIQ_CASCADE_REPEAT_V1": v2["BTC_LEAD_ALT_CASCADE"]["overlap_LIQ_CASCADE_REPEAT_V1"]},
    extra={
        "arm_difference_shock_minus_noshock": v2["BTC_LEAD_ALT_CASCADE"]["T2_shock_minus_noshock"],
        "event_weighted_cluster_robust": ew,
        "direction_control_signed_split": {
            "down_shock": v2["BTC_LEAD_ALT_CASCADE"]["P2_down_shock"]["net_bps"],
            "up_shock": v2["BTC_LEAD_ALT_CASCADE"]["P2_up_shock"]["net_bps"],
            "note": "le mécanisme prédit que le choc BAISSIER porte l'effet — confirmé",
        },
    }))

# ── #11 LIQ_CASCADE_FAR_FROM_LOW ───────────────────────────────────────────
g = v2["LIQ_CASCADE_FAR_FROM_LOW"]["T1_far_causal_q75"]
c = base_fields(
    "LIQ_CASCADE_FAR_FROM_LOW", "liquidation", 15.5, g,
    f"{BASE}/LIQ_CASCADE_FAR_FROM_LOW/REPORT.md", "DOWNGRADE_LIVE_STATUS",
    "REJECTED", ["EVIDENCE_ARTEFACT"],
    "La réimplémentation REPRODUIT exactement le chiffre publié sous la convention de la "
    "découverte (+6.84 net14 plein / +20.21 OOS 2025+, vs +6.7/+19.84 du freeze_spec) : il n'y "
    "a AUCUN désaccord d'implémentation. Le rejet porte sur l'inférence : SE sous-estimée x1.9-2.6 "
    "par le comptage de jambes corrélées, t_cluster tombe à 0.90 (far seul) et 1.30 (far-near). "
    "Le SIGNE dépend de l'unité de pondération (positif par événement, négatif par épisode) -> "
    "l'affirmation 'far bat near' n'est établie dans AUCUNE direction. far-baseline est négatif "
    "à tous les gaps d'épisode >= 1h. 3/5 années positives, pas 5/6. Ce qui reste vrai : le fade "
    "INCONDITIONNEL de cascade LONG est solide (+20.15 net14 au niveau épisode).",
    overlap={"LIQ_CASCADE_REPEAT_V1": v2["LIQ_CASCADE_FAR_FROM_LOW"]["overlap_LIQ_CASCADE_REPEAT_V1"]},
    extra={
        "live_alpha_affected": "LIQ_CASCADE_FAR_FROM_LOW_V1 (SIGNAL_SHADOW, RECONSTRUCTED)",
        "reproduction_of_claim_event_level": ffl_cmp,
        "episode_gap_sensitivity": ffl_gap,
        "event_weighted_cluster_robust": ewc["FAR_FROM_LOW"],
        "sign_status": "NOT_ESTABLISHED_EITHER_DIRECTION",
    })
c["sign_correction_required"] = None      # non tranché — voir sign_status
CANDIDATES.append(c)

# ── #8 XSEC_RESIDUAL_MOMENTUM_14D ──────────────────────────────────────────
g = v3["XSEC_RESIDUAL_MOMENTUM_14D"]["PRIMARY_resid14_LONG_excess"]
vd, tg = verdict_xsec(g)
CANDIDATES.append(base_fields(
    "XSEC_RESIDUAL_MOMENTUM_14D", "cross_sectional", 64.8, g,
    f"{BASE}/XSEC_RESIDUAL_MOMENTUM_14D/REPORT.md", "REJECT", vd, tg,
    "Excess sur l'univers éligible +31.50 net14 mais t_L3=0.63 et bootstrap p05=-52.10 : "
    "non significatif. Surtout, CE N'EST PAS UN FACTEUR DISTINCT : corrélation de rang avec "
    "le momentum 14j brut = 0.951, corrélation des rendements de portefeuille = 0.928, "
    "recouvrement Jaccard des jambes longues = 0.82. Le test apparié (resid - brut) sur les "
    "mêmes dates donne -21.51 bps (t=-1.00) : le strip de beta n'AMÉLIORE PAS le momentum brut, "
    "il le dégrade légèrement. Le +64.8 réclamé était mesuré contre zéro, pas contre l'univers.",
    extra={"same_factor_checks": v3["same_factor_checks"],
           "note_perturbation": "P7 (plancher $2M) donne t=2.02 mais une perturbation ne "
                                "sauve jamais la PRIMARY (prereg §7)."}))

# ── XSEC_MOMENTUM_HORIZON_EXTENSION (compagnon de #8) ──────────────────────
g = v3["XSEC_MOMENTUM_HORIZON_EXTENSION"]["PRIMARY_14D_LO_excess"]
vd, tg = verdict_xsec(g)
CANDIDATES.append(base_fields(
    "XSEC_MOMENTUM_HORIZON_EXTENSION", "cross_sectional", 199.3, g,
    f"{BASE}/XSEC_MOMENTUM_HORIZON_EXTENSION/REPORT.md", "REJECT", vd, tg,
    "Excess +51.78 net14, t_L3=0.85, p05=-48.76 -> échec du critère 1. Le raw vs zéro "
    "(+254.53) dépasse même la réclamation (+199.3), ce qui confirme que le chiffre publié "
    "mesurait surtout la dérive inconditionnelle du panier alt, pas le mécanisme. "
    "P1 (30D_LO) est NÉGATIF (-66.73) alors que la découverte réclamait +462.8 : la variante "
    "30 jours ne se reproduit pas du tout. Direction stable (14/14 ancrages positifs, pooled "
    "+60.07) mais magnitude non significative.",
    extra={"anchors": v3["XSEC_MOMENTUM_HORIZON_EXTENSION"]["P6_anchors"],
           "capacity": v3["capacity"]}))

# ── #6 SECTOR_ROTATION ─────────────────────────────────────────────────────
g = v5["SECTOR_ROTATION"]["PRIMARY_excess"]
vd, tg = verdict_xsec(g)
CANDIDATES.append(base_fields(
    "SECTOR_ROTATION", "relative_value", 103.0, g,
    f"{BASE}/SECTOR_ROTATION/REPORT.md", "REJECT", vd, tg,
    "Excess +13.29 net14, t_L3=0.81, net28=-0.71 (négatif) -> échoue sur significativité ET "
    "sur le coût de stress. Le raw vs zéro (+89.80) approche la réclamation (+103.0) : encore "
    "une fois le chiffre publié capture la dérive du panier, pas la rotation. Effondrement sur "
    "les perturbations structurelles : P2 (>=5 membres/secteur) -> -1.63 ; P3 (sans le panier "
    "OTHER) -> +3.94 ; P4 (hors 2021) -> +2.69, c'est-à-dire que l'essentiel de l'effet est "
    "concentré sur 2021. La carte grossière (P1) garde le signe (+15.73), donc le résultat "
    "n'est pas un artefact de MA carte — il est simplement trop faible.",
    extra={"anchors": v5["SECTOR_ROTATION"]["P6_anchors"],
           "same_factor_checks": v5["same_factor_checks"],
           "sector_map": "_lib/sector_map_v5.py (construite par le validateur)"}))

# ── SECTOR_RELATIVE_STRENGTH_REVERSAL (compagnon de #6) ────────────────────
g = v5["SECTOR_RELATIVE_STRENGTH_REVERSAL"]["PRIMARY_excess"]
vd, tg = verdict_xsec(g)
c = base_fields(
    "SECTOR_RELATIVE_STRENGTH_REVERSAL", "relative_value", 46.4, g,
    f"{BASE}/SECTOR_ROTATION/REPORT.md", "REJECT", vd, tg,
    "Excess -21.72 net14 (t=-1.33) : NÉGATIF là où la découverte réclamait +46.4. "
    "Le signal de reversal intra-secteur a une corrélation de rang de 0.86 avec le momentum 7j "
    "— ce n'est pas un facteur sectoriel, c'est du momentum inversé, et le parier à l'envers "
    "perd. P1 (carte grossière) aggrave (-35.88, t=-2.03), donc le signe négatif n'est pas un "
    "artefact de carte.",
    extra={"same_factor_checks": v5["same_factor_checks"]})
c["sign_correction_required"] = True
CANDIDATES.append(c)

# ── #12 OI_COLLAPSE_BOUNCE ─────────────────────────────────────────────────
b = ef1["OI_COLLAPSE_BOUNCE"]["primary"]
g = b["episode_level_A"]
CANDIDATES.append(base_fields(
    "OI_COLLAPSE_BOUNCE", "liquidation", 247.0, g,
    f"{BASE}/OI_COLLAPSE_BOUNCE/RESULTS.json", "MORE_RESEARCH",
    "NEEDS_MORE_RESEARCH", ["UNCONFIRMABLE_IN_HORIZON", "CONDITIONING_ADDS_NOTHING"],
    "Le bras seul est positif et significatif dans les deux conventions (épisode +18.31/t=2.74 ; "
    "événement +27.61/t_cluster=2.47). MAIS le test obligatoire bras A - bras B au niveau "
    "ÉPISODE donne -0.39 bps (Welch -0.05) : conditionner sur l'effondrement d'OI n'apporte "
    "RIEN par rapport au fade inconditionnel de cascade, qui porte déjà cet edge. Au niveau "
    "événement le contraste est significatif (+32.99, t=2.88) uniquement parce que la référence "
    "événement est négative. Le +247 bps réclamé ne se reproduit à aucune convention "
    "(max +50.79 sur la queue q05). ETA 14-28 ans.",
    extra={"arm_difference_episode": b["episode_level_A_minus_B"],
           "arm_difference_event_weighted": b["event_weighted_A_minus_B"],
           "event_weighted_cluster_robust": b["event_weighted_A"],
           "perturbations": {k: ef1["OI_COLLAPSE_BOUNCE"][k]["episode_level_A"]["net_bps"]
                             for k in ("P1_q05", "P2_oi_pctile_30d")}}))

# ── #13 CVD_SHOCK_DOWN_MEMORY ──────────────────────────────────────────────
b = ef1["CVD_SHOCK_DOWN_MEMORY"]["primary"]
g = b["episode_level_A"]
c = base_fields(
    "CVD_SHOCK_DOWN_MEMORY", "liquidation", 15.5, g,
    f"{BASE}/CVD_SHOCK_DOWN_MEMORY/RESULTS.json", "REJECT", "REJECTED", [],
    "Nul à négatif dans toutes les lectures : bras seul épisode -0.02 (t=-0.003), événement "
    "-5.31 (t=-0.72). Le contraste A-B est NÉGATIF et significatif (-19.4 épisode, Welch -2.66). "
    "La variante taker_delta_1h est significativement négative (-11.88, t_cluster=-2.19). "
    "Le 'gros N' invoqué par la découverte est un N de jambes corrélées : 2305 événements pour "
    "1133 épisodes seulement.",
    extra={"arm_difference_episode": b["episode_level_A_minus_B"],
           "variant_taker_delta_1h": ef1["CVD_SHOCK_DOWN_MEMORY"]["P1_delta_1h"]["episode_level_A"]})
c["sign_correction_required"] = True
CANDIDATES.append(c)

# ── #18 PREMIUM_EXTREME_THEN_CASCADE ───────────────────────────────────────
b = ef2["PREMIUM_EXTREME_THEN_CASCADE"]["PREM_CAPITULATION"]["extreme_tail"]
g = b["episode_level_A"]
CANDIDATES.append(base_fields(
    "PREMIUM_EXTREME_THEN_CASCADE", "liquidation", 12.1, g,
    f"{BASE}/PREMIUM_EXTREME_THEN_CASCADE/RESULTS.json", "MORE_RESEARCH",
    "NEEDS_MORE_RESEARCH", ["THIN_EPISODE_EVIDENCE"],
    "Le seul candidat de la vague avec un contraste A-B positif dans les deux conventions "
    "(épisode +21.41 ; événement +32.99... t=3.08) MAIS un t d'épisode de 1.455 sous le seuil "
    "1.645. Population inconditionnelle PREM_CAPITULATION = 0 (épisode +0.12) : c'est bien la "
    "QUEUE extrême qui porte l'effet, et le contrôle de direction est propre (queue haute nulle, "
    "-1.43). Grand écart épisode (+19.69) / événement (+101.85) = quelques gros épisodes "
    "dominent -> preuve concentrée. À reprendre avec une définition d'épisode préenregistrée "
    "et un N plus grand avant tout freeze.",
    extra={"arm_difference_episode": b["episode_level_A_minus_B"],
           "arm_difference_event_weighted": b["event_weighted_A_minus_B"],
           "unconditional_population": ef2["PREMIUM_EXTREME_THEN_CASCADE"]["PREM_CAPITULATION"]["unconditional"]["episode_level"]["net_bps"],
           "direction_control_high_tail": ef2["PREMIUM_EXTREME_THEN_CASCADE"]["PREM_CAPITULATION"]["high_tail"]["episode_level_A"]["net_bps"],
           "prem_fomo_arm": ef2["PREMIUM_EXTREME_THEN_CASCADE"]["PREM_FOMO"]["unconditional"]["episode_level"]["net_bps"]}))

# ── #19 CROWD_WASHOUT_NO_CASCADE ───────────────────────────────────────────
u = ef2["CROWD_WASHOUT_NO_CASCADE"]["unconditional"]
g = u["episode_level"]
CANDIDATES.append(base_fields(
    "CROWD_WASHOUT_NO_CASCADE", "positioning", 10.6, g,
    f"{BASE}/CROWD_WASHOUT_NO_CASCADE/RESULTS.json", "REJECT", "REJECTED",
    ["DATA_LIMITED"],
    "La réclamation ('+10.6 bps, 6/7 années stable') ne se reproduit pas : population "
    "inconditionnelle CROWD_WASHOUT à -6.35 net14 (t=-1.05) au niveau épisode, avec seulement "
    "1/5 années positives. La lecture événement est positive (+33.08) mais non significative "
    "(t_cluster=1.60) et contredit la lecture épisode. La queue extrême n'a que 215 événements "
    "/ 101 épisodes -> trop mince pour trancher (event +257.73 mais t=1.62). "
    "Dataset de 2200 événements sur 2022-2026 : c'est la contrainte dure.",
    extra={"event_weighted": u["event_weighted"],
           "extreme_tail": ef2["CROWD_WASHOUT_NO_CASCADE"]["extreme_tail"]["episode_level_A"]}))


# ── écriture ───────────────────────────────────────────────────────────────
for c in CANDIDATES:
    d = f"{BASE}/{c['candidate_id']}"
    os.makedirs(d, exist_ok=True)
    with open(f"{d}/RESULTS.json", "w") as f:
        json.dump(c, f, indent=2, default=str)

rows = []
for c in CANDIDATES:
    rows.append(
        f"| {c['candidate_id']} | {c['family']} | {c['verdict']} | "
        f"{c['discovery_net_bps']} | {c['validation_net_bps']} | {c['validation_net_bps_stress28']} | "
        f"{c['t_stat_declustered']} | {c['n_independent_L3']} | {c['eta_conservative']} | "
        f"{c['recommended_next_step']} |"
    )

md = f"""# WAVE 2 — SCOREBOARD DE VALIDATION

Généré : 2026-09-03 · worker unique (session futur-49) · harnais `_lib/validation_lib.py`

Convention : `net` = coût nominal du mécanisme (14 bps une jambe, 28 bps long-short) ;
`net28` = coût doublé (stress). `t` = t cluster-robuste sur l'unité L3 (mois calendaire pour
les cross-sectionnels, épisode cross-symbole chaîné < 4 h pour l'événementiel).

| candidat | famille | verdict | découverte net | validation net | net stress | t_L3 | N_L3 | ETA conservateur | next_step |
|---|---|---|---|---|---|---|---|---|---|
{chr(10).join(rows)}

## Lecture

**1 validé sur 9 testés.** `BTC_LEAD_ALT_CASCADE` passe les 5 critères préenregistrés dans les
deux conventions de pondération, avec un contrôle de direction (choc BTC baissier vs haussier)
qui va dans le sens du mécanisme économique.

**Le motif de rejet dominant est le même que celui de la wave 1, et il s'aggrave d'un cran.**
La wave 1 avait établi que le déclustering cross-symbole manquant surestime N de 10-20×. La
wave 2 montre que pour la famille cascade, **la pondération elle-même peut changer le signe** :
`LIQ_CASCADE_FAR_FROM_LOW` est positif par événement et négatif par épisode. La statistique
retenue désormais par défaut est donc **moyenne pondérée par événement + erreur-type
cluster-robuste** — elle conserve l'estimateur de P&L qu'un trader reconnaît tout en donnant
une significativité honnête, et rend le désaccord de convention visible au lieu de le masquer.

**Trois réclamations mesuraient un rendement contre zéro plutôt que contre leur propre univers.**
`XSEC_MOMENTUM_HORIZON_EXTENSION` (+254 raw vs +51.8 excess), `SECTOR_ROTATION` (+89.8 raw vs
+13.3 excess) et `XSEC_RESIDUAL_MOMENTUM_14D` (+225 raw vs +31.5 excess) reproduisent leur
chiffre publié en brut mais s'effondrent dès que le bras B (l'univers éligible équipondéré) est
soustrait. Le test « bras A − bras B, jamais A > 0 » du briefing est ce qui les sépare.

**Deux inversions de signe confirmées** : `SECTOR_RELATIVE_STRENGTH_REVERSAL` (−21.7 vs +46.4
réclamé) et `CVD_SHOCK_DOWN_MEMORY` (contraste A−B à −19.4, Welch −2.66).

**Un alpha live est touché.** `LIQ_CASCADE_FAR_FROM_LOW_V1` tourne en `SIGNAL_SHADOW` sur une
spec dont la preuve ne tient pas une fois la corrélation intra-cascade corrigée →
`DOWNGRADE_LIVE_STATUS` recommandé. À noter : la réimplémentation **reproduit exactement** le
chiffre du freeze_spec, il n'y a aucun bug d'implémentation — le désaccord est purement inférentiel.

## Ce qui n'a pas été testé dans cette vague

| # liste mission | candidat | raison |
|---|---|---|
| 7 | `XSEC_RELATIVE_LEVERAGE_14D` | nécessite le panel OI notionnel (`binance_vision_metrics`, 629 fichiers) — non assemblé |
| 9 | funding vs quarterly disagreement 30D | données `binance_vision_quarterly` disponibles, non branchées |
| 10 | `SHORT_COVERING_CONTINUATION` | nécessite les centiles causaux par barre sur l'univers 50 — le plus lourd |
| 14 | DVOL shock memory | `options_backfill/deribit` disponible, non branché |
| 15-17 | overlays options RV/IV, far-OTM put, block flow | déjà `ALREADY_LIVE`, hors périmètre de re-validation ici |
| 20 | LIQ vol regime gate | déjà tranché en wave 1 (`NEEDS_MORE_RESEARCH`, ETA 28-38 ans) |

Le harnais (`_lib/`) couvre déjà ces familles : il ne manque que le branchement des sources.
"""
with open(f"{BASE}/WAVE2_SCOREBOARD.md", "w") as f:
    f.write(md)

print(f"écrit {len(CANDIDATES)} RESULTS.json + WAVE2_SCOREBOARD.md")
for c in CANDIDATES:
    print(f"  {c['candidate_id']:38s} {c['verdict']:22s} net={c['validation_net_bps']} "
          f"t={c['t_stat_declustered']} L3={c['n_independent_L3']}")
