"""
src/institutional/live_alpha_lab/portfolio.py
─────────────────────────────────────────────────────────────────────────────
PORTFOLIO_SHADOW_LAYER — agrège les PortfolioIntent de tous les alphas
FORWARD_LIVE, déduplique le risque corrélé, applique les budgets par
famille/risk_bucket, limite l'exposition gross/net et par actif, calcule les
delta_position réels, les envoie à ShadowExecutionAdapter (fill simulé à un
prix réel + coûts), et tient un vrai MARK-TO-MARKET par position (phase
ECONOMIC TRUTH, 2026-08-31 soir).

Pour chaque position : quantity (signée), entry_price (moyenne pondérée),
realized_pnl, unrealized_pnl (mark-to-market au `get_mark()` courant), fees,
funding accumulés. Portfolio : equity = cash_initial + realized_pnl +
unrealized_pnl - fees + funding - borrow (borrow TOUJOURS 0.0, PAS modélisé
faute de source de données margin/lending -- champ explicite plutôt qu'un
chiffre inventé).

⚠ Si AUCUN mark n'est disponible pour un instrument, ce module ne trade PAS
dessus ce step (log + skip) -- jamais un prix inventé. Si un mark UTILISÉ est
stale (MarkQuote.is_stale()), l'état du portefeuille passe status="DEGRADED"
et ce fait est propagé dans le snapshot d'équity (ne pas lire une métrique
"OK" produite avec un prix périmé).
"""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from src.institutional.live_alpha_lab.gate import apply_screen
from src.institutional.live_alpha_lab.intents import PortfolioIntent
from src.institutional.live_alpha_lab.marks import get_mark
from src.institutional.live_alpha_lab.portfolio_config import PortfolioConfig

ROOT = Path(__file__).resolve().parents[3]
PORTFOLIO_DIR = ROOT / "reports" / "live_alpha_lab" / "portfolios"

# Coûts : mêmes constantes que le reste du projet (src/institutional/
# execution/execution_simulator.py::ExecutionConfig par défaut).
TAKER_FEE_BPS = 5.0
FIXED_SLIPPAGE_BPS = 2.0
FUNDING_INTERVAL_HOURS = 8.0   # cadence standard funding perp

# ⚠ Bug réel trouvé 2026-09-03 (soir, phase PORTFOLIO FORWARD) : aggregate()
# dimensionnait chaque intent live dédupliqué en `budget_alpha * frac`, donc K
# intents simultanés d'UN MÊME alpha sommaient à K × son budget -- seuls le
# plafond par actif et le plafond gross global rattrapaient ensuite. Constaté
# en live : les 5 portefeuilles shadow ~100 % gross LONG sur ~30 alt perps,
# toutes détenues par SHORT_COVERING_CONTINUATION_V1 dont le budget documenté
# est 1/6 du capital (P1/P2, family_budget_fraction) ou 5 % (P3,
# per_alpha_budget_fraction). Correctif : la somme des frac d'un alpha est
# normalisée à 1 au plus (voir aggregate()). Chaque NOUVEAU point d'equity_curve
# porte `sizing_rule` = cette constante (les points antérieurs ne sont jamais
# réécrits) -- c'est la frontière du nouveau segment forward (BUG POLICY,
# en-tête de configs/live_alpha_registry.yaml).
SIZING_RULE = "PER_ALPHA_BUDGET_CAP_V2"

# ═══════════════════════════════════════════════════════════════════════════
# item P0.3 — BANDE DE NON-NÉGOCIATION (audit forward 2026-09-04)
# ═══════════════════════════════════════════════════════════════════════════
# Mesure : 1 073 026 USD de turnover sur 33 333 USD d'exposition brute en
# 26,6 h (32x), pour 751 USD de frictions, alors que l'horizon déclaré de
# l'alpha est de 4 h. 96,2 % de ce turnover se concentrait dans les 38 cycles
# (sur 107) où la CIBLE changeait, et le seuil de déclenchement d'un ordre
# était `abs(delta_notional) >= 1e-6` -- c'est-à-dire aucun seuil du tout.
#
# Deux causes distinctes, toutes deux corrigées ici :
#
#   (a) REDISTRIBUTION MÉCANIQUE. aggregate() divise le budget d'un alpha par
#       `sum_frac_by_alpha` (la somme des fractions de ses intents VIVANTS).
#       Quand un intent entre ou sort, ce diviseur change, donc la cible de
#       TOUTES les autres positions du même alpha change proportionnellement,
#       alors qu'AUCUN de leurs signaux n'a bougé. C'est du turnover à
#       espérance nulle et à coût strictement positif.
#
#   (b) DÉRIVE DE PRIX. La cible est un NOTIONNEL constant ; quand le prix
#       bouge, la quantité cible bouge avec lui, donc un ordre partait à
#       chaque cycle même signal et budget parfaitement figés.
#
# La bande. On ne rééquilibre que si l'écart à la cible vaut plus cher à
# porter qu'à corriger. Corriger coûte un aller-retour :
#
#     round_trip_cost_frac = 2 x (TAKER_FEE_BPS + FIXED_SLIPPAGE_BPS) / 1e4
#
# Porter l'écart coûte l'espérance qu'on rate dessus, soit au plus l'edge
# déclaré de la décision, `edge_frac`. La bande est le rapport des deux :
#
#     band = round_trip_cost_frac / edge_frac        (borné à [0, 1])
#
# Lecture : un alpha qui espère 60 bps peut se permettre de corriger un écart
# de 23 % (14/60) ; un alpha qui espère 14 bps ne peut RIEN corriger
# (band=1) -- ce qui est le verdict économique correct, pas une punition.
#
# Aucune constante inventée : TAKER_FEE_BPS et FIXED_SLIPPAGE_BPS sont déjà
# les coûts du simulateur, `edge_frac` vient de la décision (colonne
# expected_return du ledger) ou, à défaut, de `expected_net_bps` du registre.
# Si NI l'un NI l'autre n'existe -> band = 1.0, la politique la plus
# conservatrice (voir docstring de no_trade_band_fraction).
#
# La bande ne peut JAMAIS empêcher : une ouverture depuis flat, une clôture
# vers flat, un retournement de sens. Ces trois cas satisfont
# |delta| >= 1.0 x max(|cible|, |accepté|) par construction, donc passent
# même à band = 1.0. Seuls les REDIMENSIONNEMENTS sont filtrés.
NO_TRADE_BAND_RULE = "COST_OVER_EDGE_BAND_V1"

