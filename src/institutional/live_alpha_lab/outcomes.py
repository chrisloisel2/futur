"""
src/institutional/live_alpha_lab/outcomes.py
─────────────────────────────────────────────────────────────────────────────
LABELLISEUR DE RÉSULTAT FORWARD — le chaînon manquant du Live Alpha Lab.

Le problème qu'il résout
────────────────────────
Le scoreboard imprimait `pf_net_bps_maxdd_edge_retention =
"PENDING_outcome_labeling_not_built"` : le lab comptait 862 décisions forward
horodatées, gelées, jamais vues... et ne savait dire de AUCUNE d'elles ce
qu'elle avait rapporté. Les 22,1 et 46,87 bps affichés comme « edge » venaient
de la VALIDATION (backtest), pas du forward. Autrement dit : le laboratoire
accumulait de la preuve sans jamais la lire.

Ce module lit cette preuve : pour chaque décision forward directionnelle dont
l'horizon est écoulé, il mesure le rendement réalisé sur SON PROPRE horizon,
au prix de la même source que le mark-to-market du portefeuille (marks.py).

Pourquoi le label est SCELLÉ, et pourquoi ça change tout
───────────────────────────────────────────────────────
Un batch rétrospectif peut être relancé avec d'autres paramètres jusqu'à ce
que le chiffre plaise ; un label écrit à l'échéance et scellé ne le peut pas.
C'est exactement la différence entre une preuve forward et un backtest
déguisé. Trois mécanismes, pas un :

  1. APPEND-ONLY STRICT. `append_sealed()` refuse d'écrire une clé de décision
     déjà présente. Le scellement est une porte à sens unique (même contrat
     que alpha_foundry_v5/scripts/seal_forward_window.py). Un label existant
     n'est jamais recalculé, quelle que soit l'évolution du code.

  2. FENÊTRE DE SCELLEMENT. `label_timeliness` distingue
     SEALED_AT_MATURITY (label écrit dans les SEAL_WINDOW_HOURS suivant
     l'échéance — c'est-à-dire par le cycle lui-même, en vol) de
     LATE_BACKFILL (label écrit longtemps après). Un LATE_BACKFILL reste une
     mesure de prix honnête — les partitions de derivatives_raw sont écrites
     par le collecteur au fil de l'eau et ne sont pas réécrites — mais rien ne
     garantit que la RÈGLE de labellisation ait été fixée avant d'avoir vu la
     donnée. Les deux populations ne sont donc jamais mélangées en silence.
     Conséquence assumée : au premier passage, l'historique déjà accumulé
     ressort en LATE_BACKFILL. C'est la vérité, pas un défaut.

  3. EMPREINTE DES PARAMÈTRES. `label_params_sha256` scelle la règle
     elle-même (ancrages, source de prix, seuils de fraîcheur, modèle de
     coût). Deux labels calculés sous deux règles différentes sont
     distinguables dans le ledger, sans avoir à croire personne sur parole.

Deux ancrages, deux questions différentes
─────────────────────────────────────────
Une décision a DEUX instants candidats pour l'entrée, et les confondre est le
piège central de ce lab (cf. DECISION_LATENCY_AUDIT_2026-09-05.md) :

  - ancrage DÉCISION (`dec_*`, à partir de `decided_at`) : ce que le lab
    aurait RÉELLEMENT pu capturer, latence comprise. C'est la vérité
    exécutable, et la seule qui compte pour engager du capital.
  - ancrage ÉVÉNEMENT (`evt_*`, à partir de `event_time`) : ce que le
    backtest de validation a mesuré, puisqu'il s'ancre sur la barre de
    l'événement. C'est le seul chiffre comparable à `expected_net_bps` du
    registre, donc le seul dénominateur légitime d'`edge_retention`.

L'écart entre les deux N'EST PAS du bruit : c'est le coût de la latence, en
bps, mesuré au lieu d'être supposé. Un alpha dont `evt` est bon et `dec` est
nul n'a pas un problème de signal, il a un problème d'architecture.

Coûts : jamais une constante unique
───────────────────────────────────
Le prix relevé est un fait scellé ; le modèle de coût est une hypothèse. Les
deux ne sont donc pas scellés ensemble : le ledger stocke le BRUT (`*_gross_bps`)
et les consommateurs dérivent le net sous DEUX hypothèses déclarées, la base
du simulateur et sa borne haute (voir COST_BPS_ROUNDTRIP_*). Un résultat qui
survit à sa borne pessimiste est un résultat ; un résultat qui n'existe qu'à
l'hypothèse optimiste est une hypothèse. Même logique que le
`breakeven_capture` d'alpha_foundry_v5 : rendre la conclusion indépendante de
la constante plutôt que de la choisir.

Ce module ne labellise PAS tout, et le dit
──────────────────────────────────────────
`NOT_LABELABLE` liste explicitement les alphas hors périmètre AVEC leur
motif : un screen sans direction n'a pas de rendement directionnel, et un
forecast de volatilité a déjà son propre mécanisme de label
(`actual_realized_rv`). Compter leurs décisions dans un total « forward »
suggérerait un volume de preuve tradable qui n'existe pas.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.dataset as pads

from src.institutional.live_alpha_lab.marks import DERIVATIVES_RAW, get_mark
from src.institutional.live_alpha_lab.provenance import git_head_sha

ROOT = Path(__file__).resolve().parents[3]
LAB_DIR = ROOT / "reports" / "live_alpha_lab"

# Toute évolution de la sémantique d'une colonne existante impose d'incrémenter
# ce numéro : les anciennes lignes ne sont jamais réécrites, elles doivent donc
# rester lisibles POUR CE QU'ELLES ÉTAIENT.
LABEL_SCHEMA_VERSION = 1

# Le cycle tourne toutes les 15 min (futur-live-alpha-lab.timer,
# OnUnitActiveSec=15min). 2 h = 8 cycles de marge : assez pour absorber une
# panne courte ou un cycle long sans dégrader le label, assez serré pour que
# "scellé à l'échéance" veuille encore dire quelque chose.
SEAL_WINDOW_HOURS = 2.0

# Au-delà, on cesse d'espérer que la donnée de prix arrive et on inscrit un
# refus DÉFINITIF dans le ledger. Objectif : l'exhaustivité. Toute décision
# forward mûre d'un alpha labellisable finit avec exactement UNE ligne, soit
# labellisée soit explicitement refusée -- jamais silencieusement absente.
ABANDON_AFTER_HOURS = 72.0

# Modèle de coût : aller-retour, en bps de notionnel.
#   base   = 2 x (TAKER_FEE_BPS 5,0 + FIXED_SLIPPAGE_BPS 2,0) = 14 bps
#            -- exactement le coût du simulateur (portfolio.py::shadow_execute)
#   stress = 28 bps -- la borne haute déjà utilisée par le registre
#            (`expected_net_bps_stress28`), soit 9 bps de slippage par jambe.
# Ce n'est PAS une mesure du carnet : derivatives_raw ne porte pas de bid/ask.
# Tant que la collecte microstructure ne fournit pas de spread conditionnel au
# régime, publier les deux bornes est la seule lecture honnête.
COST_BPS_ROUNDTRIP_BASE = 14.0
COST_BPS_ROUNDTRIP_STRESS = 28.0

# Fraîcheur maximale du mark accepté pour ancrer un prix. get_mark() renvoie le
# dernier point <= as_of : sans ce garde-fou, un trou dans l'archive donnerait
# silencieusement un prix vieux de plusieurs heures présenté comme le prix de
# l'instant. Le collecteur poll ~5 min ; 15 min = même seuil que
# marks.STALE_MS_BY_SOURCE["DERIVATIVES_RAW_MARK"], pas une constante neuve.
MAX_MARK_AGE_MINUTES = 15.0

_HORIZON_HOURS = {"fwd_4h": 4.0, "fwd_24h": 24.0, "24h": 24.0, "fwd_7d": 168.0, "k30d": 720.0}

# Univers de RÉFÉRENCE du label — le frozen-50 déjà figé de
# configs/portfolio_v1_1_parallel_50.yaml, exactement celui que le collecteur
# 5 min alimente (scripts/collect_oi_metrics_5m.py::load_universe). Jamais un
# glob() : l'univers-drift est un bug déjà corrigé une fois dans ce projet
# (tests/test_universe_drift_guard.py, 2026-08-30).
BENCHMARK_UNIVERSE_CONFIG = ROOT / "configs" / "portfolio_v1_1_parallel_50.yaml"

# Nombre minimal de symboles cotés aux DEUX jambes pour qu'une référence soit
# publiée. En dessous, `bench` vaut None -- une moyenne sur 3 symboles n'est pas
# "le marché", et la publier laisserait croire à un contrôle qui n'existe pas.
MIN_BENCHMARK_SYMBOLS = 20


@dataclass(frozen=True)
class LabelSpec:
    """Comment lire UNE décision de cet alpha. Jamais deviné, toujours mappé
    explicitement -- même discipline que _TIME_COL/_SYMBOL_COL du scoreboard."""
    time_col: str          # instant de l'ÉVÉNEMENT (ancrage validation)
    symbol_col: str
    direction_col: str
    horizon: str
    # Le déclencheur est-il COMMUN à tous les symboles ?
    #
    # Mesuré au premier passage du labelliseur : les 31 décisions forward de
    # BTC_LEAD_ALT_CASCADE_V1 portent sur 31 symboles distincts mais sur
    # QUATRE event_time, tous dans la même fenêtre de 15 minutes du
    # 2026-09-04. Ce n'est pas 31 preuves : c'est UN choc BTC observé sur 31
    # alts, et le mécanisme lui-même ("BTC mène, les alts suivent") garantit
    # qu'elles sont maximalement corrélées à cet instant. Le decluster
    # same-symbol standard renvoie pourtant 31 épisodes indépendants, ce qui
    # rétrécit tout intervalle de confiance d'un facteur ~√31.
    #
    # Quand ce drapeau est vrai, l'épisode est une fenêtre de TEMPS commune à
    # tout l'univers : un choc = une preuve, quel que soit le nombre de
    # symboles touchés. C'est le même piège de decluster déjà rencontré 4× dans
    # alpha_hunt round 2, sur son autre axe (transversal au lieu de temporel).
    cross_sectional: bool = False


# Alphas directionnels : une direction + un horizon => un rendement mesurable.
LABELABLE: Dict[str, LabelSpec] = {
    "LIQ_CASCADE_REPEAT_V1": LabelSpec("event_time", "symbol", "direction", "fwd_4h"),
    "LIQ_CASCADE_REPEAT_SYSTEMIC_V1": LabelSpec("event_time", "symbol", "direction", "fwd_4h"),
    "LIQ_CASCADE_FAR_FROM_LOW_V1": LabelSpec("event_time", "symbol", "direction", "fwd_4h"),
    # cross_sectional : le déclencheur est un choc BTC unique répercuté sur N
    # alts -- voir LabelSpec.cross_sectional.
    "BTC_LEAD_ALT_CASCADE_V1": LabelSpec("event_time", "symbol", "direction", "fwd_4h",
                                         cross_sectional=True),
    "SHORT_COVERING_CONTINUATION_V1": LabelSpec("timestamp", "asset", "direction", "fwd_4h"),
}

# Hors périmètre, AVEC motif. L'absence de label ici est une propriété de
# l'alpha, pas une lacune du labelliseur -- et elle doit se lire comme telle
# dans le scoreboard plutôt que comme un "0" ambigu.
NOT_LABELABLE: Dict[str, str] = {
    "WHALE_LSR_SCREEN_V1":
        "NOT_DIRECTIONAL_SCREEN — aucune colonne `direction` : c'est un écran de "
        "positionnement consommé comme GATE par le portefeuille, pas une position. "
        "Ses 304 décisions forward ne sont pas des trades et ne peuvent pas porter "
        "un rendement directionnel.",
    "VOL_FORECAST_LAYER_V1":
        "HAS_OWN_LABEL_MECHANISM — alpha de volatilité, pas de direction de prix. "
        "Son label de résultat existe déjà et lui est propre : `actual_realized_rv` "
        "(src/institutional/engines/vol_forecast_layer/backfill.py). Le dupliquer "
        "ici produirait deux vérités concurrentes pour la même décision.",
    "FUNDING_BASIS_DISAGREEMENT_V1":
        "NO_FORWARD_DECISIONS — scientific_status=REJECTED, jamais lancé sous cette "
        "identité (jambe perp stale). Rien à labelliser.",
    "FUNDING_BASIS_DISAGREEMENT_V2":
        "SIGNAL_SHADOW_PUR — le registre déclare explicitement « AUCUN exit simulé, "
        "AUCUN fill » ; k30d n'est qu'une fenêtre de decluster, pas un horizon de "
        "détention. Labelliser reviendrait à inventer une stratégie que la spec "
        "refuse de définir.",
    "CROSS_SECTIONAL_MOMENTUM_LIVE_V1":
        "NO_FORWARD_DECISIONS — 0 décision FORWARD_LIVE (tout REPLAY).",
    "CROSS_SECTIONAL_MOMENTUM_LIVE_V2":
        "NO_FORWARD_DECISIONS — 0 décision FORWARD_LIVE (tout REPLAY).",
    "AMIHUD_ILLIQUIDITY_PREMIUM_V1":
        "NO_FORWARD_DECISIONS — 0 décision FORWARD_LIVE (tout REPLAY).",
}


def _utc(ts) -> Optional[pd.Timestamp]:
    t = pd.to_datetime(ts, utc=True, errors="coerce")
    return None if pd.isna(t) else pd.Timestamp(t)


def horizon_hours(horizon: str) -> Optional[float]:
    return _HORIZON_HOURS.get(horizon)


def label_params_digest() -> str:
    """Empreinte de la RÈGLE de labellisation. Change dès qu'un ancrage, un
    seuil de fraîcheur, une fenêtre de scellement ou le modèle de coût bouge —
    ce qui rend toute dérive de méthode visible dans le ledger lui-même,
    ligne par ligne, sans dépendre d'un changelog."""
    payload = {
        "schema_version": LABEL_SCHEMA_VERSION,
        "seal_window_hours": SEAL_WINDOW_HOURS,
        "abandon_after_hours": ABANDON_AFTER_HOURS,
        "max_mark_age_minutes": MAX_MARK_AGE_MINUTES,
        "cost_bps_roundtrip_base": COST_BPS_ROUNDTRIP_BASE,
        "cost_bps_roundtrip_stress": COST_BPS_ROUNDTRIP_STRESS,
        "horizon_hours": _HORIZON_HOURS,
        "anchors": ["decided_at", "event_time"],
        "price_source": "derivatives_raw.open_interest.mark_price (MarkSeriesCache)",
        "benchmark_universe_config": BENCHMARK_UNIVERSE_CONFIG.name,
        "benchmark_min_symbols": MIN_BENCHMARK_SYMBOLS,
        "labelable": {k: asdict(v) for k, v in sorted(LABELABLE.items())},
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:16]


