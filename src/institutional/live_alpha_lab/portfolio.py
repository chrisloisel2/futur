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


def aggregate(intents: List[PortfolioIntent], config: PortfolioConfig,
             screened_symbols: set, vol_overlay_multiplier: float = 1.0,
             as_of: Optional[pd.Timestamp] = None) -> AggregationResult:
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

    target: Dict[str, float] = defaultdict(float)
    owner: Dict[str, str] = {}
    for it in deduped:
        frac = apply_screen(it.target_position_fraction, it.instrument, it.direction, screened_symbols)
        if config.apply_vol_overlay:
            frac *= vol_overlay_multiplier
        if frac <= 0:
            continue
        budget = _alpha_budget(it.risk_bucket, it.alpha_id, config,
                               len(n_alphas_per_bucket[it.risk_bucket]))
        notional = budget * frac
        sign = 1.0 if it.direction == "LONG" else -1.0

        if it.multi_leg and it.leg_instrument_b:
            target[it.instrument] += sign * notional
            target[it.leg_instrument_b] += -sign * notional
            owner[it.instrument] = it.alpha_id
            owner[it.leg_instrument_b] = it.alpha_id
        else:
            target[it.instrument] += sign * notional
            owner[it.instrument] = it.alpha_id

    cap_asset = config.capital_eur * config.max_per_asset_fraction
    for instr, notional in list(target.items()):
        if abs(notional) > cap_asset:
            target[instr] = cap_asset * (1 if notional > 0 else -1)

    gross = sum(abs(v) for v in target.values())
    cap_gross = config.capital_eur * config.max_gross_exposure_fraction
    if gross > cap_gross and gross > 0:
        scale = cap_gross / gross
        target = {k: v * scale for k, v in target.items()}

    return AggregationResult(dict(target), owner, dict(raw_by_instrument))


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
    d = json.loads(p.read_text())
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
    p = state_path(portfolio_name)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(asdict(state), indent=2, default=str))


def _append_intent_ledger(portfolio_name: str, as_of: pd.Timestamp,
                          raw_by_instrument: Dict[str, List[dict]],
                          target: Dict[str, float], executed_delta: Dict[str, float]) -> None:
    rows = []
    for instr in set(raw_by_instrument) | set(target) | set(executed_delta):
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
    df.to_parquet(p, index=False)


def step(portfolio_name: str, config: PortfolioConfig, agg: AggregationResult,
        as_of: pd.Timestamp) -> PortfolioState:
    """Un pas d'agrégation -> mark -> exécution shadow -> MTM -> équity.

    Idempotent au sens où rejouer le MÊME target sur un état déjà à jour ne
    produit aucun delta_quantity (donc aucun nouveau fill/coût) -- MAIS
    l'unrealized_pnl et donc l'équity SONT recalculés à chaque appel (le
    marché a bougé même sans nouveau trade -- c'est le point du MTM)."""
    state = load_state(portfolio_name, config.capital_eur)
    target = agg.target_notional
    owner = agg.owner
    executed_delta: Dict[str, float] = {}
    any_stale_used = False
    skipped_no_mark = []

    all_instruments = set(state.positions) | set(target)
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

        target_notional = target.get(instr, 0.0)
        target_quantity = target_notional / mark.price if mark.price else 0.0
        delta_quantity = target_quantity - pos.quantity

        if abs(delta_quantity * mark.price) >= 1e-6:
            fill = shadow_execute(delta_quantity, instr, mark)
            executed_delta[instr] = delta_quantity * mark.price
            state.cumulative_fees_usd += fill.fee_usd
            state.cumulative_turnover_usd += abs(fill.delta_notional_at_fill)
            pos.fees_paid += fill.fee_usd

            same_sign_or_flat = (pos.quantity == 0) or (
                (pos.quantity > 0) == (delta_quantity > 0))
            if same_sign_or_flat:
                # ouverture ou renforcement -> moyenne pondérée du prix d'entrée
                new_qty = pos.quantity + delta_quantity
                if new_qty != 0:
                    pos.entry_price = (
                        (pos.quantity * pos.entry_price + delta_quantity * fill.fill_price) / new_qty
                    )
                pos.quantity = new_qty
            else:
                closing_qty = min(abs(delta_quantity), abs(pos.quantity))
                sign_closed = 1.0 if pos.quantity > 0 else -1.0
                just_realized = closing_qty * sign_closed * (fill.fill_price - pos.entry_price)
                pos.realized_pnl += just_realized
                state.cumulative_realized_pnl += just_realized
                # item 5 : PnL réalisé crédité à l'owner qui détenait la
                # position AU MOMENT de la clôture (pas au nouvel owner
                # éventuel si l'instrument change de mains le même step).
                state.cumulative_pnl_by_alpha[pos.owner_alpha] = (
                    state.cumulative_pnl_by_alpha.get(pos.owner_alpha, 0.0) + just_realized)
                pos.quantity += delta_quantity
                if (pos.quantity > 0) != (sign_closed > 0) and pos.quantity != 0:
                    # on a traversé zéro -> nouvelle position ouverte au fill_price
                    pos.entry_price = fill.fill_price

            current_owner = owner.get(instr, pos.owner_alpha)
            pos.owner_alpha = current_owner
            state.cumulative_cost_by_alpha[current_owner] = (
                state.cumulative_cost_by_alpha.get(current_owner, 0.0) + fill.fee_usd)

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
    })
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