# Epsilon purement NUMÉRIQUE (bruit flottant), pas un seuil économique : les
# décisions économiques sont prises par la bande ci-dessus.
NUMERICAL_EPSILON_USD = 1e-6


def round_trip_cost_fraction() -> float:
    """Coût d'un aller-retour, en fraction de prix. Une seule source pour les
    frais et le slippage : les constantes du simulateur, ci-dessus."""
    return 2.0 * (TAKER_FEE_BPS + FIXED_SLIPPAGE_BPS) / 10_000.0


def no_trade_band_fraction(edge_fraction: Optional[float]) -> float:
    """Largeur de bande, en fraction de la position visée.

    `edge_fraction` = espérance de rendement de la décision sur son horizon
    (fraction de prix). Retourne `round_trip_cost / edge`, borné à [0, 1].

    Bornes et cas dégénérés, tous du côté prudent :
      - edge None, NaN, <= 0  -> 1.0 (aucun redimensionnement ; ouvertures,
        clôtures et retournements restent possibles). Un edge inconnu ou nul
        ne justifie AUCUN coût de rééquilibrage.
      - edge <= coût A/R      -> 1.0 (l'alpha ne paie pas son propre churn).
      - edge très grand       -> band -> 0, on suit la cible de près.
    """
    if edge_fraction is None:
        return 1.0
    try:
        edge = float(edge_fraction)
    except (TypeError, ValueError):
        return 1.0
    if not (edge > 0) or edge != edge:     # <= 0 ou NaN
        return 1.0
    return min(1.0, round_trip_cost_fraction() / edge)


# Classes de turnover — répondent à « distinguer turnover de signal vs
# turnover mécanique » (item P0.3). Portées par chaque ordre et cumulées
# dans PortfolioState.cumulative_turnover_by_class.
TURNOVER_ENTRY = "ENTRY"                       # ouverture depuis flat
TURNOVER_EXIT = "EXIT"                         # clôture vers flat
TURNOVER_FLIP = "FLIP"                         # retournement de sens
TURNOVER_SIGNAL_RESIZE = "SIGNAL_RESIZE"       # le jeu d'intents a changé
TURNOVER_MECHANICAL_RESIZE = "MECHANICAL_RESIZE"   # ni entrée/sortie/flip ni changement de signal
TURNOVER_FILL_CONVERGENCE = "FILL_CONVERGENCE"  # suite d'un ordre partiellement rempli


@dataclass
class Fill:
    """Paper execution réaliste (item 12) : le fill_price inclut DÉJÀ le
    slippage (adverse selon le sens du trade), pas un cout dollar séparé
    appliqué à un prix mid fictif."""
    instrument: str
    delta_quantity: float
    mark_price: float
    fill_price: float
    fee_usd: float
    mark_source: str
    mark_stale: bool

    @property
    def delta_notional_at_fill(self) -> float:
        return self.delta_quantity * self.fill_price


def shadow_execute(delta_quantity: float, instrument: str, mark) -> Fill:
    """AUCUN ordre réel. `mark` = MarkQuote (jamais None ici -- l'appelant a
    déjà vérifié l'existence avant d'appeler)."""
    slip_frac = FIXED_SLIPPAGE_BPS / 10_000
    sign = 1.0 if delta_quantity > 0 else -1.0
    fill_price = mark.price * (1 + sign * slip_frac)   # adverse : on paie plus cher à l'achat, moins cher à la vente pour SOI
    notional = abs(delta_quantity) * fill_price
    fee = notional * TAKER_FEE_BPS / 10_000
    return Fill(instrument=instrument, delta_quantity=delta_quantity, mark_price=mark.price,
               fill_price=fill_price, fee_usd=fee, mark_source=mark.mark_source,
               mark_stale=mark.is_stale())


def _dedup_correlated(intents: List[PortfolioIntent],
                      max_dominant_per_family: Optional[int]) -> List[PortfolioIntent]:
    """Ne jamais stacker deux intents corrélés (même correlation_family, même
    instrument, même direction) au même moment : garde le MAX
    target_position_fraction, pas la somme."""
    if max_dominant_per_family:
        by_family: Dict[str, List[PortfolioIntent]] = defaultdict(list)
        for it in intents:
            by_family[it.correlation_family].append(it)
        kept = []
        for fam, group in by_family.items():
            by_alpha_conf: Dict[str, list] = defaultdict(list)
            for it in group:
                by_alpha_conf[it.alpha_id].append(it.confidence)
            best_alpha = max(by_alpha_conf, key=lambda a: sum(by_alpha_conf[a]) / len(by_alpha_conf[a]))
            kept.extend([it for it in group if it.alpha_id == best_alpha])
        intents = kept

    groups: Dict[tuple, List[PortfolioIntent]] = defaultdict(list)
    for it in intents:
        groups[(it.correlation_family, it.instrument, it.direction)].append(it)
    out = []
    for key, group in groups.items():
        best = max(group, key=lambda it: it.target_position_fraction)
        out.append(best)
    return out


def _alpha_budget(risk_bucket: str, alpha_id: str, config: PortfolioConfig,
                  n_alphas_in_bucket: int) -> float:
    if config.per_alpha_budget_fraction is not None:
        return config.capital_eur * config.per_alpha_budget_fraction
    frac = config.family_budget_fraction.get(risk_bucket, config.default_family_budget_fraction)
    return config.capital_eur * frac / max(n_alphas_in_bucket, 1)