def decision_key(alpha_id: str, event_time, symbol: str, direction: str, decided_at) -> str:
    """Identité stable d'une décision, indépendante de l'ordre des lignes et de
    l'index pandas (qui, lui, bouge à chaque réécriture du parquet). Inclut
    `decided_at` : le même événement re-décidé à un cycle ultérieur est une
    décision distincte, et les fusionner masquerait un doublon de producteur."""
    et = _utc(event_time)
    da = _utc(decided_at)
    raw = "|".join([
        alpha_id,
        et.isoformat() if et is not None else "NaT",
        str(symbol),
        str(direction),
        da.isoformat() if da is not None else "NaT",
    ])
    return hashlib.sha1(raw.encode()).hexdigest()[:20]


# ═══════════════════════════════════════════════════════════════════════════
# SOURCE DE PRIX
# ═══════════════════════════════════════════════════════════════════════════
# Une seule source, DERIVATIVES_RAW_MARK — la même que le mark-to-market du
# portefeuille (marks.py). Restriction DÉLIBÉRÉE, pas un raccourci :
#   - REST_BOOKTICKER_MID est structurellement incapable de répondre à une
#     question historique (marks.REST_CAUSALITY_TOLERANCE_MS) : il refuserait
#     de toute façon toutes les jambes sauf celles d'il y a moins d'une minute ;
#   - mélanger deux sources entre l'entrée et la sortie rendrait le rendement
#     incomparable à lui-même — un écart de source se lirait comme de l'edge.
# Un symbole absent de derivatives_raw n'est donc pas labellisé : NO_PRICE
# explicite, jamais un prix d'une autre nature présenté comme équivalent.

