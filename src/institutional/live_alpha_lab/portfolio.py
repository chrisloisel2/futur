"""
src/institutional/live_alpha_lab/portfolio.py
─────────────────────────────────────────────────────────────────────────────
PORTFOLIO_SHADOW_LAYER — agrège les PortfolioIntent de tous les alphas
FORWARD_LIVE, déduplique le risque corrélé, applique les budgets par
famille/risk_bucket, limite l'exposition gross/net et par actif, calcule les
delta_position réels, et les envoie à ShadowExecutionAdapter (coûts simulés,
AUCUN ordre réel).

⚠ LIMITE HONNÊTE ASSUMÉE : pas de mark-to-market. Ce module trace le coût
réalisé (frais sur le turnover) et l'exposition au coût d'entrée (notional),
PAS un PnL non réalisé mark-to-market -- ça demanderait un flux de prix live
unifié pour tous les instruments (majors + alts + spreads calendar), qui
n'existe pas encore de façon homogène dans ce repo. gross_pnl/net_pnl
reportés ici = -frais cumulés (le seul PnL réellement mesuré à ce stade).
Documenté explicitement plutôt que de fabriquer un flux de prix.
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
from src.institutional.live_alpha_lab.portfolio_config import PortfolioConfig

ROOT = Path(__file__).resolve().parents[3]
PORTFOLIO_DIR = ROOT / "reports" / "live_alpha_lab" / "portfolios"

# Coûts : mêmes constantes que le reste du projet (src/institutional/
# execution/execution_simulator.py::ExecutionConfig par défaut), appliquées
# directement au NOTIONAL (pas de conversion taille/prix -- le portfolio
# layer raisonne en dollars, pas en unités d'actif ; voir docstring module).
TAKER_FEE_BPS = 5.0
FIXED_SLIPPAGE_BPS = 2.0


@dataclass
class ShadowFill:
    instrument: str
    delta_notional: float
    fee_usd: float
    slippage_usd: float

    @property
    def total_cost_usd(self) -> float:
        return self.fee_usd + self.slippage_usd


def shadow_execute(delta_notional: float, instrument: str) -> ShadowFill:
    """AUCUN ordre réel. Coût simulé sur |delta| uniquement (jamais sur la
    position brute -- section 10 de la mission)."""
    notional = abs(delta_notional)
    fee = notional * TAKER_FEE_BPS / 10_000
    slippage = notional * FIXED_SLIPPAGE_BPS / 10_000
    return ShadowFill(instrument=instrument, delta_notional=delta_notional,
                      fee_usd=fee, slippage_usd=slippage)


def _dedup_correlated(intents: List[PortfolioIntent],
                      max_dominant_per_family: Optional[int]) -> List[PortfolioIntent]:
    """Ne jamais stacker deux intents corrélés (même correlation_family, même
    instrument, même direction) au même moment : garde le MAX
    target_position_fraction, pas la somme. Si max_dominant_per_family=1
    (P2_DIVERSIFIED), garde uniquement l'alpha le plus confiant du cluster
    ENTIER (pas juste sur cet instrument) à cet instant."""
    if max_dominant_per_family:
        # un seul alpha "actif" par correlation_family à un instant donné,
        # celui avec la plus haute confidence moyenne sur ses intents courants
        by_family: Dict[str, List[PortfolioIntent]] = defaultdict(list)
        for it in intents:
            by_family[it.correlation_family].append(it)
        kept = []
        for fam, group in by_family.items():
            by_alpha_conf: Dict[str, float] = defaultdict(list)
            for it in group:
                by_alpha_conf[it.alpha_id] = by_alpha_conf.get(it.alpha_id, []) + [it.confidence]
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


def aggregate(intents: List[PortfolioIntent], config: PortfolioConfig,
             screened_symbols: set, vol_overlay_multiplier: float = 1.0
             ) -> tuple:
    """Retourne (target_notional_by_instrument, owner_by_instrument).

    owner_by_instrument attribue chaque instrument à l'alpha_id "gagnant" du
    dedup corrélé sur cet instrument -- utilisé ensuite pour l'attribution
    des coûts/PnL par alpha. Simplification assumée : si deux alphas de
    correlation_family DIFFÉRENTES visaient un jour le même instrument (rare
    avec le catalogue actuel, aucun cas au 2026-08-31), le dernier écrit
    gagne l'attribution -- documenté, pas un cas géré finement pour l'instant."""
    intents = _dedup_correlated(intents, config.max_dominant_per_correlation_family)

    n_alphas_per_bucket: Dict[str, set] = defaultdict(set)
    for it in intents:
        n_alphas_per_bucket[it.risk_bucket].add(it.alpha_id)

    target: Dict[str, float] = defaultdict(float)
    owner: Dict[str, str] = {}
    for it in intents:
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
            # spread : jambe A dans le sens `direction`, jambe B opposée, même magnitude
            target[it.instrument] += sign * notional
            target[it.leg_instrument_b] += -sign * notional
            owner[it.instrument] = it.alpha_id
            owner[it.leg_instrument_b] = it.alpha_id
        else:
            target[it.instrument] += sign * notional
            owner[it.instrument] = it.alpha_id

    # limite par actif
    cap_asset = config.capital_eur * config.max_per_asset_fraction
    for instr, notional in list(target.items()):
        if abs(notional) > cap_asset:
            target[instr] = cap_asset * (1 if notional > 0 else -1)

    # limite gross (implique aussi une borne sur le net)
    gross = sum(abs(v) for v in target.values())
    cap_gross = config.capital_eur * config.max_gross_exposure_fraction
    if gross > cap_gross and gross > 0:
        scale = cap_gross / gross
        target = {k: v * scale for k, v in target.items()}

    return dict(target), owner


@dataclass
class PortfolioState:
    positions: Dict[str, float] = field(default_factory=dict)   # instrument -> notional
    cumulative_fees_usd: float = 0.0
    cumulative_slippage_usd: float = 0.0
    cumulative_turnover_usd: float = 0.0
    cumulative_cost_by_alpha: Dict[str, float] = field(default_factory=dict)
    equity_curve: List[dict] = field(default_factory=list)      # [{ts, equity, gross, net}]
    last_processed_intent_key: Optional[str] = None


def state_path(portfolio_name: str) -> Path:
    return PORTFOLIO_DIR / portfolio_name / "state.json"


def load_state(portfolio_name: str) -> PortfolioState:
    p = state_path(portfolio_name)
    if not p.exists():
        return PortfolioState()
    d = json.loads(p.read_text())
    return PortfolioState(**d)


def save_state(portfolio_name: str, state: PortfolioState) -> None:
    p = state_path(portfolio_name)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(asdict(state), indent=2, default=str))


def step(portfolio_name: str, config: PortfolioConfig, target_notional: Dict[str, float],
        as_of: pd.Timestamp, owner_by_instrument: Optional[Dict[str, str]] = None) -> PortfolioState:
    """Un pas d'agrégation -> exécution shadow -> équity. Idempotent au sens
    où rejouer le MÊME target_notional sur un état déjà à jour ne produit
    aucun delta (delta = target - previous_position = 0)."""
    state = load_state(portfolio_name)
    owner_by_instrument = owner_by_instrument or {}
    total_fees = total_slippage = total_turnover = 0.0

    all_instruments = set(state.positions) | set(target_notional)
    for instr in all_instruments:
        prev = state.positions.get(instr, 0.0)
        tgt = target_notional.get(instr, 0.0)
        delta = tgt - prev
        if abs(delta) < 1e-9:
            continue
        fill = shadow_execute(delta, instr)
        total_fees += fill.fee_usd
        total_slippage += fill.slippage_usd
        total_turnover += abs(delta)
        state.positions[instr] = tgt

        owner = owner_by_instrument.get(instr, "UNATTRIBUTED")
        state.cumulative_cost_by_alpha[owner] = (
            state.cumulative_cost_by_alpha.get(owner, 0.0) + fill.total_cost_usd)

    # positions résiduelles nulles nettoyées
    state.positions = {k: v for k, v in state.positions.items() if abs(v) > 1e-9}

    state.cumulative_fees_usd += total_fees
    state.cumulative_slippage_usd += total_slippage
    state.cumulative_turnover_usd += total_turnover

    gross = sum(abs(v) for v in state.positions.values())
    net = sum(state.positions.values())
    # equity = capital - couts cumulés (pas de mark-to-market, voir docstring module)
    equity = config.capital_eur - state.cumulative_fees_usd - state.cumulative_slippage_usd
    state.equity_curve.append({
        "ts": as_of.isoformat(), "equity": equity, "gross_exposure": gross,
        "net_exposure": net, "n_positions": len(state.positions),
        "cumulative_fees_usd": state.cumulative_fees_usd,
        "cumulative_slippage_usd": state.cumulative_slippage_usd,
        "cumulative_turnover_usd": state.cumulative_turnover_usd,
    })
    save_state(portfolio_name, state)
    return state