@dataclass
class AggregationResult:
    target_notional: Dict[str, float]
    owner: Dict[str, str]
    # item 6 : ledger des intents individuels par instrument, AVANT dedup/cap,
    # pour mesurer overlap/compétition de capital/contribution marginale.
    raw_intents_by_instrument: Dict[str, List[dict]]
    # item P0.2/P0.3 : timestamp ISO de l'intent GAGNANT (celui qui a fixé
    # target/owner) par instrument -- sert à construire intent_id/signal_id
    # de l'ordre shadow correspondant, pour la reconstruction de trace.
    owner_intent_ts: Dict[str, str] = field(default_factory=dict)
    # item P0.4 : instruments où TOUS les intents qui les visaient (y compris
    # la jambe B en multi-leg) sont désormais expirés -- signal utilisé par
    # step() pour marquer EXIT_REASON=ALPHA_HORIZON_EXPIRY. Si un AUTRE
    # intent vivant vise encore le même instrument, il n'apparaît PAS ici :
    # la réduction/clôture éventuelle vient d'ailleurs (screen/cap/dedup),
    # pas de l'expiration.
    expired_driven_instruments: set = field(default_factory=set)
    # item P0.3 : empreinte du JEU D'INTENTS VIVANTS visant chaque instrument
    # (alpha, sens, fraction, timestamp de décision). Deux steps qui portent
    # la même empreinte pour un instrument ont, par définition, le MÊME signal
    # dessus -- tout écart de cible entre ces deux steps est alors mécanique
    # (rediviseur de budget, plafond, overlay), jamais un changement d'avis.
    intent_signature: Dict[str, str] = field(default_factory=dict)
    # item P0.3 : espérance déclarée de l'intent GAGNANT par instrument
    # (fraction de prix), source de la largeur de bande. Absent = inconnu.
    expected_edge: Dict[str, float] = field(default_factory=dict)
    # item P0.3 : dénominateur de budget par alpha, à CLIQUET (voir aggregate).
    # Retourné pour être persisté par step() et réinjecté au cycle suivant.
    denominator_high_water: Dict[str, float] = field(default_factory=dict)
    # item A3 : le multiplicateur RÉELLEMENT appliqué à ce pas. Porté par le
    # résultat plutôt que redemandé à step(), qui ne le connaît pas -- c'est
    # aggregate() qui l'applique, donc c'est aggregate() qui sait s'il a mordu.
    # 1.0 quand le portefeuille n'a pas d'overlay : « pas d'overlay » et
    # « overlay neutre » se distinguent par config.apply_vol_overlay.
    vol_overlay_multiplier_applied: float = 1.0


def aggregate(intents: List[PortfolioIntent], config: PortfolioConfig,
             screened_symbols: set, vol_overlay_multiplier: float = 1.0,
             as_of: Optional[pd.Timestamp] = None,
             denominator_high_water: Optional[Dict[str, float]] = None) -> AggregationResult:
    """⚠ Bug réel trouvé 2026-09-01 (phase ECONOMIC TRUTH) : `PortfolioIntent.
    expiry` existait comme champ mais n'était JAMAIS vérifié -- une décision
    restait indéfiniment "active" (contribuait au dedup MAX pour toujours),
    au lieu d'expirer après son horizon (ex. fwd_4h pour liq_cascade). Une
    vieille décision à forte conviction pouvait masquer indéfiniment des
    décisions plus récentes du même instrument/correlation_family. Filtré
    ici : un intent expiré (`expiry <= as_of`) est exclu de l'agrégation
    (mais reste dans `raw_intents_by_instrument`/l'intent_ledger pour la
    traçabilité -- on ne perd pas la trace qu'il a existé, juste son effet
    sur la position courante)."""
    as_of = as_of if as_of is not None else pd.Timestamp.now(tz="UTC")
    live_intents = [it for it in intents if it.expiry > as_of]

    def _targeted_instruments(its: List[PortfolioIntent]) -> set:
        out: set = set()
        for it in its:
            out.add(it.instrument)
            if it.multi_leg and it.leg_instrument_b:
                out.add(it.leg_instrument_b)
        return out

    expired_driven_instruments = _targeted_instruments(intents) - _targeted_instruments(live_intents)

    raw_by_instrument: Dict[str, List[dict]] = defaultdict(list)
    for it in intents:
        raw_by_instrument[it.instrument].append({
            "alpha_id": it.alpha_id, "direction": it.direction,
            "target_position_fraction": it.target_position_fraction, "confidence": it.confidence,
        })
        if it.multi_leg and it.leg_instrument_b:
            raw_by_instrument[it.leg_instrument_b].append({
                "alpha_id": it.alpha_id, "direction": "SHORT" if it.direction == "LONG" else "LONG",
                "target_position_fraction": it.target_position_fraction, "confidence": it.confidence,
            })

    deduped = _dedup_correlated(live_intents, config.max_dominant_per_correlation_family)

    n_alphas_per_bucket: Dict[str, set] = defaultdict(set)
    for it in deduped:
        n_alphas_per_bucket[it.risk_bucket].add(it.alpha_id)

    # ⚠ Correctif 2026-09-03 (SIZING_RULE = PER_ALPHA_BUDGET_CAP_V2) : la somme
    # des frac vivantes d'un alpha ne doit jamais dépasser 1 (= son budget).
    # Un intent multi-leg compte UNE fois (pas une par jambe). Les adapters qui
    # somment déjà à 1 (cross-sectional, jambes Amihud) ne sont pas touchés :
    # somme <= 1 -> diviseur 1.
    sized: List[tuple] = []
    sum_frac_by_alpha: Dict[str, float] = defaultdict(float)
    for it in deduped:
        frac = apply_screen(it.target_position_fraction, it.instrument, it.direction, screened_symbols)
        if config.apply_vol_overlay:
            frac *= vol_overlay_multiplier
        if frac <= 0:
            continue
        sum_frac_by_alpha[it.alpha_id] += frac
        sized.append((it, frac))

    # ═══ item P0.3, cause (a) : DÉNOMINATEUR DE BUDGET À CLIQUET ═══════════
    # `sum_frac_by_alpha` est le diviseur qui garantit qu'un alpha ne dépasse
    # jamais son budget (correctif PER_ALPHA_BUDGET_CAP_V2). Mais il SUIT le
    # nombre d'intents vivants : quand un intent SORT, il diminue, donc la
    # cible de toutes les positions restantes AUGMENTE mécaniquement — et
    # toutes sont retradées alors qu'aucun de leurs signaux n'a bougé.
    #
    # Correction : le dénominateur ne descend jamais tant que l'alpha a au
    # moins un intent vivant. Il monte quand une entrée l'exige (le plafond de
    # budget reste strictement respecté, c'est non négociable) et reste en
    # place quand un intent sort — le budget libéré reste INUTILISÉ plutôt que
    # de gonfler les positions survivantes. Une sortie ailleurs n'est pas une
    # information nouvelle sur les noms restants ; elle ne doit pas les
    # redimensionner.
    #
    # Remise à zéro quand l'alpha n'a plus AUCUN intent vivant : l'épisode est
    # terminé, le suivant repart proprement. C'est un point de reset naturel,
    # pas un paramètre.
    hw: Dict[str, float] = dict(denominator_high_water or {})
    alphas_with_live_intents = {it.alpha_id for it, _ in sized}
    for alpha_id in list(hw):
        if alpha_id not in alphas_with_live_intents:
            del hw[alpha_id]
    for alpha_id, s in sum_frac_by_alpha.items():
        hw[alpha_id] = max(hw.get(alpha_id, 0.0), s)

    target: Dict[str, float] = defaultdict(float)
    owner: Dict[str, str] = {}
    owner_intent_ts: Dict[str, str] = {}
    # item P0.3 : empreinte du signal par instrument. Construite à partir des
    # intents RÉELLEMENT dimensionnés (post-expiry, post-dedup, post-screen) --
    # c'est bien « quel signal vise cet instrument maintenant », pas « quelles
    # décisions ont un jour existé ». `frac` est celui d'AVANT la division par
    # sum_frac_by_alpha : c'est la conviction de l'alpha, indépendante du
    # nombre de ses autres positions -- sinon l'empreinte changerait à chaque
    # entrée/sortie et tout redimensionnement mécanique passerait pour du
    # signal, ce qui viderait la mesure de son sens.
    sig_parts: Dict[str, List[str]] = defaultdict(list)
    edge_by_instrument: Dict[str, float] = {}

    def _note(instr: str, it: PortfolioIntent, frac: float, direction: str) -> None:
        sig_parts[instr].append(
            f"{it.alpha_id}|{direction}|{frac:.12g}|{it.timestamp.isoformat()}")
        if it.expected_edge_fraction is not None:
            prev = edge_by_instrument.get(instr)
            # plusieurs intents sur le même instrument : garder le PLUS PETIT
            # edge (bande la plus large = le plus prudent), jamais le plus
            # flatteur.
            e = float(it.expected_edge_fraction)
            edge_by_instrument[instr] = e if prev is None else min(prev, e)

    for it, frac in sized:
        budget = _alpha_budget(it.risk_bucket, it.alpha_id, config,
                               len(n_alphas_per_bucket[it.risk_bucket]))
        notional = budget * frac / max(1.0, hw.get(it.alpha_id, sum_frac_by_alpha[it.alpha_id]))
        sign = 1.0 if it.direction == "LONG" else -1.0

        if it.multi_leg and it.leg_instrument_b:
            target[it.instrument] += sign * notional
            target[it.leg_instrument_b] += -sign * notional
            owner[it.instrument] = it.alpha_id
            owner[it.leg_instrument_b] = it.alpha_id
            owner_intent_ts[it.instrument] = it.timestamp.isoformat()
            owner_intent_ts[it.leg_instrument_b] = it.timestamp.isoformat()
            _note(it.instrument, it, frac, it.direction)
            _note(it.leg_instrument_b, it, frac,
                  "SHORT" if it.direction == "LONG" else "LONG")
        else:
            target[it.instrument] += sign * notional
            owner[it.instrument] = it.alpha_id
            owner_intent_ts[it.instrument] = it.timestamp.isoformat()
            _note(it.instrument, it, frac, it.direction)

    cap_asset = config.capital_eur * config.max_per_asset_fraction
    for instr, notional in list(target.items()):
        if abs(notional) > cap_asset:
            target[instr] = cap_asset * (1 if notional > 0 else -1)

    gross = sum(abs(v) for v in target.values())
    cap_gross = config.capital_eur * config.max_gross_exposure_fraction
    if gross > cap_gross and gross > 0:
        scale = cap_gross / gross
        target = {k: v * scale for k, v in target.items()}

    intent_signature = {
        instr: hashlib.sha1(";".join(sorted(parts)).encode()).hexdigest()[:16]
        for instr, parts in sig_parts.items()
    }
    return AggregationResult(dict(target), owner, dict(raw_by_instrument), owner_intent_ts,
                             expired_driven_instruments, intent_signature,
                             dict(edge_by_instrument), hw,
                             vol_overlay_multiplier if config.apply_vol_overlay else 1.0)