_OI_BASE = DERIVATIVES_RAW / "exchange=binance" / "market=usdm" / "stream=open_interest"

# Schéma imposé à la lecture : les partitions du collecteur ont dérivé dans le
# temps (`symbol` tantôt string tantôt dictionary), ce qui fait échouer l'union
# automatique de pyarrow sur un lot de fichiers. On ne lit que les deux colonnes
# utiles, typées explicitement -- insensible à toute dérive des autres colonnes.
_PRICE_SCHEMA = pa.schema([("timestamp", pa.int64()), ("mark_price", pa.float64())])


class MarkSeriesCache:
    """Cache (symbole, jour) de la série mark_price.

    Pourquoi il existe : `marks.get_mark()` relit ~1000 fichiers parquet à
    CHAQUE appel. Le labelliseur en fait quatre par décision (deux ancrages x
    entrée/sortie), soit ~2200 appels au premier passage -> ~15 minutes, c'est-
    à-dire plus que la période du cycle lui-même. Le cache ramène ça à une
    lecture par (symbole, jour).

    Sémantique IDENTIQUE à `marks._from_derivatives_raw` : dernier point de
    marque <= as_of, cherché dans la partition du jour et celle de la veille
    (mêmes deux partitions que `eligible_files_for_as_of`). Vérifié par
    tests/test_outcomes_labeling.py::test_cache_matches_get_mark.
    """

    def __init__(self, base: Path = _OI_BASE):
        self.base = base
        self._chunks: Dict[Tuple[str, str], Tuple[np.ndarray, np.ndarray]] = {}

    def _chunk(self, symbol: str, day: str) -> Tuple[np.ndarray, np.ndarray]:
        key = (symbol, day)
        if key in self._chunks:
            return self._chunks[key]
        d = self.base / f"symbol={symbol}" / f"date={day}"
        ts = np.empty(0, dtype="int64")
        px = np.empty(0, dtype="float64")
        files = sorted(str(f) for f in d.glob("part-*.parquet")) if d.is_dir() else []
        if files:
            try:
                tb = pads.dataset(files, format="parquet", schema=_PRICE_SCHEMA).to_table()
                ts = tb.column("timestamp").to_numpy(zero_copy_only=False).astype("int64")
                px = tb.column("mark_price").to_numpy(zero_copy_only=False).astype("float64")
                order = np.argsort(ts, kind="stable")
                ts, px = ts[order], px[order]
            except Exception:
                ts = np.empty(0, dtype="int64")
                px = np.empty(0, dtype="float64")
        self._chunks[key] = (ts, px)
        return self._chunks[key]

    def at(self, symbol: str, as_of: pd.Timestamp) -> Tuple[Optional[float], str, Optional[float]]:
        """(prix, source_ou_motif_de_refus, âge_du_mark_en_ms).

        Jamais de prix « au mieux » : un mark trop vieux est refusé
        explicitement (STALE_MARK) plutôt que servi comme s'il datait de
        l'instant demandé -- un trou dans l'archive doit se voir, pas se lire
        comme un rendement."""
        day = as_of.strftime("%Y-%m-%d")
        prev = (as_of - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        ts_parts, px_parts = [], []
        for d in (prev, day):
            t, p = self._chunk(symbol, d)
            if t.size:
                ts_parts.append(t)
                px_parts.append(p)
        if not ts_parts:
            return None, "NO_PRICE", None
        ts = np.concatenate(ts_parts)
        px = np.concatenate(px_parts)
        # `timestamp` est en epoch MILLISECONDES dans derivatives_raw (même
        # remarque que marks.py) -- jamais laisser pandas/numpy deviner.
        cutoff_ms = int(as_of.value // 1_000_000)
        idx = int(np.searchsorted(ts, cutoff_ms, side="right")) - 1
        if idx < 0:
            return None, "NO_PRICE", None
        age_ms = float(cutoff_ms - ts[idx])
        if age_ms > MAX_MARK_AGE_MINUTES * 60_000:
            return None, "STALE_MARK", age_ms
        price = float(px[idx])
        if not np.isfinite(price) or price <= 0:
            return None, "NO_PRICE", age_ms
        return price, "DERIVATIVES_RAW_MARK", age_ms


_DEFAULT_CACHE = MarkSeriesCache()

_benchmark_universe: Optional[List[str]] = None


def benchmark_universe() -> List[str]:
    global _benchmark_universe
    if _benchmark_universe is None:
        import yaml
        _benchmark_universe = sorted(
            yaml.safe_load(BENCHMARK_UNIVERSE_CONFIG.read_text())["universe"])
    return list(_benchmark_universe)


def universe_return_bps(entry_at: pd.Timestamp, exit_at: pd.Timestamp,
                        cache: Optional[MarkSeriesCache] = None
                        ) -> Tuple[Optional[float], int]:
    """Rendement equipondéré du frozen-50 sur la MÊME fenêtre, en bps.

    Pourquoi c'est dans le label et pas dans un rapport à côté
    ────────────────────────────────────────────────────────────
    Les cinq alphas labellisables sont TOUS long-only. Sur cinq jours d'un
    marché qui monte, chacun affiche des bps positifs, signal ou pas. Un
    rendement brut sans référence n'est donc pas une mesure d'edge : c'est une
    mesure de bêta déguisée. `excess_bps` est le chiffre qui répond à la
    question posée -- ce mécanisme bat-il le fait de détenir l'univers ?

    Et il DOIT être scellé en même temps que le reste : le ledger est
    append-only, une colonne absente de la première ligne ne peut plus jamais
    être ajoutée aux lignes déjà scellées. Ce qui manque au premier passage
    manque pour toujours.

    ⚠ Ce n'est PAS le placebo. Une référence de marché mesure le bêta, pas le
    biais de l'infrastructure de simulation. Un alpha à signal aléatoire
    tournant dans les mêmes portefeuilles reste nécessaire pour ça, et reste à
    faire.
    """
    cache = cache if cache is not None else _DEFAULT_CACHE
    rets = []
    for sym in benchmark_universe():
        e, _, _ = cache.at(sym, entry_at)
        x, _, _ = cache.at(sym, exit_at)
        if e and x and e > 0:
            rets.append(x / e - 1.0)
    if len(rets) < MIN_BENCHMARK_SYMBOLS:
        return None, len(rets)
    return float(np.mean(rets)) * 10_000.0, len(rets)


_bench_memo: Dict[Tuple[int, int], Tuple[Optional[float], int]] = {}


def _universe_return_memo(entry_at: pd.Timestamp, exit_at: pd.Timestamp,
                          cache: Optional[MarkSeriesCache]) -> Tuple[Optional[float], int]:
    """Les décisions d'un même cycle partagent leur `decided_at`, et beaucoup
    partagent leur `event_time` : une même fenêtre est demandée des dizaines de
    fois. 50 symboles x 2 jambes par fenêtre, ça vaut le mémo."""
    key = (int(entry_at.value), int(exit_at.value))
    if key not in _bench_memo:
        _bench_memo[key] = universe_return_bps(entry_at, exit_at, cache)
    return _bench_memo[key]


def _price_at(symbol: str, as_of: pd.Timestamp,
              cache: Optional[MarkSeriesCache] = None) -> Tuple[Optional[float], str, Optional[float]]:
    return (cache or _DEFAULT_CACHE).at(symbol, as_of)


def _signed_bps(direction: str, entry: float, exit_: float) -> Optional[float]:
    if entry is None or exit_ is None or entry <= 0:
        return None
    sign = {"LONG": 1.0, "SHORT": -1.0}.get(str(direction).upper())
    if sign is None:
        return None
    return sign * (exit_ / entry - 1.0) * 10_000.0


def _anchor_leg(symbol: str, direction: str, entry_at: pd.Timestamp,
                exit_at: pd.Timestamp, prefix: str,
                cache: Optional[MarkSeriesCache] = None) -> dict:
    e_px, e_src, e_age = _price_at(symbol, entry_at, cache)
    x_px, x_src, x_age = _price_at(symbol, exit_at, cache)
    gross = _signed_bps(direction, e_px, x_px) if (e_px and x_px) else None
    if e_px is None:
        status = f"ENTRY_{e_src}"
    elif x_px is None:
        status = f"EXIT_{x_src}"
    elif gross is None:
        status = "BAD_DIRECTION"
    else:
        status = "OK"
    bench_bps, bench_n = _universe_return_memo(entry_at, exit_at, cache)
    sign = {"LONG": 1.0, "SHORT": -1.0}.get(str(direction).upper())
    excess = (gross - sign * bench_bps
              if (gross is not None and bench_bps is not None and sign is not None) else None)
    return {
        f"{prefix}_entry_at": entry_at, f"{prefix}_entry_price": e_px,
        f"{prefix}_entry_source": e_src, f"{prefix}_entry_age_ms": e_age,
        f"{prefix}_exit_at": exit_at, f"{prefix}_exit_price": x_px,
        f"{prefix}_exit_source": x_src, f"{prefix}_exit_age_ms": x_age,
        f"{prefix}_gross_bps": gross, f"{prefix}_status": status,
        # référence de marché, scellée avec la ligne -- voir universe_return_bps()
        f"{prefix}_bench_bps": bench_bps, f"{prefix}_bench_n_symbols": bench_n,
        f"{prefix}_excess_bps": excess,
    }


def seal_digest(row: dict) -> str:
    """SHA-256 sur le CONTENU observé du label (prix, instants, brut, règle).
    Une ligne dont le digest ne recolle plus a été touchée après scellement —
    information, pas bug (même contrat que verify_manifest d'alpha_foundry_v5)."""
    sealed_fields = [
        "alpha_id", "decision_key", "symbol", "direction", "horizon",
        "event_time", "decided_at",
        "dec_entry_at", "dec_entry_price", "dec_exit_at", "dec_exit_price",
        "dec_gross_bps", "dec_bench_bps", "dec_excess_bps", "dec_status",
        "evt_entry_at", "evt_entry_price", "evt_exit_at", "evt_exit_price",
        "evt_gross_bps", "evt_bench_bps", "evt_excess_bps", "evt_status",
        "label_schema_version", "label_params_sha256",
    ]
    payload = {}
    for k in sealed_fields:
        v = row.get(k)
        if isinstance(v, pd.Timestamp):
            v = v.isoformat()
        elif isinstance(v, float):
            v = repr(round(v, 10))
        payload[k] = v
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def label_one(alpha_id: str, spec: LabelSpec, row: pd.Series,
              now: pd.Timestamp, code_sha: str, params_sha: str,
              cache: Optional[MarkSeriesCache] = None) -> Optional[dict]:
    """Label d'UNE décision, ou None si elle n'est pas encore mûre.

    Mûre = l'horizon compté depuis `decided_at` est écoulé. C'est bien
    l'ancrage DÉCISION qui commande l'échéance : c'est le dernier des deux à
    arriver, donc le seul qui garantisse que les deux jambes sont observables.
    """
    h = horizon_hours(spec.horizon)
    if h is None:
        return None
    event_time = _utc(row[spec.time_col])
    decided_at = _utc(row["decided_at"])
    if event_time is None or decided_at is None:
        return None

    dec_exit_at = decided_at + pd.Timedelta(hours=h)
    if dec_exit_at > now:
        return None   # pas encore mûre — repassera au prochain cycle

    symbol = str(row[spec.symbol_col])
    direction = str(row[spec.direction_col])
    delay_h = (now - dec_exit_at).total_seconds() / 3600.0

    out = {
        "alpha_id": alpha_id,
        "decision_key": decision_key(alpha_id, event_time, symbol, direction, decided_at),
        "symbol": symbol, "direction": direction,
        "horizon": spec.horizon, "horizon_hours": h,
        "event_time": event_time, "decided_at": decided_at,
        "decision_lag_h": round((decided_at - event_time).total_seconds() / 3600.0, 4),
    }
    out.update(_anchor_leg(symbol, direction, decided_at, dec_exit_at, "dec", cache))
    out.update(_anchor_leg(symbol, direction, event_time,
                           event_time + pd.Timedelta(hours=h), "evt", cache))

    # Prix indisponible mais décision encore jeune : on ne scelle RIEN, la
    # donnée peut encore arriver (partition en cours d'écriture). Au-delà
    # d'ABANDON_AFTER_HOURS, on scelle le refus pour que le ledger reste
    # exhaustif plutôt que d'accumuler des trous invisibles.
    if out["dec_status"] != "OK" and delay_h < ABANDON_AFTER_HOURS:
        return None

    out.update({
        "label_written_at": now,
        "label_delay_h": round(delay_h, 4),
        "label_timeliness": ("SEALED_AT_MATURITY" if delay_h <= SEAL_WINDOW_HOURS
                             else "LATE_BACKFILL"),
        "label_schema_version": LABEL_SCHEMA_VERSION,
        "label_params_sha256": params_sha,
        "label_code_commit_sha": code_sha,
        "cost_bps_roundtrip_base": COST_BPS_ROUNDTRIP_BASE,
        "cost_bps_roundtrip_stress": COST_BPS_ROUNDTRIP_STRESS,
    })
    out["seal_sha256"] = seal_digest(out)
    return out


def outcomes_path(alpha_id: str, lab_dir: Path = LAB_DIR) -> Path:
    return lab_dir / alpha_id / "outcomes.parquet"


def load_outcomes(alpha_id: str, lab_dir: Path = LAB_DIR) -> Optional[pd.DataFrame]:
    p = outcomes_path(alpha_id, lab_dir)
    return pd.read_parquet(p) if p.exists() else None


def append_sealed(path: Path, rows: List[dict]) -> Tuple[int, int]:
    """Écrit les nouvelles lignes. (n_écrites, n_refusées_car_déjà_scellées).

    PORTE À SENS UNIQUE : une `decision_key` déjà présente n'est jamais
    réécrite, même si le code de labellisation a changé entre-temps. C'est la
    propriété qui interdit de « retenter » un label jusqu'à ce qu'il plaise.
    """
    if not rows:
        return 0, 0
    new = pd.DataFrame(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        existing = pd.read_parquet(path)
        known = set(existing["decision_key"])
        blocked = int(new["decision_key"].isin(known).sum())
        new = new[~new["decision_key"].isin(known)]
        if new.empty:
            return 0, blocked
        merged = pd.concat([existing, new], ignore_index=True)
    else:
        blocked = 0
        merged = new
    merged.to_parquet(path, index=False)
    return len(new), blocked


def label_alpha(alpha_id: str, decisions: pd.DataFrame, spec: LabelSpec,
                now: Optional[pd.Timestamp] = None,
                lab_dir: Path = LAB_DIR,
                cache: Optional[MarkSeriesCache] = None) -> dict:
    """Labellise toutes les décisions FORWARD_LIVE mûres et non encore scellées."""
    now = now or pd.Timestamp.now(tz="UTC")
    cache = cache if cache is not None else _DEFAULT_CACHE
    code_sha, params_sha = git_head_sha(), label_params_digest()

    if "provenance" not in decisions.columns:
        return {"alpha_id": alpha_id, "status": "NO_PROVENANCE_COLUMN",
                "n_forward": 0, "n_new": 0, "n_blocked": 0}
    fwd = decisions[decisions["provenance"] == "FORWARD_LIVE"]
    missing = [c for c in (spec.time_col, spec.symbol_col, spec.direction_col, "decided_at")
               if c not in fwd.columns]
    if missing:
        return {"alpha_id": alpha_id, "status": f"MISSING_COLUMNS:{','.join(missing)}",
                "n_forward": len(fwd), "n_new": 0, "n_blocked": 0}

    existing = load_outcomes(alpha_id, lab_dir)
    known = set(existing["decision_key"]) if existing is not None else set()

    rows, n_pending = [], 0
    for _, r in fwd.iterrows():
        k = decision_key(alpha_id, _utc(r[spec.time_col]), str(r[spec.symbol_col]),
                         str(r[spec.direction_col]), _utc(r["decided_at"]))
        if k in known:
            continue
        rec = label_one(alpha_id, spec, r, now, code_sha, params_sha, cache)
        if rec is None:
            n_pending += 1
            continue
        rows.append(rec)

    n_new, n_blocked = append_sealed(outcomes_path(alpha_id, lab_dir), rows)
    sealed_now = sum(1 for r in rows if r["label_timeliness"] == "SEALED_AT_MATURITY")
    refused = sum(1 for r in rows if r["dec_status"] != "OK")
    return {
        "alpha_id": alpha_id, "status": "OK",
        "n_forward": len(fwd), "n_already_labeled": len(known),
        "n_new": n_new, "n_blocked_already_sealed": n_blocked,
        "n_sealed_at_maturity": sealed_now, "n_late_backfill": len(rows) - sealed_now,
        "n_refused_no_price": refused, "n_pending_not_mature_or_waiting_price": n_pending,
        "label_params_sha256": params_sha,
    }


# ═══════════════════════════════════════════════════════════════════════════
# LECTURE — le net n'est jamais scellé, il est dérivé sous hypothèse déclarée
# ═══════════════════════════════════════════════════════════════════════════

def net_bps(gross_bps, cost_bps: float):
    return None if gross_bps is None or pd.isna(gross_bps) else gross_bps - cost_bps


@dataclass(frozen=True)
class OutcomeStats:
    n_labeled: int
    n_episodes: int              # après decluster -- la VRAIE taille d'échantillon
    anchor: str                  # "dec" (exécutable) ou "evt" (comparable validation)
    metric: str                  # "excess" (net du marché) ou "gross" (bêta compris)
    gross_bps_mean: Optional[float]
    net_bps_base: Optional[float]
    net_bps_stress: Optional[float]
    profit_factor_base: Optional[float]
    hit_rate: Optional[float]
    ci95_low_bps: Optional[float]
    ci95_high_bps: Optional[float]
    sealed_at_maturity: int
    late_backfill: int


def _episode_means(df: pd.DataFrame, col: str, cluster_window_hours: float,
                   cross_sectional: bool) -> pd.Series:
    """Un épisode = une preuve. Deux décisions du même symbole à moins d'une
    fenêtre l'une de l'autre décrivent le même mouvement de prix : les compter
    deux fois gonfle n et rétrécit l'IC à tort (piège de decluster déjà
    rencontré 4× dans ce projet). On agrège donc AVANT toute statistique.

    `cross_sectional` : quand le déclencheur est commun à tout l'univers (un
    choc BTC répercuté sur N alts), le decluster same-symbol ne protège de
    rien -- N symboles simultanés restent N copies du même événement. On
    regroupe alors sur le TEMPS seul, tous symboles confondus. Voir
    LabelSpec.cross_sectional."""
    from src.institutional.live_alpha_lab.episodes import decluster
    ok = df[df[col].notna()].copy()
    if ok.empty:
        return pd.Series(dtype="float64")
    if cross_sectional:
        ok["_episode_symbol"] = "_ALL_"
        ok = decluster(ok, "event_time", "_episode_symbol", cluster_window_hours)
    else:
        ok = decluster(ok, "event_time", "symbol", cluster_window_hours)
    return ok.groupby("cluster_id")[col].mean()


def summarize_outcomes(df: Optional[pd.DataFrame], anchor: str = "dec",
                       metric: str = "excess",
                       cluster_window_hours: float = 24.0,
                       cross_sectional: bool = False,
                       n_boot: int = 2000, seed: int = 7) -> Optional[OutcomeStats]:
    """Statistiques d'un ledger de labels, au niveau ÉPISODE.

    `anchor` : "dec" (exécutable, ancré sur decided_at) ou "evt" (comparable
    à la validation, ancré sur event_time).
    `metric` : "excess" (net de la référence de marché — le défaut, parce que
    c'est le seul chiffre qui répond à « ce mécanisme a-t-il un edge »)
    ou "gross" (rendement absolu, bêta compris).
    """
    if df is None or df.empty:
        return None
    col = f"{anchor}_{'excess' if metric == 'excess' else 'gross'}_bps"
    if col not in df.columns or f"{anchor}_status" not in df.columns:
        return None
    ok = df[df[f"{anchor}_status"] == "OK"]
    ep = _episode_means(ok, col, cluster_window_hours, cross_sectional)
    tl = df["label_timeliness"].value_counts() if "label_timeliness" in df else {}
    n_sealed = int(tl.get("SEALED_AT_MATURITY", 0))
    n_late = int(tl.get("LATE_BACKFILL", 0))
    if ep.empty:
        return OutcomeStats(len(ok), 0, anchor, metric, None, None, None, None, None,
                            None, None, n_sealed, n_late)

    gross = float(ep.mean())
    net_base = ep - COST_BPS_ROUNDTRIP_BASE
    wins, losses = net_base[net_base > 0].sum(), -net_base[net_base < 0].sum()
    pf = float(wins / losses) if losses > 0 else None

    rng = np.random.default_rng(seed)
    vals = ep.to_numpy(dtype=float)
    boot = rng.choice(vals, size=(n_boot, len(vals)), replace=True).mean(axis=1)
    lo, hi = (float(x) for x in np.percentile(boot, [2.5, 97.5]))

    return OutcomeStats(
        n_labeled=len(ok), n_episodes=len(ep), anchor=anchor, metric=metric,
        gross_bps_mean=round(gross, 2),
        net_bps_base=round(gross - COST_BPS_ROUNDTRIP_BASE, 2),
        net_bps_stress=round(gross - COST_BPS_ROUNDTRIP_STRESS, 2),
        profit_factor_base=round(pf, 3) if pf is not None else None,
        hit_rate=round(float((net_base > 0).mean()), 3),
        ci95_low_bps=round(lo - COST_BPS_ROUNDTRIP_BASE, 2),
        ci95_high_bps=round(hi - COST_BPS_ROUNDTRIP_BASE, 2),
        sealed_at_maturity=n_sealed, late_backfill=n_late,
    )


# Bases possibles de `expected_net_bps` dans le registre. Ajoutées 2026-09-06
# après avoir constaté que la colonne mélangeait deux grandeurs : 27,1 pour
# LIQ_CASCADE_REPEAT_V1 est un net ABSOLU (freeze_spec.net_bps_full_sample),
# 9,2 pour SHORT_COVERING est un EXCESS vs baseline (son net absolu vaut
# -2,72 full / +2,3 OOS). Les comparer entre eux, ou comparer le mauvais des
# deux au forward, produit un ratio qui a l'air d'un chiffre sans en être un.
EXPECTED_BASIS_ABSOLUTE = "ABSOLUTE"
EXPECTED_BASIS_EXCESS = "EXCESS_VS_BASELINE"


def edge_retention(expected_net_bps: Optional[float], basis: Optional[str], *,
                   gross_net_bps: Optional[float] = None,
                   excess_net_bps: Optional[float] = None) -> Optional[float]:
    """net_bps forward / net_bps de validation, sur l'ancrage ÉVÉNEMENT — le
    seul comparable, puisque le backtest s'ancre sur la barre de l'événement.

    `basis` dit LAQUELLE des deux mesures forward est comparable :
      ABSOLUTE            -> le `gross` forward (rendement du symbole)
      EXCESS_VS_BASELINE  -> l'`excess` forward (net de l'univers)
    Une base absente renvoie None. Fail closed délibéré : deviner la base,
    c'est précisément l'erreur que ce paramètre existe pour rendre impossible,
    et un ratio faux se cite plus facilement qu'une case vide.

    Un dénominateur nul ou négatif ne produit PAS de ratio non plus : la
    rétention n'a pas de sens quand la référence n'est pas un edge positif
    (WHALE_LSR affiche -57,8 bps attendus)."""
    if expected_net_bps is None or expected_net_bps <= 0:
        return None
    if basis == EXPECTED_BASIS_ABSOLUTE:
        forward = gross_net_bps
    elif basis == EXPECTED_BASIS_EXCESS:
        forward = excess_net_bps
    else:
        return None
    return None if forward is None else round(forward / expected_net_bps, 3)
