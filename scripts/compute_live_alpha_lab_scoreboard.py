#!/usr/bin/env python3
"""
scripts/compute_live_alpha_lab_scoreboard.py
─────────────────────────────────────────────────────────────────────────────
Scoreboard Live Alpha Lab — lit configs/live_alpha_registry.yaml +
reports/live_alpha_lab/*/decisions.parquet (colonne `provenance`, voir
scripts/apply_provenance_tags.py) et écrit un scoreboard qui NE CONFOND
JAMAIS "le programme tourne" (operational_status) avec "l'alpha est
confirmé" (scientific_status), et sépare explicitement replay/forward.

Exécuter apply_provenance_tags.py AVANT ce script si des runners ont tourné
depuis le dernier calcul.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.institutional.live_alpha_lab.episodes import summarize as summarize_episodes
from src.institutional.live_alpha_lab.outcomes import (
    COST_BPS_ROUNDTRIP_BASE, COST_BPS_ROUNDTRIP_STRESS, LABELABLE, NOT_LABELABLE,
    edge_retention, load_outcomes, summarize_outcomes,
)

REGISTRY = ROOT / "configs" / "live_alpha_registry.yaml"
LAB_DIR = ROOT / "reports" / "live_alpha_lab"
OUT_MD = LAB_DIR / "SCOREBOARD.md"


def load_decisions(alpha_id: str):
    p = LAB_DIR / alpha_id / "decisions.parquet"
    if not p.exists():
        return None
    return pd.read_parquet(p)


# event_time / timestamp / date -- même mapping que apply_provenance_tags.py
# (dupliqué ici volontairement : ce script doit pouvoir tourner seul sans
# dépendre de l'exécution préalable de l'autre pour connaître le nom de colonne).
_TIME_COL = {
    "LIQ_CASCADE_REPEAT_V1": "event_time", "LIQ_CASCADE_FAR_FROM_LOW_V1": "event_time",
    "BTC_LEAD_ALT_CASCADE_V1": "event_time",
    "SHORT_COVERING_CONTINUATION_V1": "timestamp", "WHALE_LSR_SCREEN_V1": "timestamp",
    "FUNDING_BASIS_DISAGREEMENT_V1": "date", "FUNDING_BASIS_DISAGREEMENT_V2": "date",
    "CROSS_SECTIONAL_MOMENTUM_LIVE_V1": "event_time",
    "CROSS_SECTIONAL_MOMENTUM_LIVE_V2": "event_time", "VOL_FORECAST_LAYER_V1": "event_time",
    # Ajoutés 2026-09-05 : absents de cette table depuis leur déploiement, donc
    # forward_age/last_trigger/actual_freq sortaient VIDES pour eux -- un angle
    # mort de monitoring sur précisément les deux alphas issus de la validation.
    "LIQ_CASCADE_REPEAT_SYSTEMIC_V1": "event_time",
    "AMIHUD_ILLIQUIDITY_PREMIUM_V1": "event_time",
}
# colonne "symbole" par alpha -- pas toujours `symbol` (SHORT_COVERING utilise
# `asset`, hérité du schéma Opportunity). Jamais deviné, mappé explicitement.
_SYMBOL_COL = {
    "LIQ_CASCADE_REPEAT_V1": "symbol", "LIQ_CASCADE_FAR_FROM_LOW_V1": "symbol",
    "BTC_LEAD_ALT_CASCADE_V1": "symbol",
    "SHORT_COVERING_CONTINUATION_V1": "asset", "WHALE_LSR_SCREEN_V1": "symbol",
    "FUNDING_BASIS_DISAGREEMENT_V1": "symbol", "FUNDING_BASIS_DISAGREEMENT_V2": "symbol",
    "CROSS_SECTIONAL_MOMENTUM_LIVE_V1": "symbol",
    "CROSS_SECTIONAL_MOMENTUM_LIVE_V2": "symbol", "VOL_FORECAST_LAYER_V1": None,  # univers=BTC seul, pas de decluster multi-symbole
    "LIQ_CASCADE_REPEAT_SYSTEMIC_V1": "symbol", "AMIHUD_ILLIQUIDITY_PREMIUM_V1": "symbol",
}

# item 15 : niveaux de confiance basés sur le NOMBRE d'épisodes indépendants,
# jamais sur le PnL ("+300bps sur 2 événements" reste TOO_EARLY).
_CONFIDENCE_THRESHOLDS = [
    (0, "TOO_EARLY"), (5, "EARLY"), (20, "DEVELOPING"), (50, "MEANINGFUL"), (100, "STRONG"),
]


# ═══════════════════════════════════════════════════════════════════════════
# LATENCE DE DÉCISION (ajouté 2026-09-05)
# ═══════════════════════════════════════════════════════════════════════════
# Un alpha peut produire des décisions forward impeccables et rester totalement
# INEXÉCUTABLE si le lab découvre l'événement après l'expiration de son propre
# horizon de détention. Mesuré ce jour : la famille cascade de liquidation
# apprend ses événements 45 à 48h après coup pour un horizon de 4h -- 100% des
# décisions arrivent périmées. `forward_decisions` seul ne le montre PAS : le
# compteur monte, la confiance monte, et rien n'est traçable comme trade.
#
# D'où ces deux colonnes, calculées sur `decided_at - <colonne temps événement>` :
#   decision_lag_median_h : à quelle distance de l'événement le lab décide
#   expired_on_arrival    : combien de décisions arrivent déjà au-delà de leur
#                           horizon (donc capital impossible à engager)
# C'est une mesure d'EXÉCUTABILITÉ, pas de validité du signal -- les deux sont
# volontairement séparées, comme scientific_status l'est d'operational_status.
_HORIZON_HOURS = {
    "fwd_4h": 4.0, "fwd_24h": 24.0, "24h": 24.0, "fwd_7d": 168.0, "k30d": 720.0,
}


# Fenêtre de la mesure « récente ». Le ledger cumule tout depuis le freeze, y
# compris les périodes où le lab tournait à la main et rattrapait plusieurs
# jours d'événements d'un coup. Ces décisions-là sont nées périmées et le
# resteront à jamais dans le cumul : mesuré le 2026-09-05, SHORT_COVERING
# affichait 160/360 périmées sur tout son historique alors que ses exécutions
# du jour tournaient à ~10 min de latence. Un indicateur d'exploitation qui ne
# redescend jamais après un incident ne sert plus à rien -- d'où les deux
# mesures, cumul ET récent, la seconde étant celle qui dit si ça va MAINTENANT.
RECENT_WINDOW_HOURS = 24


def decision_latency(df, time_col: str, horizon: str, recent_only: bool = False) -> tuple:
    """(lag médian en heures, "n_périmées/total") sur les décisions FORWARD_LIVE.

    `recent_only` : ne garder que les décisions PRISES dans les dernières
    RECENT_WINDOW_HOURS heures (filtre sur `decided_at`, pas sur l'événement --
    on veut juger le comportement du système, pas la date des marchés).

    Renvoie (None, None) si la mesure n'est pas possible -- jamais une valeur
    par défaut optimiste : une latence inconnue ne doit pas se lire comme nulle.
    """
    if df is None or "provenance" not in df.columns or "decided_at" not in df.columns:
        return None, None
    if time_col is None or time_col not in df.columns:
        return None, None
    fwd = df[df["provenance"] == "FORWARD_LIVE"]
    if recent_only and not fwd.empty:
        decided = pd.to_datetime(fwd["decided_at"], utc=True, errors="coerce")
        cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(hours=RECENT_WINDOW_HOURS)
        fwd = fwd[decided >= cutoff]
    if fwd.empty:
        return None, None
    lag_h = (pd.to_datetime(fwd["decided_at"], utc=True, errors="coerce")
             - pd.to_datetime(fwd[time_col], utc=True, errors="coerce")
             ).dt.total_seconds() / 3600.0
    lag_h = lag_h.dropna()
    if lag_h.empty:
        return None, None
    horizon_h = _HORIZON_HOURS.get(horizon)
    expired = f"{int((lag_h > horizon_h).sum())}/{len(lag_h)}" if horizon_h else "horizon_inconnu"
    return round(float(lag_h.median()), 1), expired


def confidence_level(n_independent_episodes: int) -> str:
    level = "TOO_EARLY"
    for threshold, name in _CONFIDENCE_THRESHOLDS:
        if n_independent_episodes >= threshold:
            level = name
    return level


def row_for(entry: dict) -> dict:
    alpha_id = entry["alpha_id"]
    df = load_decisions(alpha_id)
    replay = forward = independent_episodes = 0
    forward_age_hours = time_since_last_trigger_hours = actual_freq_per_day = None
    if df is not None and "provenance" in df.columns:
        vc = df["provenance"].value_counts()
        replay = int(vc.get("REPLAY", 0))
        forward = int(vc.get("FORWARD_LIVE", 0))
        symbol_col = _SYMBOL_COL.get(alpha_id)
        time_col_for_episodes = _TIME_COL.get(alpha_id)
        if forward and symbol_col and time_col_for_episodes:
            fwd_only = df[df["provenance"] == "FORWARD_LIVE"]
            independent_episodes = summarize_episodes(
                fwd_only, time_col_for_episodes, symbol_col).independent_episodes
        elif forward and not symbol_col:
            independent_episodes = forward   # univers mono-symbole (VOL_FORECAST_LAYER) -- pas de decluster cross-symbole applicable, mais garde le decluster temporel implicite via la cadence quotidienne du forecast lui-meme
        freeze = entry.get("freeze_timestamp")
        time_col = _TIME_COL.get(alpha_id)
        if freeze and time_col and time_col in df.columns:
            now = pd.Timestamp.now(tz="UTC")
            freeze_ts = pd.Timestamp(freeze)
            forward_age_hours = round((now - freeze_ts).total_seconds() / 3600, 1)
            fwd_df = df[df["provenance"] == "FORWARD_LIVE"]
            if not fwd_df.empty:
                last_trigger = pd.to_datetime(fwd_df[time_col], utc=True).max()
                time_since_last_trigger_hours = round((now - last_trigger).total_seconds() / 3600, 1)
                if forward_age_hours and forward_age_hours > 0:
                    actual_freq_per_day = round(forward / (forward_age_hours / 24), 3)
    elif df is not None:
        replay = len(df)   # pas encore tagué -- traité comme tout-replay par prudence (fail closed)
    lag_median_h, expired_on_arrival = decision_latency(
        df, _TIME_COL.get(alpha_id), entry.get("horizon"))
    lag_recent_h, expired_recent = decision_latency(
        df, _TIME_COL.get(alpha_id), entry.get("horizon"), recent_only=True)
    return {
        "alpha_id": alpha_id,
        "family": entry.get("family"),
        "scientific_status": entry.get("scientific_status", "?"),
        "operational_status": entry.get("operational_status", "?"),
        "freeze_timestamp": entry.get("freeze_timestamp"),
        "replay_decisions": replay,
        "forward_decisions": forward,
        "forward_age_hours": forward_age_hours,
        "time_since_last_trigger_hours": time_since_last_trigger_hours,
        "actual_freq_per_day": actual_freq_per_day,
        "expected_capacity": entry.get("expected_capacity"),
        # Mode A pur partout à ce stade -> pas de fills simulés -> pas de "trades" réels.
        "forward_trades": 0,
        "forward_independent_episodes": independent_episodes,
        "confidence_level": confidence_level(independent_episodes),
        "decision_lag_median_h": lag_median_h,
        "expired_on_arrival": expired_on_arrival,
        "decision_lag_median_h_recent": lag_recent_h,
        "expired_on_arrival_recent": expired_recent,
        # PF / net_bps / edge_retention : désormais CALCULÉS, à partir du ledger
        # de labels scellés (outcomes.parquet, cf. scripts/label_forward_outcomes.py).
        # Détail dans la section « RÉSULTATS FORWARD » plus bas — pas dans cette
        # ligne, parce qu'un edge n'a de sens qu'accompagné de son n, de son
        # ancrage et de son hypothèse de coût.
        "pf_net_bps_maxdd_edge_retention": outcome_verdict(alpha_id),
        "risk_bucket": entry.get("risk_bucket"),
        "correlation_family": entry.get("correlation_family"),
    }


# ═══════════════════════════════════════════════════════════════════════════
# RÉSULTATS FORWARD (ajouté 2026-09-06)
# ═══════════════════════════════════════════════════════════════════════════
# Ce que cette section répond, et que rien ne répondait avant : combien ont
# rapporté les décisions forward déjà prises. Elle lit `outcomes.parquet`, le
# ledger de labels SCELLÉS écrit par scripts/label_forward_outcomes.py.
#
# Trois précautions structurelles, dans l'ordre où elles changent la lecture :
#
# 1. EXCESS, PAS BRUT. Les cinq alphas labellisables sont tous long-only, et
#    l'univers frozen-50 a pris +10,9 % sur la fenêtre forward. Un rendement
#    brut ne mesure donc pas un edge, il mesure du bêta. La colonne qui compte
#    est `net_excess` — le rendement moins celui de l'univers sur exactement la
#    même fenêtre. Le brut reste affiché à côté, précisément pour que l'écart
#    entre les deux soit visible plutôt que dissimulé.
#
# 2. DEUX ANCRAGES. `dec` part de `decided_at` (ce que le lab pouvait
#    RÉELLEMENT capturer, latence comprise) ; `evt` part de `event_time` (ce
#    que le backtest de validation a mesuré). Seul `evt` est comparable à
#    `expected_net_bps`, donc seul `evt` alimente edge_retention. L'écart entre
#    les deux est le coût de la latence, mesuré et non supposé.
#
# 3. DEUX COÛTS. Base 14 bps (le coût exact du simulateur) et stress 28 bps.
#    Un résultat qui ne survit pas à sa borne haute est une hypothèse, pas un
#    résultat -- et le slippage est modélisé par une CONSTANTE alors que ces
#    alphas tradent précisément pendant les cascades, c'est-à-dire au moment où
#    les spreads s'écartent le plus.
#
# Seuil d'échantillon DÉCLARÉ D'AVANCE (item D1) : sous MIN_EPISODES_FOR_POINT,
# l'estimation ponctuelle n'est pas imprimée du tout -- seulement l'intervalle
# et la mention INSUFFICIENT_SAMPLE. Un chiffre absent est plus honnête qu'un
# chiffre présent accompagné d'un avertissement que personne ne lit. Aucune
# métrique ANNUALISÉE n'est produite ici, à aucun n : cinq jours et un seul
# régime ne disent rien d'un Sharpe.
MIN_EPISODES_FOR_POINT = 20

_outcome_cache = {}


def outcome_row(alpha_id: str, entry: dict) -> dict:
    """Ligne de résultats forward pour un alpha, ou son motif d'exclusion."""
    if alpha_id in _outcome_cache:
        return _outcome_cache[alpha_id]
    if alpha_id in NOT_LABELABLE:
        out = {"labelable": False, "reason": NOT_LABELABLE[alpha_id]}
        _outcome_cache[alpha_id] = out
        return out
    if alpha_id not in LABELABLE:
        # Un alpha sans ledger du tout n'est pas une DÉRIVE de classification :
        # il n'a simplement pas de code qui tourne (CODE_MISSING, DATA_BLOCKED,
        # MERGED_INTO_*). Confondre les deux ferait crier au loup sur la moitié
        # du registre et noierait le seul cas qui compte vraiment : un alpha qui
        # PRODUIT des décisions forward sans qu'on ait décidé quoi en faire.
        p = LAB_DIR / alpha_id / "decisions.parquet"
        if not p.exists():
            out = {"labelable": False,
                   "reason": f"pas de ledger de décisions — operational_status="
                             f"{entry.get('operational_status', '?')}"}
        else:
            out = {"labelable": False,
                   "reason": "⚠ UNCLASSIFIED — porte des décisions forward mais n'est ni "
                             "dans LABELABLE ni dans NOT_LABELABLE (outcomes.py). "
                             "Dérive à corriger."}
        _outcome_cache[alpha_id] = out
        return out

    led = load_outcomes(alpha_id)
    cs = LABELABLE[alpha_id].cross_sectional
    out = {"labelable": True, "cross_sectional": cs}
    if led is None or led.empty:
        out["reason"] = "aucune décision forward encore arrivée à échéance"
        _outcome_cache[alpha_id] = out
        return out
    for anchor in ("dec", "evt"):
        for metric in ("gross", "excess"):
            out[f"{anchor}_{metric}"] = summarize_outcomes(
                led, anchor=anchor, metric=metric, cross_sectional=cs)
    s = out.get("evt_excess")
    out["edge_retention"] = (
        edge_retention(s.net_bps_base, entry.get("expected_net_bps"))
        if s is not None and s.n_episodes >= MIN_EPISODES_FOR_POINT else None)
    out["n_sealed"] = int((led["label_timeliness"] == "SEALED_AT_MATURITY").sum())
    out["n_late"] = int((led["label_timeliness"] == "LATE_BACKFILL").sum())
    out["n_refused"] = int((led["dec_status"] != "OK").sum())
    _outcome_cache[alpha_id] = out
    return out