@dataclass
class Position:
    instrument: str
    quantity: float = 0.0
    entry_price: float = 0.0
    realized_pnl: float = 0.0
    fees_paid: float = 0.0
    funding_paid: float = 0.0   # signé : négatif = payé, positif = reçu
    owner_alpha: str = "UNATTRIBUTED"   # dernier alpha_id gagnant du dedup sur cet instrument

    @property
    def notional(self) -> float:
        return self.quantity * self.entry_price   # notional au COÛT d'entrée, pas au mark

    def unrealized_pnl(self, mark_price: float) -> float:
        return self.quantity * (mark_price - self.entry_price)


@dataclass
class PortfolioState:
    positions: Dict[str, dict] = field(default_factory=dict)   # instrument -> Position.__dict__ (JSON-serialisable)
    cash: float = 0.0
    peak_equity: float = 0.0
    cumulative_fees_usd: float = 0.0
    cumulative_turnover_usd: float = 0.0
    cumulative_funding_usd: float = 0.0
    # ⚠ Bug réel trouvé par test_mtm_position_close_realizes_pnl : sommer
    # realized_pnl depuis `positions` échoue dès qu'une position clôturée
    # (quantity=0) est nettoyée du dict AVANT la somme -- son PnL réalisé
    # disparaîtrait silencieusement. Total cumulatif au niveau portfolio,
    # jamais re-dérivé des positions courantes.
    cumulative_realized_pnl: float = 0.0
    cumulative_cost_by_alpha: Dict[str, float] = field(default_factory=dict)
    # item 5 : POSITION_PNL (par instrument, ci-dessus dans `positions`) vs
    # ALPHA_ATTRIBUTED_PNL (ci-dessous) -- règle déterministe documentée :
    # tout le realized+unrealized d'un instrument est attribué à l'alpha
    # "propriétaire" (owner_by_instrument, le gagnant du dedup corrélé) AU
    # MOMENT du calcul -- si l'ownership change entre deux steps (rare,
    # nécessiterait 2 alphas différents de correlation_family différentes
    # sur le même instrument), le PnL déjà réalisé sous l'ancien owner
    # RESTE crédité à l'ancien owner (jamais réattribué rétroactivement).
    cumulative_pnl_by_alpha: Dict[str, float] = field(default_factory=dict)
    equity_curve: List[dict] = field(default_factory=list)
    last_step_ts: Optional[str] = None
    initialized: bool = False
    # item P0.2 : ledger d'ordres/fills shadow, append-only, persisté au même
    # titre que positions/equity_curve (durabilité restart -- l'adapter lui-
    # même n'est PAS persisté, seul son résultat via step() l'est ici).
    orders: List[dict] = field(default_factory=list)
    fills: List[dict] = field(default_factory=list)
    # ── item P0.3 (bande de non-négociation) ─────────────────────────────
    # `accepted_target_notional` est la cible à laquelle le portefeuille s'est
    # ENGAGÉ. Elle ne suit la cible calculée que lorsque l'écart franchit la
    # bande. Entre deux franchissements, la position est laissée tranquille --
    # c'est tout le mécanisme anti-churn.
    accepted_target_notional: Dict[str, float] = field(default_factory=dict)
    # empreinte du signal au moment du dernier engagement : sert à qualifier
    # un futur écart de cible en SIGNAL_RESIZE vs MECHANICAL_RESIZE.
    accepted_intent_signature: Dict[str, str] = field(default_factory=dict)
    # instruments dont le dernier ordre est resté PARTIELLEMENT rempli : on
    # continue de converger vers la cible acceptée sans repasser par la bande
    # (un ordre inachevé n'est pas du churn, c'est une décision non terminée).
    converging: Dict[str, bool] = field(default_factory=dict)
    cumulative_turnover_by_class: Dict[str, float] = field(default_factory=dict)
    # item P0.3 : dénominateur de budget à cliquet par alpha (voir aggregate).
    alpha_denominator_high_water: Dict[str, float] = field(default_factory=dict)
    # turnover NON exécuté grâce à la bande, pour pouvoir chiffrer ce que la
    # correction évite (comptabilité honnête : on mesure ce qu'on refuse).
    suppressed_turnover_usd: float = 0.0
    suppressed_order_count: int = 0
    # item B3 : ce que le PLAFOND DE LIQUIDITÉ refuse, compté au lieu d'être
    # seulement subi. Même principe que suppressed_turnover_usd (on mesure ce
    # qu'on refuse), appliqué à l'autre cause de non-exécution.
    #
    # Ces deux compteurs ne changent AUCUN fill : ils rendent visible un
    # comportement déjà présent. Mesuré avant leur ajout, sur 1 634 ordres du
    # forward : le plafond a mordu 16 fois (1,0 %), pour un plafond adossé à
    # `open_interest x 0,002` -- un STOCK de positions, pas une profondeur de
    # carnet. Sans compteur, personne ne pouvait dire s'il mordait 1 % ou 50 %
    # du temps, donc personne ne pouvait dire si la capacité était contrainte.
    capped_order_count: int = 0
    capped_notional_usd: float = 0.0
    # item A3 : l'overlay de vol était APPLIQUÉ puis immédiatement OUBLIÉ.
    # SUMMARY.json ne portait que sa valeur COURANTE (`vol_overlay_multiplier:
    # 1.0`), ce qui se lit comme « il n'a jamais mordu » alors que c'est juste
    # « il ne mord pas maintenant ». Mesuré : 45,3 % des `combined_forecast_z`
    # historiques sont > 0, donc l'overlay MORD régulièrement — et P1_VOL_OVERLAY
    # diverge bien de P1_CONTROL (+1232 vs +1288 sur SHORT_COVERING). Sans
    # historique, impossible de dire combien de fois ni de combien, donc
    # impossible de savoir si les trois variantes P1 testent trois hypothèses
    # ou une seule en trois exemplaires.
    overlay_steps: int = 0                    # steps où l'overlay était actif
    overlay_binding_steps: int = 0            # steps où il a réellement réduit la taille
    overlay_multiplier_min: float = 1.0       # la morsure la plus forte observée
    overlay_multiplier_sum: float = 0.0       # pour la moyenne, sans garder l'historique complet


def state_path(portfolio_name: str) -> Path:
    return PORTFOLIO_DIR / portfolio_name / "state.json"


def intent_ledger_path(portfolio_name: str) -> Path:
    return PORTFOLIO_DIR / portfolio_name / "intent_ledger.parquet"


def load_state(portfolio_name: str, initial_cash: float) -> PortfolioState:
    """⚠ Transition de méthodologie 2026-08-31 (phase ECONOMIC TRUTH) : l'état
    portfolio pré-MTM (positions = notional brut seulement, pas de quantity/
    entry_price/realized_pnl) ne peut pas être migré sans INVENTER un prix
    d'entrée historique -- ça violerait "ne jamais recalculer le passé avec
    des prix futurs" (item 4 du mandat). Un état pré-MTM détecté (schéma
    incompatible) est donc ARCHIVÉ (jamais supprimé) et le tracking MTM
    redémarre proprement à partir de maintenant, même discipline que
    FUNDING_BASIS_DISAGREEMENT V1->V2 (changement de méthodologie = nouveau
    segment, pas une réécriture silencieuse)."""
    p = state_path(portfolio_name)
    if not p.exists():
        return PortfolioState(cash=initial_cash, peak_equity=initial_cash, initialized=True)
    try:
        d = json.loads(p.read_text())
    except json.JSONDecodeError as e:
        # item P0.3 (phase OPERATIONAL HARDENING) : fichier corrompu -- ne
        # devrait plus jamais arriver après le passage à une écriture
        # atomique (save_state ci-dessous), mais un ANCIEN fichier pré-fix
        # ou une panne disque pourrait encore en laisser un. Ne JAMAIS
        # planter silencieusement le pipeline dessus : archiver (jamais
        # supprimer) et repartir propre, en loguant BRUYAMMENT -- perdre
        # l'historique d'un portefeuille est un événement économique
        # sérieux, pas un détail à masquer.
        print(f"[portfolio] ALERTE state.json corrompu pour {portfolio_name} ({e}) "
             f"-- archivé, redémarrage propre à partir de maintenant", flush=True)
        archive = p.parent / f"state_corrupt_archive_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}.json"
        p.rename(archive)
        return PortfolioState(cash=initial_cash, peak_equity=initial_cash, initialized=True)
    try:
        return PortfolioState(**d)
    except TypeError:
        archive = p.parent / "state_pre_mtm_archive.json"
        if not archive.exists():
            p.rename(archive)
        else:
            p.unlink()
        return PortfolioState(cash=initial_cash, peak_equity=initial_cash, initialized=True)


def save_state(portfolio_name: str, state: PortfolioState) -> None:
    """item P0.3 (phase OPERATIONAL HARDENING) : écrit dans un fichier .tmp
    PUIS renomme (os.replace, atomique sur POSIX) plutôt que d'écrire
    directement sur state.json. Un kill -9 (ou crash machine) pendant
    l'écriture directe aurait pu laisser un state.json tronqué -- JSON
    invalide, jamais rechargeable par load_state() au redémarrage. Avec le
    fichier temporaire, un crash pendant l'écriture laisse SOIT l'ancien
    state.json intact (si le crash arrive avant le rename), SOIT le nouveau
    complet (si après) -- jamais un état hybride/tronqué."""
    p = state_path(portfolio_name)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(asdict(state), indent=2, default=str))
    tmp.replace(p)