def outcome_verdict(alpha_id: str) -> str:
    """Résumé d'une case de tableau -- volontairement court et sans chiffre
    isolé : un edge sans son n ni son ancrage se fait citer de travers."""
    o = _outcome_cache.get(alpha_id)
    if o is None or not o.get("labelable"):
        return "NOT_LABELABLE (voir section RÉSULTATS FORWARD)"
    s = o.get("dec_excess")
    if s is None:
        return "PAS_ENCORE_D_ÉCHÉANCE"
    if s.n_episodes < MIN_EPISODES_FOR_POINT:
        return f"INSUFFICIENT_SAMPLE (n_ep={s.n_episodes})"
    return f"voir RÉSULTATS FORWARD (n_ep={s.n_episodes})"


def _fmt(stats, field: str) -> str:
    """Aucune suppression conditionnelle ICI : sous le seuil, la ligne entière
    est remplacée par INSUFFICIENT_SAMPLE en amont. Imprimer certains chiffres
    et pas d'autres sur la même ligne inviterait à lire ceux qui restent."""
    if stats is None:
        return "—"
    v = getattr(stats, field)
    if v is None:
        return "—"
    return f"{v:+.1f}" if "bps" in field else f"{v}"


def outcomes_section(rows: list, registry: dict) -> list:
    lines = [
        "",
        "---",
        "",
        "## RÉSULTATS FORWARD — ce que les décisions ont réellement rapporté",
        "",
        "Source : `reports/live_alpha_lab/<ALPHA>/outcomes.parquet`, ledger de labels",
        "**scellés** (append-only, jamais réécrits) écrit par `scripts/label_forward_outcomes.py`",
        "à chaque cycle, à l'échéance de l'horizon de chaque décision.",
        "",
        "⚠ **`net_excess` est le seul chiffre qui mesure un edge.** Les cinq alphas",
        "labellisables sont long-only et l'univers frozen-50 a pris **+10,9 %** sur la fenêtre",
        "forward : le rendement BRUT de n'importe quelle position longue y est positif, signal",
        "ou pas. `net_excess` retranche le rendement de l'univers sur exactement la même",
        "fenêtre. L'écart entre `net_gross` et `net_excess` EST le bêta.",
        "",
        "⚠ **Ancrages.** `dec` = à partir de `decided_at`, ce que le lab pouvait réellement",
        "capturer. `evt` = à partir de `event_time`, ce que le backtest de validation a mesuré.",
        "Seul `evt` est comparable à `expected_net_bps`, donc seul `evt` alimente",
        "`edge_retention`. L'écart entre les deux est le coût de la latence.",
        "",
        f"⚠ **Coûts.** `net@{COST_BPS_ROUNDTRIP_BASE:.0f}` = coût exact du simulateur "
        f"(aller-retour) ; `net@{COST_BPS_ROUNDTRIP_STRESS:.0f}` = borne haute. Le slippage",
        "est une CONSTANTE de 2 bps alors que ces alphas tradent pendant les cascades,",
        "c'est-à-dire au moment où les spreads s'écartent le plus. Un résultat qui ne survit",
        "pas à la borne haute est une hypothèse, pas un résultat.",
        "",
        f"⚠ **Seuil d'échantillon déclaré : {MIN_EPISODES_FOR_POINT} épisodes indépendants.**",
        "En dessous, AUCUN chiffre n'est imprimé — ni moyenne, ni intervalle, ni hit rate.",
        "Un IC calculé sur un seul épisode a l'air précis parce qu'il n'a pas de largeur.",
        "Aucune métrique annualisée n'est produite ici, à aucun `n`.",
        "",
        "| alpha_id | n_lab | n_épisodes | scellés/tardifs | anc. | net_gross@14 | net_excess@14 | net_excess@28 | PF | hit | IC95 excess@14 | edge_retention |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    excluded = []
    for r in rows:
        alpha_id = r["alpha_id"]
        o = outcome_row(alpha_id, registry.get(alpha_id, {}))
        if not o.get("labelable"):
            excluded.append((alpha_id, o["reason"]))
            continue
        if "dec_excess" not in o:
            excluded.append((alpha_id, o.get("reason", "pas de label")))
            continue
        n_ep = o["dec_excess"].n_episodes if o["dec_excess"] else 0
        if n_ep < MIN_EPISODES_FOR_POINT:
            # Sous le seuil : AUCUN chiffre. Pas de moyenne, pas d'IC, pas de
            # hit rate. Un IC calculé sur 1 épisode ([-22,5 ; -22,5]) a l'air
            # d'une mesure précise alors qu'il n'a aucune largeur faute de
            # variance observable -- c'est la façon la plus efficace de faire
            # lire une certitude là où il n'y a qu'une observation.
            lines.append(
                f"| {alpha_id} | {o['dec_excess'].n_labeled if o['dec_excess'] else 0} | "
                f"{n_ep} | {o['n_sealed']}/{o['n_late']} | — | "
                f"INSUFFICIENT_SAMPLE (n_ep={n_ep} < {MIN_EPISODES_FOR_POINT}) | | | | | | |")
            continue
        for anchor in ("dec", "evt"):
            ex, gr = o[f"{anchor}_excess"], o[f"{anchor}_gross"]
            ci = (f"[{ex.ci95_low_bps:+.1f}, {ex.ci95_high_bps:+.1f}]"
                  if ex and ex.ci95_low_bps is not None else "—")
            ret = (f"{o['edge_retention']}" if (anchor == "evt" and o.get("edge_retention")
                                                is not None) else "—")
            lines.append(
                f"| {alpha_id if anchor == 'dec' else ''} | {ex.n_labeled} | "
                f"{ex.n_episodes} | {o['n_sealed']}/{o['n_late']} | `{anchor}` | "
                f"{_fmt(gr, 'net_bps_base')} | {_fmt(ex, 'net_bps_base')} | "
                f"{_fmt(ex, 'net_bps_stress')} | {_fmt(ex, 'profit_factor_base')} | "
                f"{_fmt(ex, 'hit_rate')} | {ci} | {ret} |")
    if excluded:
        lines += ["", "### Hors périmètre du label, avec motif", ""]
        for alpha_id, reason in excluded:
            lines.append(f"- **{alpha_id}** — {reason}")
    lines += [
        "",
        "### Ce que ce tableau ne dit pas",
        "",
        "- **Il ne dit rien d'un Sharpe.** Cinq jours, un seul régime, un marché qui monte de",
        "  près de 11 % : la question « quel edge par décision » (n = épisodes, mesurable) et",
        "  la question « quel Sharpe » (n = 5 jours, non mesurable) n'ont pas la même taille",
        "  d'échantillon, et la seconde ne se déduit pas de la première.",
        "- **`net_excess` n'est pas un placebo.** Une référence de marché mesure le bêta, pas",
        "  le biais de l'infrastructure de simulation. Un alpha à signal aléatoire tournant",
        "  dans les mêmes portefeuilles, avec le même sizing et les mêmes coûts, reste à faire.",
        "- **Les labels `LATE_BACKFILL` ne sont pas des labels scellés à l'échéance.** Le prix",
        "  relevé est honnête (les partitions de `derivatives_raw` ne sont pas réécrites), mais",
        "  rien ne garantit que la règle de labellisation ait été fixée avant d'avoir vu la",
        "  donnée. Seule la colonne `scellés` porte cette garantie, et elle ne peut que croître",
        "  à partir du 2026-09-06.",
        "- **`edge_retention` contre une référence RECONSTRUCTED ne confirme rien.** Le registre",
        "  le dit déjà pour SHORT_COVERING et WHALE_LSR : leur `expected_net_bps` vient d'un",
        "  seuil ajusté sur ces mêmes données, c'est un contexte historique, pas une cible.",
    ]
    return lines


def main() -> int:
    reg = yaml.safe_load(REGISTRY.read_text())
    by_id = {a["alpha_id"]: a for a in reg["alphas"]}
    # Les résultats forward sont calculés AVANT les lignes du tableau principal :
    # row_for() lit le verdict depuis le cache que ceci remplit.
    for a in reg["alphas"]:
        outcome_row(a["alpha_id"], a)
    rows = [row_for(a) for a in reg["alphas"]]

    lines = [
        "# Live Alpha Lab — scoreboard",
        "",
        f"Généré : {datetime.now(timezone.utc).isoformat()}",
        "",
        "⚠ `operational_status=SIGNAL_SHADOW` signifie UNIQUEMENT que le signal tourne réellement.",
        "Ça ne dit RIEN sur la validité de l'alpha — voir `scientific_status`. Seule la colonne",
        "`forward_decisions` (event_time > freeze_timestamp) compte comme preuve jamais-vue ;",
        "`replay_decisions` est du backfill historique, pas une preuve forward.",
        "",
        "⚠ `lag_med_h` / `périmées` mesurent l'EXÉCUTABILITÉ, pas la validité : un alpha dont le",
        "lab découvre les événements après l'expiration de son propre horizon accumule des décisions",
        "forward correctes mais ne pourra JAMAIS engager de capital.",
        "",
        "**Lire la colonne (24h), pas le cumul, pour juger l'état COURANT.** Le cumul inclut les",
        "périodes où le lab tournait à la main et rattrapait plusieurs jours d'événements d'un coup :",
        "ces décisions sont nées périmées et le restent à jamais dans le total. Exemple mesuré le",
        "2026-09-05 : SHORT_COVERING_CONTINUATION_V1 affichait 160/360 périmées en cumul alors que",
        "ses exécutions du jour tournaient à ~10 minutes de latence.",
        "",
        "| alpha_id | family | scientific_status | operational_status | freeze_timestamp | replay | forward | independent_episodes | confidence | forward_age_h | last_trigger_h_ago | actual_freq/day | lag_med_h (cumul) | périmées (cumul) | lag_med_h (24h) | périmées (24h) | risk_bucket |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in sorted(rows, key=lambda r: (r["scientific_status"], r["alpha_id"])):
        lines.append(
            f"| {r['alpha_id']} | {r['family']} | {r['scientific_status']} | "
            f"{r['operational_status']} | {r['freeze_timestamp']} | "
            f"{r['replay_decisions']} | **{r['forward_decisions']}** | "
            f"{r['forward_independent_episodes']} | {r['confidence_level']} | "
            f"{r['forward_age_hours']} | {r['time_since_last_trigger_hours']} | "
            f"{r['actual_freq_per_day']} | {r['decision_lag_median_h']} | "
            f"{r['expired_on_arrival']} | {r['decision_lag_median_h_recent']} | "
            f"{r['expired_on_arrival_recent']} | {r['risk_bucket']} |"
        )

    total_forward = sum(r["forward_decisions"] for r in rows)
    lines += [
        "",
        f"**Total forward_decisions toutes familles : {total_forward}**"
        + (" — attendu à ce stade, le correctif de discipline vient d'être appliqué "
           "(tous les freeze_timestamp sont à J0 ou récents)." if total_forward == 0 else "."),
        "",
        "⚠ **PF / net_bps / maxDD / edge_retention ne sont PAS encore calculés** pour les alphas",
        "de position (nécessite un label de résultat forward par décision, comme le backfill",
        "`actual_realized_rv` de VOL_FORECAST_LAYER_V1 mais pour chaque alpha directionnel —",
        "pas encore construit, prochaine étape logique une fois plus de forward accumulé).",
        "",
        "⚠ **EXÉCUTABILITÉ — constat du 2026-09-05.** La famille cascade de liquidation",
        "(`LIQ_CASCADE_REPEAT_V1`, `LIQ_CASCADE_REPEAT_SYSTEMIC_V1`, `LIQ_CASCADE_FAR_FROM_LOW_V1`,",
        "`BTC_LEAD_ALT_CASCADE_V1`) lit `data/derivatives_backfill/binance_vision_metrics/`, un",
        "backfill d'archives quotidiennes Binance Vision structurellement en retard de 1 à 2 jours.",
        "Mesuré : **100% de ses décisions forward arrivent 45-48h après l'événement, pour un",
        "horizon de 4h** — elles sont périmées à l'arrivée et ne peuvent pas recevoir de capital.",
        "Ce n'est pas un creux de marché, c'est une impossibilité d'architecture. Ces alphas",
        "accumulent une preuve de SIGNAL valable, pas une preuve de STRATÉGIE exécutable.",
        "Détail et options de correction : `reports/live_alpha_lab/DECISION_LATENCY_AUDIT_2026-09-05.md`.",
        "",
        "**0 signal pendant quelques heures n'est PAS un problème** — les cascades de liquidation,",
        "le funding-basis (~15-18/an/actif) et le screen positioning sont des mécanismes rares par",
        "construction. `actual_freq_per_day` est là pour comparer objectivement à",
        "`expected_capacity` (texte libre du registre) le moment venu, pas pour juger après",
        "quelques heures.",
    ]

    lines += outcomes_section(sorted(rows, key=lambda r: r["alpha_id"]), by_id)

    OUT_MD.write_text("\n".join(lines) + "\n")
    print(f"Scoreboard écrit -> {OUT_MD}")
    print(f"Total forward_decisions : {total_forward}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