def _append_intent_ledger(portfolio_name: str, as_of: pd.Timestamp,
                          raw_by_instrument: Dict[str, List[dict]],
                          target: Dict[str, float], executed_delta: Dict[str, float]) -> None:
    rows = []
    # item P1.1 : même correctif que step() -- itérer un `set` directement
    # dépend du hash-seed du processus, trié explicitement pour un ordre de
    # lignes reproductible entre deux runs séparés.
    for instr in sorted(set(raw_by_instrument) | set(target) | set(executed_delta)):
        rows.append({
            "ts": as_of.isoformat(), "instrument": instr,
            "alpha_intents": json.dumps(raw_by_instrument.get(instr, [])),
            "portfolio_target": target.get(instr, 0.0),
            "executed_delta": executed_delta.get(instr, 0.0),
        })
    if not rows:
        return
    df = pd.DataFrame(rows)
    p = intent_ledger_path(portfolio_name)
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists():
        old = pd.read_parquet(p)
        df = pd.concat([old, df], ignore_index=True)
    # item P0.3 (atomicité restart) : même raisonnement que save_state() --
    # écrire dans un .tmp puis renommer, jamais un parquet tronqué illisible
    # si le processus est tué pendant l'écriture.
    tmp = p.with_suffix(p.suffix + ".tmp")
    df.to_parquet(tmp, index=False)
    tmp.replace(p)


def step(portfolio_name: str, config: PortfolioConfig, agg: AggregationResult,
        as_of: pd.Timestamp, execution_adapter=None) -> PortfolioState:
    """Un pas d'agrégation -> mark -> exécution shadow -> MTM -> équity.

    Idempotent au sens où rejouer le MÊME target sur un état déjà à jour ne
    produit aucun delta_quantity (donc aucun nouveau fill/coût) -- MAIS
    l'unrealized_pnl et donc l'équity SONT recalculés à chaque appel (le
    marché a bougé même sans nouveau trade -- c'est le point du MTM).

    item P0.2 (phase CLOSE THE EXECUTION LOOP) : `execution_adapter` est
    désormais le SEUL chemin qui transforme un delta demandé en changement
    de position -- plus d'appel direct à shadow_execute() ici. Un
    ShadowExecutionAdapter frais est construit par défaut si aucun n'est
    fourni (le bookkeeping durable est PortfolioState, pas l'adapter --
    voir execution_adapter.py). Le delta réellement appliqué à la position
    est `order.filled_quantity` (signé par `order.side`), PAS le delta
    demandé : un plafond de liquidité peut avoir partiellement rempli
    l'ordre (voir orders.py)."""
    if execution_adapter is None:
        from src.institutional.live_alpha_lab.execution_adapter import ShadowExecutionAdapter
        execution_adapter = ShadowExecutionAdapter()

    state = load_state(portfolio_name, config.capital_eur)
    target = agg.target_notional
    owner = agg.owner
    executed_delta: Dict[str, float] = {}
    any_stale_used = False
    skipped_no_mark = []

    # item P1.1 (déterminisme replay) : `set` a un ordre d'itération
    # dépendant du hash-seed du PROCESSUS (randomisé par défaut en Python
    # depuis 3.3) -- deux runs SÉPARÉS (deux process) sur des entrées
    # identiques pourraient itérer les instruments dans un ordre différent,
    # ce qui change la séquence des order_id/fill_id (leur suffixe dépend de
    # l'ordre de soumission) ET l'ordre de sommation flottante (gross/net/
    # unrealized_pnl, pas garanti bit-identique si l'ordre change). Trié
    # explicitement : même ordre garanti quel que soit le hash-seed.
    all_instruments = sorted(set(state.positions) | set(target))
    for instr in all_instruments:
        pos_d = state.positions.get(instr, asdict(Position(instrument=instr)))
        pos = Position(**pos_d)
        mark = get_mark(instr, as_of)
        if mark is None:
            # AUCUN prix disponible -- ne pas trader, ne pas halluciner un mark.
            # La position existante (si il y en a une) garde son dernier
            # unrealized_pnl connu jusqu'à ce qu'un mark redevienne dispo.
            skipped_no_mark.append(instr)
            state.positions[instr] = pos_d
            continue
        if mark.is_stale():
            any_stale_used = True

        # ══ item P0.3 : cible calculée -> cible ACCEPTÉE, via la bande ══
        raw_target_notional = target.get(instr, 0.0)
        accepted_before = state.accepted_target_notional.get(instr)
        sig_now = agg.intent_signature.get(instr)
        sig_before = state.accepted_intent_signature.get(instr)
        band = no_trade_band_fraction(agg.expected_edge.get(instr))
        current_notional = pos.quantity * mark.price

        gap = abs(raw_target_notional - (accepted_before if accepted_before is not None else 0.0))
        scale = max(abs(raw_target_notional),
                    abs(accepted_before) if accepted_before is not None else 0.0)
        crosses_band = gap >= band * scale if scale > 0 else False
        flips_sign = (accepted_before is not None and accepted_before != 0.0
                      and raw_target_notional != 0.0
                      and (raw_target_notional > 0) != (accepted_before > 0))

        # Ouverture, clôture et retournement ne sont JAMAIS filtrés. Les deux
        # premiers passent déjà la bande par construction (gap == scale, et
        # band <= 1) ; ils sont écrits explicitement pour que la propriété soit
        # lisible ici, pas seulement déductible de l'arithmétique.
        is_entry = accepted_before is None or (accepted_before == 0.0 and raw_target_notional != 0.0)
        is_exit = raw_target_notional == 0.0 and (
            accepted_before is None or accepted_before != 0.0 or pos.quantity != 0)

        # ⚠ Le cœur de la correction P0.3. Un changement d'EMPREINTE DE SIGNAL
        # sur CET instrument (nouvelle décision, décision expirée, conviction
        # révisée, changement de propriétaire au dedup) est un vrai changement
        # d'avis : on y répond toujours, sans bande. Une bande qui s'y
        # appliquerait ne serait pas prudente, elle laisserait sur le livre du
        # risque que plus aucun signal ne veut.
        #
        # A contrario, quand l'empreinte est INCHANGÉE, tout écart de cible
        # vient du rediviseur de budget (une entrée/sortie AILLEURS chez le
        # même alpha), d'un plafond, de l'overlay ou de la dérive de prix :
        # espérance nulle, coût positif. C'est là — et seulement là — que la
        # bande tranche. C'est exactement « une modification d'un signal ne
        # doit pas provoquer le resize de toutes les autres positions » : les
        # autres positions ont, elles, une empreinte inchangée.
        signal_changed = sig_now != sig_before
        accept_new_target = (is_entry or is_exit or flips_sign
                             or signal_changed or crosses_band)

        if not accept_new_target and abs(raw_target_notional - (accepted_before or 0.0)) > NUMERICAL_EPSILON_USD:
            # turnover que la bande vient d'éviter : mesuré, jamais ignoré.
            state.suppressed_turnover_usd += abs(raw_target_notional - (accepted_before or 0.0))
            state.suppressed_order_count += 1

        if accept_new_target:
            state.accepted_target_notional[instr] = raw_target_notional
            if sig_now is not None:
                state.accepted_intent_signature[instr] = sig_now
            elif instr in state.accepted_intent_signature:
                del state.accepted_intent_signature[instr]
            state.converging[instr] = True

        accepted_notional = state.accepted_target_notional.get(instr, 0.0)
        target_quantity = accepted_notional / mark.price if mark.price else 0.0
        delta_quantity = target_quantity - pos.quantity

        # Classe de turnover -- la réponse à « signal ou mécanique ? ».
        if is_exit:
            turnover_class = TURNOVER_EXIT
        elif flips_sign:
            turnover_class = TURNOVER_FLIP
        elif is_entry:
            turnover_class = TURNOVER_ENTRY
        elif accept_new_target:
            turnover_class = (TURNOVER_SIGNAL_RESIZE if signal_changed
                              else TURNOVER_MECHANICAL_RESIZE)
        else:
            turnover_class = TURNOVER_FILL_CONVERGENCE

        # Hors convergence d'un ordre inachevé, un écart résiduel à la cible
        # acceptée (typiquement la dérive de prix : la quantité cible bouge
        # quand le mark bouge, à notionnel constant) doit lui aussi franchir la
        # bande pour justifier un ordre.
        residual_ok = state.converging.get(instr, False) or (
            abs(delta_quantity * mark.price) >= band * max(abs(accepted_notional),
                                                           abs(current_notional)))

        if abs(delta_quantity * mark.price) >= NUMERICAL_EPSILON_USD and not residual_ok:
            state.suppressed_turnover_usd += abs(delta_quantity * mark.price)
            state.suppressed_order_count += 1
        elif abs(delta_quantity * mark.price) < NUMERICAL_EPSILON_USD:
            # Position exactement à la cible acceptée : la décision est
            # terminée. Sans cette ligne, un `converging=True` posé par une
            # acceptation dont le delta était déjà nul resterait vrai pour
            # toujours et la bande ne s'appliquerait plus jamais à cet
            # instrument (la dérive de prix repasserait librement).
            state.converging[instr] = False

        if abs(delta_quantity * mark.price) >= NUMERICAL_EPSILON_USD and residual_ok:
            decision_ts_iso = agg.owner_intent_ts.get(instr, as_of.isoformat())
            current_owner = owner.get(instr, pos.owner_alpha)
            intent_id = signal_id = f"{current_owner}:{instr}:{decision_ts_iso}"
            order, fill_record = execution_adapter.submit_order(
                portfolio_id=portfolio_name, alpha_id=current_owner,
                intent_id=intent_id, signal_id=signal_id, symbol=instr,
                delta_quantity=delta_quantity, as_of=as_of,
                timestamp_decision=decision_ts_iso, mark=mark,
            )
            if fill_record is not None:
                state.fills.append(asdict(fill_record))

            # exécuté RÉEL (post-plafond de liquidité) -- peut être < delta
            # demandé (fill partiel), jamais halluciné au-delà.
            executed_qty = order.filled_quantity if order.side == "BUY" else -order.filled_quantity
            executed_delta[instr] = executed_qty * (order.fill_price or 0.0)

            # item P0.3 : un ordre partiellement rempli laisse l'instrument en
            # convergence (on finira la décision au prochain step) ; un ordre
            # complet la termine, et la bande reprend la main.
            state.converging[instr] = (
                abs(order.filled_quantity - order.requested_quantity) > 1e-12)

            # Plafond de liquidité effectivement mordu sur CET ordre. Chiffré
            # au prix de fill quand il existe, au mark sinon (un ordre plafonné
            # à zéro n'a pas de fill_price -- utiliser 0 le rendrait invisible
            # dans le notionnel refusé, ce qui est exactement le contraire du
            # but).
            unfilled = order.requested_quantity - order.filled_quantity
            if unfilled > 1e-12:
                state.capped_order_count += 1
                px = order.fill_price or order.mark_price_at_decision or 0.0
                state.capped_notional_usd += unfilled * px

            if abs(executed_qty) > 1e-12:
                state.cumulative_fees_usd += order.fee_amount
                turn = abs(executed_qty * order.fill_price)
                state.cumulative_turnover_usd += turn
                state.cumulative_turnover_by_class[turnover_class] = (
                    state.cumulative_turnover_by_class.get(turnover_class, 0.0) + turn)
                pos.fees_paid += order.fee_amount

                same_sign_or_flat = (pos.quantity == 0) or (
                    (pos.quantity > 0) == (executed_qty > 0))
                if same_sign_or_flat:
                    # ouverture ou renforcement -> moyenne pondérée du prix d'entrée
                    new_qty = pos.quantity + executed_qty
                    if new_qty != 0:
                        pos.entry_price = (
                            (pos.quantity * pos.entry_price + executed_qty * order.fill_price) / new_qty
                        )
                    pos.quantity = new_qty
                else:
                    # item P0.4 : c'est une réduction/clôture -- déterminer
                    # EXIT_REASON avant de persister l'ordre. ALPHA_HORIZON_
                    # EXPIRY seulement si PLUS AUCUN intent vivant ne visait
                    # cet instrument (tous ceux qui le visaient ont expiré) ;
                    # sinon TARGET_CHANGE (screen/cap/dedup/signal inversé --
                    # catch-all honnête, pas une fausse précision).
                    order.exit_reason = (
                        "ALPHA_HORIZON_EXPIRY" if instr in agg.expired_driven_instruments
                        else "TARGET_CHANGE"
                    )
                    closing_qty = min(abs(executed_qty), abs(pos.quantity))
                    sign_closed = 1.0 if pos.quantity > 0 else -1.0
                    just_realized = closing_qty * sign_closed * (order.fill_price - pos.entry_price)
                    pos.realized_pnl += just_realized
                    state.cumulative_realized_pnl += just_realized
                    # item 5 : PnL réalisé crédité à l'owner qui détenait la
                    # position AU MOMENT de la clôture (pas au nouvel owner
                    # éventuel si l'instrument change de mains le même step).
                    state.cumulative_pnl_by_alpha[pos.owner_alpha] = (
                        state.cumulative_pnl_by_alpha.get(pos.owner_alpha, 0.0) + just_realized)
                    pos.quantity += executed_qty
                    if (pos.quantity > 0) != (sign_closed > 0) and pos.quantity != 0:
                        # on a traversé zéro -> nouvelle position ouverte au fill_price
                        pos.entry_price = order.fill_price

                pos.owner_alpha = current_owner
                state.cumulative_cost_by_alpha[current_owner] = (
                    state.cumulative_cost_by_alpha.get(current_owner, 0.0) + order.fee_amount)

            # persisté APRES la détermination d'exit_reason ci-dessus (item P0.4)
            order_d = asdict(order)
            order_d["turnover_class"] = turnover_class      # item P0.3
            order_d["no_trade_band_fraction"] = band
            state.orders.append(order_d)

        # funding (perp uniquement, pas les contrats _QUARTERLY) : accrual
        # proportionnel au temps écoulé depuis le dernier step, APPROXIMATION
        # documentée (pas un accrual continu réel toutes les 8h pile).
        if not instr.endswith("_QUARTERLY") and pos.quantity != 0 and state.last_step_ts:
            funding_rate = _latest_funding_rate(instr.replace("_PERP", ""), as_of)
            if funding_rate is not None:
                elapsed_h = (as_of - pd.Timestamp(state.last_step_ts)).total_seconds() / 3600.0
                frac_of_interval = min(elapsed_h / FUNDING_INTERVAL_HOURS, 1.0)
                funding_pnl = -pos.quantity * mark.price * funding_rate * frac_of_interval
                pos.funding_paid += funding_pnl
                state.cumulative_funding_usd += funding_pnl

        pos.instrument = instr
        state.positions[instr] = asdict(pos)

    # positions résiduelles nulles nettoyées (mais gardées si un mark a manqué ce step)
    state.positions = {
        k: v for k, v in state.positions.items()
        if abs(v["quantity"]) > 1e-9 or k in skipped_no_mark
    }
    # item P0.3 : un instrument sans position ET sans cible n'a plus d'état de
    # bande à garder -- sinon `accepted_target_notional` grossirait sans fin et
    # une réouverture future serait jugée contre une cible périmée.
    for aux in (state.accepted_target_notional, state.accepted_intent_signature,
                state.converging):
        for k in [k for k in aux if k not in state.positions and not target.get(k)]:
            del aux[k]

    realized_pnl = state.cumulative_realized_pnl
    unrealized_pnl = 0.0
    gross = net = 0.0
    # item 5 : ALPHA_ATTRIBUTED_PNL non-réalisé recalculé ENTIÈREMENT à
    # chaque step à partir des positions ouvertes (contrairement au réalisé,
    # qui est un cumul -- l'unrealized n'a pas de mémoire, il reflète l'état
    # marché COURANT uniquement).
    unrealized_by_alpha: Dict[str, float] = defaultdict(float)
    for instr, p in state.positions.items():
        if p["quantity"] == 0:
            continue
        mark = get_mark(instr, as_of)
        if mark is None:
            continue
        upnl = Position(**p).unrealized_pnl(mark.price)
        unrealized_pnl += upnl
        unrealized_by_alpha[p.get("owner_alpha", "UNATTRIBUTED")] += upnl
        notional_now = p["quantity"] * mark.price
        gross += abs(notional_now)
        net += notional_now

    equity = (state.cash + realized_pnl + unrealized_pnl
             - state.cumulative_fees_usd + state.cumulative_funding_usd)
    state.peak_equity = max(state.peak_equity, equity)
    drawdown = (equity - state.peak_equity) / state.peak_equity if state.peak_equity else 0.0

    pnl_by_alpha = {
        a: state.cumulative_pnl_by_alpha.get(a, 0.0) + unrealized_by_alpha.get(a, 0.0)
        for a in set(state.cumulative_pnl_by_alpha) | set(unrealized_by_alpha)
    }

    status = "DEGRADED" if (any_stale_used or skipped_no_mark) else "OK"
    state.equity_curve.append({
        "ts": as_of.isoformat(), "status": status,
        "cash": state.cash, "realized_pnl": realized_pnl, "unrealized_pnl": unrealized_pnl,
        "pnl_by_alpha": dict(pnl_by_alpha),
        "fees": state.cumulative_fees_usd, "funding": state.cumulative_funding_usd,
        "equity": equity, "drawdown": drawdown,
        "gross_exposure": gross, "net_exposure": net, "n_positions": len(state.positions),
        "skipped_no_mark": skipped_no_mark,
        "sizing_rule": SIZING_RULE,   # 2026-09-03 : frontière de segment, points antérieurs non réécrits
        # item P0.3 : frontière du segment « bande de non-négociation »
        "no_trade_band_rule": NO_TRADE_BAND_RULE,
        "turnover_by_class": dict(state.cumulative_turnover_by_class),
        "suppressed_turnover_usd": state.suppressed_turnover_usd,
        "suppressed_order_count": state.suppressed_order_count,
    })
    state.alpha_denominator_high_water = dict(agg.denominator_high_water)
    # item A3 : trace de la MORSURE de l'overlay, pas seulement de sa valeur
    # courante. Un portefeuille dont l'overlay n'a jamais mordu doit pouvoir
    # être signalé comme tel (OVERLAY_NEVER_BINDING) plutôt que rapporté comme
    # une variante indépendante -- sinon on croit comparer trois hypothèses
    # alors qu'on en teste une seule en trois exemplaires.
    if config.apply_vol_overlay:
        mult = float(agg.vol_overlay_multiplier_applied)
        state.overlay_steps += 1
        state.overlay_multiplier_sum += mult
        if mult < 1.0 - 1e-12:
            state.overlay_binding_steps += 1
            state.overlay_multiplier_min = min(state.overlay_multiplier_min, mult)
    state.last_step_ts = as_of.isoformat()
    _append_intent_ledger(portfolio_name, as_of, agg.raw_intents_by_instrument, target, executed_delta)
    save_state(portfolio_name, state)
    return state


def _latest_funding_rate(symbol: str, as_of: pd.Timestamp) -> Optional[float]:
    from src.institutional.live_alpha_lab.marks import _oi_base, eligible_files_for_as_of
    files = eligible_files_for_as_of(_oi_base(symbol), as_of)
    if not files:
        return None
    frames = []
    for f in files:
        try:
            frames.append(pd.read_parquet(f, columns=["timestamp", "funding_rate"]))
        except Exception:
            continue
    if not frames:
        return None
    df = pd.concat(frames, ignore_index=True)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df[df["timestamp"] <= as_of].sort_values("timestamp")
    if df.empty:
        return None
    return float(df.iloc[-1]["funding_rate"])
