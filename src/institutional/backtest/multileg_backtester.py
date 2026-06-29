"""
src/institutional/backtest/multileg_backtester.py
─────────────────────────────────────────────────────────────────────────────
Backtester PORTEFEUILLE MULTI-JAMBES (Phase 37).

Extension COMPTABLE (pas un ajout de signaux) : LONG_SPOT + DELTA_NEUTRAL_CARRY
+ PORTFOLIO_HEDGE, avec PnL décomposé (price / funding / hedge / costs), funding
accrual horaire, carry gaté par régime de funding, hedge lié au book long,
invariants no-naked-short. Ordre d'événements strict, sans lookahead.

Carry delta-neutral : spot long + perp short, MÊME série de prix → les price_pnl
des deux jambes s'annulent ; le PnL vient du FUNDING (basis omis, documenté).
"""
from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from src.institutional.engines.base import AlphaEngine
from src.institutional.engines.legacy_bridge import load_enriched
from src.institutional.engines.carry_basis.funding_gate import classify_funding_regime, CARRY_OK, FundingGateConfig
from src.institutional.portfolio.position import PositionLeg, PortfolioPosition
from src.institutional.portfolio.funding import accrue_funding_leg, is_funding_hour
from src.institutional.portfolio.invariants import (
    InvariantLimits, check_portfolio_invariants,
)
from src.institutional.risk.hedge_governor import HedgeGovernorV1, HedgeConfig
from src.institutional.portfolio.regime_gate import RegimeGate, RegimeGateConfig
from src.institutional.portfolio.regime_exit import should_exit_long_on_regime_flip
from src.institutional.risk.intra_position_governor import decide_position_risk, IntraGovernorConfig
from src.institutional.risk.correlation_buckets import bucket_of as _bucket_of, is_meme as _is_meme

logger = logging.getLogger(__name__)


@dataclass
class MultiLegConfig:
    initial_capital: float = 10_000.0
    taker_fee_bps: float = 5.0
    slippage_bps: float = 2.0
    maker_fee_bps: float = 1.0          # carry en maker
    long_fraction: float = 0.10
    carry_fraction: float = 0.20        # par asset carry (notional spot)
    max_open_longs: int = 3
    max_long_entries_per_bar: int = 1   # limite la rotation (anti-churn)
    # réparations long book (Phase 3)
    long_min_er_cost_mult: float = 0.0  # rejet si expected_return < mult × coût (0=off, 3=filtre)
    long_universe: Optional[List[str]] = None  # restreint l'univers long (ex. BTC/ETH/SOL)
    borrow_bps_per_year: float = 1.0
    # ranker d'opportunités (univers élargi : sélectionner les MEILLEURS, pas tout exécuter)
    enable_ranker: bool = False          # caps corrélation/meme/alt sur la sélection long
    ranker_max_per_bucket: int = 2       # max positions par bucket de corrélation
    ranker_max_meme: int = 1             # max positions meme
    ranker_max_alt: int = 5              # max positions alt (hors BTC/ETH)
    # gate edge réalisé par-actif (causal) : ne trader un alt que s'il a prouvé un edge net>0 avant
    enable_asset_edge_gate: bool = False
    asset_edge_min_net: float = 0.0      # PnL net moyen/signal requis sur les folds précédents
    asset_edge_min_signals: int = 20     # échantillon prior minimal (sinon non tradable)
    # toggles d'ablation
    enable_long: bool = True
    enable_carry: bool = False
    carry_gate_v2: bool = False          # gate carry cross-exchange (entry+exit) — churn ⚠
    carry_gate_v2_size_only: bool = False  # old sticky gate entry/exit + v2 réduit la TAILLE seulement
    enable_hedge: bool = False
    enable_regime_gate: bool = False    # bloque les longs hors BULL/RECOVERY (macro BTC, causal)
    enable_asset_regime_gate: bool = False  # gate PAR-ASSET (BTC macro AND régime asset) — Phase 47
    enable_regime_flip_exit: bool = False   # ferme les longs quand régime quitte BULL/RECOVERY
    enable_intra_position_governor: bool = False  # close-only DD governor intra-position
    intra_governor_config: IntraGovernorConfig = field(default_factory=IntraGovernorConfig)
    limits: InvariantLimits = field(default_factory=InvariantLimits)
    hedge_config: HedgeConfig = field(default_factory=HedgeConfig)
    funding_gate_config: FundingGateConfig = field(default_factory=FundingGateConfig)

    @property
    def long_roundtrip(self) -> float:
        return 2 * (self.taker_fee_bps + self.slippage_bps) / 1e4

    @property
    def carry_leg_cost(self) -> float:  # une jambe, un sens (maker)
        return (self.maker_fee_bps + self.slippage_bps) / 1e4


@dataclass
class MultiLegResult:
    equity: pd.Series
    portfolio_ledger: pd.DataFrame
    leg_ledger: pd.DataFrame
    pnl_by_type: Dict[str, float]
    metrics: Dict
    config: MultiLegConfig

    def summary(self) -> str:
        m = self.metrics
        return (f"Equity {self.equity.iloc[0]:.0f}→{self.equity.iloc[-1]:.0f} "
                f"({(self.equity.iloc[-1]/self.equity.iloc[0]-1)*100:+.1f}%) | "
                f"PF={m.get('pf',0):.2f} maxDD={m.get('max_drawdown',0)*100:.1f}% "
                f"ret/mo_med={m.get('roi_month_median',0)*100:+.2f}% | "
                f"PnL[dir={self.pnl_by_type.get('directional',0):.0f} "
                f"carry={self.pnl_by_type.get('carry_funding',0):.0f} "
                f"hedge={self.pnl_by_type.get('hedge',0):.0f} "
                f"fees={self.pnl_by_type.get('fees',0):.0f}]")


class MultiLegBacktester:
    def __init__(self, long_engines: List[AlphaEngine], config: Optional[MultiLegConfig] = None,
                 carry_assets: Optional[List[str]] = None,
                 hedge_governor: Optional[HedgeGovernorV1] = None):
        self.long_engines = long_engines or []
        self.config = config or MultiLegConfig()
        self.carry_assets = carry_assets or ["BTCUSDT", "ETHUSDT"]
        self.hedge_gov = hedge_governor or HedgeGovernorV1(self.config.hedge_config)
        self._pid = 0
        self._lid = 0

    def _new_pid(self, tag: str) -> str:
        self._pid += 1
        return f"{tag}_{self._pid}"

    def _new_lid(self) -> str:
        self._lid += 1
        return f"leg_{self._lid}"

    # ── données ──────────────────────────────────────────────────────────────
    def _load(self, assets: List[str], start: str, end: str):
        prices, funding = {}, {}
        for a in assets:
            df = load_enriched(a, required_cols=["close", "funding_rate"], start=start, end=end)
            if df is None or df.empty:
                continue
            df = df.set_index("datetime").sort_index()
            prices[a] = df["close"]
            if "funding_rate" in df.columns:
                funding[a] = df["funding_rate"]
        return prices, funding

    def _long_opps(self, start, end):
        by_ts = {}
        if not self.config.enable_long:
            return by_ts
        for eng in self.long_engines:
            for opp in eng.generate_all(start, end):
                if opp.decision_zone == "A_TRADE" and opp.direction == "LONG":
                    by_ts.setdefault(pd.Timestamp(opp.timestamp), []).append(opp)
        return by_ts

    # ── run ──────────────────────────────────────────────────────────────────
    def run(self, start: str, end: str) -> MultiLegResult:
        cfg = self.config
        assets = sorted(set([a for e in self.long_engines for a in e.assets]) | set(self.carry_assets)
                        | {"BTCUSDT"})
        prices, funding = self._load(assets, start, end)
        if not prices:
            raise ValueError("no prices")
        grid = sorted(set().union(*[set(s.index) for s in prices.values()]))
        grid = [t for t in grid if pd.Timestamp(start, tz="UTC") <= t <= pd.Timestamp(end, tz="UTC")]
        long_opps = self._long_opps(start, end)

        # RegimeGate causal (série régime BTC calculée sur F_t)
        regime_gate = None
        if cfg.enable_regime_gate and "BTCUSDT" in prices:
            regime_gate = RegimeGate()
            regime_gate.compute_regime_series(prices["BTCUSDT"])
        # Asset Regime Gate (Phase 47) : BTC macro AND régime par actif
        asset_gate = None
        if cfg.enable_asset_regime_gate:
            from src.institutional.portfolio.asset_regime_gate import AssetRegimeGate
            asset_gate = AssetRegimeGate().fit(prices)
        # Asset Edge Gate (causal) : ne trader un alt que s'il a prouvé un edge net>0 avant
        edge_gate = None
        if cfg.enable_asset_edge_gate:
            from src.institutional.portfolio.asset_edge_gate import AssetEdgeGate
            flat = [o for opps in long_opps.values() for o in opps]
            edge_gate = AssetEdgeGate(min_net=cfg.asset_edge_min_net,
                                      min_signals=cfg.asset_edge_min_signals).fit(
                flat, prices, cfg.long_roundtrip)
        carry_gate = None
        carry_size_gate = None
        if cfg.carry_gate_v2 or cfg.carry_gate_v2_size_only:
            from src.institutional.engines.carry_basis.carry_gate_v2 import CarryGateV2, CarryGateV2Status
            cg = CarryGateV2(self.carry_assets)
            self._CG_BLOCK = CarryGateV2Status.BLOCK
            if cfg.carry_gate_v2_size_only:
                carry_size_gate = cg     # old gate entry/exit + v2 size mult
            else:
                carry_gate = cg          # v2 entry+exit

        cash = cfg.initial_capital
        positions: List[PortfolioPosition] = []
        cooldown: Dict[str, pd.Timestamp] = {}      # asset → until (long)
        carry_open: Dict[str, PortfolioPosition] = {}
        hedge_pos: Optional[PortfolioPosition] = None
        peak = cfg.initial_capital
        eq_window = deque(maxlen=720)   # ~30j : DD GLISSANT pour le governor (≠ ratchet all-time)
        eq_curve, port_rows, leg_rows = [], [], []
        pnl_acc = {"directional": 0.0, "carry_funding": 0.0, "hedge": 0.0,
                   "fees": 0.0, "borrow": 0.0}

        def px(a, t):
            s = prices.get(a)
            if s is None:
                return None
            i = s.index.searchsorted(t, side="right") - 1
            return float(s.iloc[i]) if i >= 0 else None

        def fr(a, t):
            s = funding.get(a)
            if s is None:
                return 0.0
            i = s.index.searchsorted(t, side="right") - 1
            v = float(s.iloc[i]) if i >= 0 else 0.0
            return v if np.isfinite(v) else 0.0

        def open_long_legs():
            return [l for p in positions if p.is_open and p.position_type == "DIRECTIONAL_LONG"
                    for l in p.legs if l.is_open]

        for t in grid:
            # 1-2. mark-to-market
            for p in positions:
                if not p.is_open:
                    continue
                for l in p.legs:
                    if l.is_open:
                        mp = px(l.asset, t)
                        if mp:
                            l.mark_price = mp

            # 3. funding accrual (heures de funding)
            if is_funding_hour(t):
                for p in positions:
                    if not p.is_open:
                        continue
                    for l in p.legs:
                        if l.is_open and l.leg_type in ("CARRY_SHORT_PERP", "SHORT_HEDGE"):
                            fpnl = accrue_funding_leg(l, fr(l.asset, t), l.mark_price)
                            cash += fpnl
                            key = "carry_funding" if l.leg_type == "CARRY_SHORT_PERP" else "hedge"
                            pnl_acc[key] += fpnl
                # borrow cost on carry spot legs (per funding period)
                bper = cfg.borrow_bps_per_year / 1e4 / (365 * 3)
                for p in positions:
                    if p.is_open and p.position_type == "DELTA_NEUTRAL_CARRY":
                        for l in p.legs:
                            if l.is_open and l.leg_type == "CARRY_LONG_SPOT":
                                c = bper * l.qty * (l.mark_price or l.entry_price)
                                cash -= c; pnl_acc["borrow"] -= c

            # 4. exits
            def close_leg(l: PositionLeg, t, side_cost: float):
                nonlocal cash
                l.is_open = False; l.exit_time = str(t); l.exit_price = l.mark_price
                realized = l.price_pnl()
                cost = side_cost * l.qty * (l.mark_price or l.entry_price)
                l.fees_exit += cost
                cash += realized - cost
                pnl_acc["fees"] -= cost
                if l.leg_type in ("LONG_SPOT",):
                    pnl_acc["directional"] += realized
                elif l.leg_type == "SHORT_HEDGE":
                    pnl_acc["hedge"] += realized
                # carry legs: price pnl ~ cancels; still attribute to carry
                elif l.leg_type in ("CARRY_LONG_SPOT", "CARRY_SHORT_PERP"):
                    pnl_acc["carry_funding"] += realized

            # 4a. long exits (horizon/stop/tp)
            for p in positions:
                if not p.is_open or p.position_type != "DIRECTIONAL_LONG":
                    continue
                for l in p.legs:
                    if not l.is_open:
                        continue
                    held = (t - pd.Timestamp(l.entry_time)) / pd.Timedelta(hours=1)
                    mp = l.mark_price or l.entry_price
                    stop = l.entry_price * 0.975
                    tp = l.entry_price * 1.04
                    if mp <= stop or mp >= tp or held >= p.legs[0].__dict__.get("_horizon", 8):
                        close_leg(l, t, cfg.taker_fee_bps / 1e4 + cfg.slippage_bps / 1e4)
                        cooldown[l.asset] = t + pd.Timedelta(hours=8)
                if all(not l.is_open for l in p.legs):
                    p.is_open = False

            # 4b. carry exits (funding gate flip)
            for a in list(carry_open.keys()):
                p = carry_open[a]
                if carry_gate is not None:
                    block = carry_gate.hard_block(a, t)   # sortie only si funding négatif (anti-whipsaw)
                else:
                    fwin = funding.get(a)
                    regime = "FUNDING_NEUTRAL"
                    if fwin is not None:
                        w = fwin[fwin.index <= t]
                        w = w[w.index.hour.isin((0, 8, 16))].tail(cfg.funding_gate_config.window_periods)
                        regime = classify_funding_regime(w, cfg.funding_gate_config)
                    block = regime != CARRY_OK
                if block:
                    for l in p.legs:
                        if l.is_open:
                            close_leg(l, t, cfg.carry_leg_cost)
                    p.is_open = False
                    del carry_open[a]

            # 5. equity + drawdown
            unreal = sum(l.price_pnl() for p in positions if p.is_open for l in p.legs if l.is_open)
            equity = cash + unreal
            peak = max(peak, equity)
            eq_curve.append((t, equity))
            eq_window.append(equity)
            # DD GLISSANT (gouverneur) — pas le peak all-time (qui ratchet et whipsaw)
            roll_peak = max(eq_window)
            portfolio_dd = (equity - roll_peak) / max(roll_peak, 1e-9)
            long_cost = cfg.taker_fee_bps / 1e4 + cfg.slippage_bps / 1e4

            def _close_pos(p, reason_cost=long_cost):
                for l in p.legs:
                    if l.is_open:
                        close_leg(l, t, reason_cost)
                p.is_open = False

            # 4c. FORCED EXIT on regime flip (logique alpha) — sortir l'ancien risque d'abord
            if cfg.enable_regime_flip_exit and (asset_gate is not None or regime_gate is not None):
                cur_reg = regime_gate.decide_at(t).btc_regime if regime_gate is not None else "n/a"
                for p in positions:
                    if not (p.is_open and p.position_type == "DIRECTIONAL_LONG"):
                        continue
                    # asset-level flip (réparation FORCED_EXIT) : BTC OU l'actif hostile
                    if asset_gate is not None:
                        if asset_gate.should_exit_long(p.asset, t):
                            _close_pos(p)
                    elif should_exit_long_on_regime_flip(p.position_type, cur_reg):
                        _close_pos(p)

            # 4d. INTRA-POSITION DD governor (logique survie, close-only)
            if cfg.enable_intra_position_governor:
                pf_action = decide_position_risk("DIRECTIONAL_LONG", 0.0, portfolio_dd,
                                                 cfg.intra_governor_config)
                if pf_action == "KILL":
                    for p in positions:
                        if p.is_open:
                            _close_pos(p)
                elif pf_action == "CLOSE_ALL_DIRECTIONAL_LONGS":
                    for p in positions:
                        if p.is_open and p.position_type == "DIRECTIONAL_LONG":
                            _close_pos(p)
                else:
                    for p in positions:
                        if not p.is_open or p.position_type != "DIRECTIONAL_LONG":
                            continue
                        unreal_p = sum(l.price_pnl() for l in p.legs if l.is_open)
                        peak_p = max(p.__dict__.get("_peak_unreal", unreal_p), unreal_p)
                        p.__dict__["_peak_unreal"] = peak_p
                        dd_eq = max(0.0, peak_p - unreal_p) / max(equity, 1e-9)
                        if decide_position_risk(p.position_type, dd_eq, portfolio_dd,
                                                cfg.intra_governor_config) == "CLOSE_POSITION":
                            _close_pos(p)

            # 4e. hedge close if no long / governor risk_on handled below
            long_exp = sum(l.qty * (l.mark_price or l.entry_price) for l in open_long_legs())

            # 6-7. CARRY open (gate)
            if cfg.enable_carry:
                for a in self.carry_assets:
                    if a in carry_open:
                        continue
                    carry_mult = 1.0
                    if carry_gate is not None:
                        gd = carry_gate.evaluate(a, t)
                        if gd.status == self._CG_BLOCK:
                            continue
                        carry_mult = gd.carry_size_multiplier
                    else:
                        fwin = funding.get(a)
                        if fwin is None:
                            continue
                        w = fwin[fwin.index <= t]
                        w = w[w.index.hour.isin((0, 8, 16))].tail(cfg.funding_gate_config.window_periods)
                        if classify_funding_regime(w, cfg.funding_gate_config) != CARRY_OK:
                            continue
                        # size-only : la dispersion cross-exchange réduit la taille (pas l'entrée)
                        if carry_size_gate is not None:
                            d2 = carry_size_gate.evaluate(a, t)
                            carry_mult = 0.5 if d2.status == self._CG_BLOCK else d2.carry_size_multiplier
                    price = px(a, t)
                    if not price:
                        continue
                    notional = cfg.carry_fraction * equity * carry_mult
                    qty = notional / price
                    pid = self._new_pid("CARRY")
                    legs = [
                        PositionLeg(self._new_lid(), pid, a, "CARRY_LONG_SPOT", str(t), price, qty, notional,
                                    fees_entry=cfg.carry_leg_cost * notional, mark_price=price),
                        PositionLeg(self._new_lid(), pid, a, "CARRY_SHORT_PERP", str(t), price, qty, notional,
                                    fees_entry=cfg.carry_leg_cost * notional, mark_price=price),
                    ]
                    cash -= (legs[0].fees_entry + legs[1].fees_entry)
                    pnl_acc["fees"] -= (legs[0].fees_entry + legs[1].fees_entry)
                    pos = PortfolioPosition(pid, "DELTA_NEUTRAL_CARRY", "CARRY_BASIS", a, str(t), legs,
                                            funding_regime_at_entry=CARRY_OK)
                    positions.append(pos); carry_open[a] = pos

            # 8. HEDGE governor
            if cfg.enable_hedge:
                d = self.hedge_gov.decide(equity=equity, long_exposure=long_exp,
                                          beta_to_btc=1.0, beta_to_eth=0.0,
                                          btc_regime="UNKNOWN")
                want_hedge = d.state in ("BTC_PARTIAL_HEDGE", "ETH_PARTIAL_HEDGE") and d.hedge_notional > 0
                if hedge_pos and hedge_pos.is_open and (not want_hedge or long_exp <= 0):
                    for l in hedge_pos.legs:
                        if l.is_open:
                            close_leg(l, t, cfg.taker_fee_bps / 1e4 + cfg.slippage_bps / 1e4)
                    hedge_pos.is_open = False; hedge_pos = None
                elif want_hedge and (hedge_pos is None or not hedge_pos.is_open) and long_exp > 0:
                    a = d.hedge_asset
                    price = px(a, t)
                    if price:
                        notional = min(d.hedge_notional, long_exp)
                        qty = notional / price
                        pid = self._new_pid("HEDGE")
                        leg = PositionLeg(self._new_lid(), pid, a, "SHORT_HEDGE", str(t), price, qty, notional,
                                          fees_entry=(cfg.taker_fee_bps + cfg.slippage_bps) / 1e4 * notional,
                                          mark_price=price)
                        cash -= leg.fees_entry; pnl_acc["fees"] -= leg.fees_entry
                        hedge_pos = PortfolioPosition(pid, "PORTFOLIO_HEDGE", "HEDGE_GOVERNOR", a, str(t),
                                                      [leg], linked_position_id="LONG_BOOK",
                                                      hedge_reason=d.reason)
                        positions.append(hedge_pos)

            # 9-10. LONG entries (regime-gated par-asset + anti-churn)
            long_mult = 1.0   # macro BTC (si gate macro seul)
            btc_regime = "n/a"
            if regime_gate is not None:
                gd = regime_gate.decide_at(t)
                long_mult = gd.size_mult
                btc_regime = gd.btc_regime
            if asset_gate is not None:
                btc_regime = asset_gate._btc.decide_at(t).btc_regime
            n_open_long = len({p.position_id for p in positions if p.is_open and p.position_type == "DIRECTIONAL_LONG"})
            entries_this_bar = 0
            # ranker : compteurs bucket/meme/alt sur les longs DÉJÀ ouverts (sélection diversifiée)
            bucket_ct: Dict[str, int] = {}
            meme_ct = alt_ct = 0
            if cfg.enable_ranker:
                for p in positions:
                    if p.is_open and p.position_type == "DIRECTIONAL_LONG":
                        bucket_ct[_bucket_of(p.asset)] = bucket_ct.get(_bucket_of(p.asset), 0) + 1
                        if _is_meme(p.asset):
                            meme_ct += 1
                        if p.asset not in ("BTCUSDT", "ETHUSDT"):
                            alt_ct += 1
            # si gate macro seul bloque tout → pas de candidats (sauf gate par-asset qui décide par opp)
            if asset_gate is None and long_mult <= 0:
                cand_opps = []
            else:
                cand_opps = sorted(long_opps.get(t, []), key=lambda o: -o.score_net)
            for opp in cand_opps:
                if n_open_long >= cfg.max_open_longs or entries_this_bar >= cfg.max_long_entries_per_bar:
                    break
                a = opp.asset
                if cfg.long_universe is not None and a not in cfg.long_universe:
                    continue
                if a in cooldown and t < cooldown[a]:
                    continue
                if any(p.is_open and p.position_type == "DIRECTIONAL_LONG" and p.asset == a for p in positions):
                    continue
                # asset edge gate (causal) : alt non tradable tant qu'il n'a pas prouvé un edge net>0
                if edge_gate is not None and not edge_gate.allows(a, t):
                    continue
                # ranker : caps corrélation / meme / alt (diversification, pas tout exécuter)
                if cfg.enable_ranker:
                    is_major = a in ("BTCUSDT", "ETHUSDT")
                    if bucket_ct.get(_bucket_of(a), 0) >= cfg.ranker_max_per_bucket:
                        continue
                    if _is_meme(a) and meme_ct >= cfg.ranker_max_meme:
                        continue
                    if (not is_major) and alt_ct >= cfg.ranker_max_alt:
                        continue
                # filtre fees-dominated (Phase 3) : expected_return net ≥ mult × coût aller-retour
                if cfg.long_min_er_cost_mult > 0 and \
                        opp.expected_return < cfg.long_min_er_cost_mult * cfg.long_roundtrip:
                    continue
                # mult par-asset (Phase 47) prioritaire
                opp_mult = long_mult
                if asset_gate is not None:
                    opp_mult = asset_gate.decide_long(a, t).size_mult
                if opp_mult <= 0:
                    continue
                price = px(a, t)
                if not price:
                    continue
                notional = cfg.long_fraction * equity * opp_mult
                qty = notional / price
                pid = self._new_pid("LONG")
                leg = PositionLeg(self._new_lid(), pid, a, "LONG_SPOT", str(t), price, qty, notional,
                                  fees_entry=(cfg.taker_fee_bps + cfg.slippage_bps) / 1e4 * notional,
                                  mark_price=price)
                leg.__dict__["_horizon"] = float(opp.expected_holding_hours)
                cash -= leg.fees_entry; pnl_acc["fees"] -= leg.fees_entry
                positions.append(PortfolioPosition(pid, "DIRECTIONAL_LONG", opp.engine_id, a, str(t), [leg]))
                n_open_long += 1; entries_this_bar += 1
                if cfg.enable_ranker:   # MAJ compteurs ranker pour la suite de la barre
                    bucket_ct[_bucket_of(a)] = bucket_ct.get(_bucket_of(a), 0) + 1
                    if _is_meme(a):
                        meme_ct += 1
                    if a not in ("BTCUSDT", "ETHUSDT"):
                        alt_ct += 1

            # invariants (crash si short nu)
            exposures = check_portfolio_invariants(positions, equity, cfg.limits)

            # 11. ledger portefeuille
            port_rows.append({
                "timestamp": t, "equity": equity, "cash": cash,
                "drawdown": (equity - peak) / max(peak, 1e-9),
                "btc_regime": btc_regime,
                **exposures,
                "funding_pnl_total": pnl_acc["carry_funding"] + pnl_acc["hedge"],
                "carry_funding_pnl": pnl_acc["carry_funding"],
                "hedge_pnl": pnl_acc["hedge"], "directional_pnl": pnl_acc["directional"],
                "fees_total": pnl_acc["fees"], "borrow_total": pnl_acc["borrow"],
            })

        # close residuals
        last = grid[-1]
        for p in positions:
            if p.is_open:
                for l in p.legs:
                    if l.is_open:
                        l.mark_price = px(l.asset, last) or l.entry_price
                        sc = self.config.carry_leg_cost if "CARRY" in l.leg_type else \
                            (cfg.taker_fee_bps + cfg.slippage_bps) / 1e4
                        l.is_open = False; l.exit_time = str(last); l.exit_price = l.mark_price
                        cost = sc * l.qty * l.mark_price
                        cash += l.price_pnl() - cost
                p.is_open = False

        # leg ledger
        for p in positions:
            for l in p.legs:
                leg_rows.append({
                    "position_id": p.position_id, "leg_id": l.leg_id, "asset": l.asset,
                    "leg_type": l.leg_type, "position_type": p.position_type, "engine": p.engine_id,
                    "entry_time": l.entry_time, "exit_time": l.exit_time, "qty": l.qty,
                    "notional": l.notional, "entry_price": l.entry_price, "exit_price": l.exit_price,
                    "price_pnl": l.price_pnl(), "funding_pnl": l.funding_pnl_cum,
                    "costs": l.total_costs(), "net_pnl": l.net_pnl(),
                })

        eq = pd.Series(dict(eq_curve)).sort_index()
        metrics = self._metrics(eq, leg_rows)
        pnl_by_type = {
            "directional": round(pnl_acc["directional"], 2),
            "carry_funding": round(pnl_acc["carry_funding"], 2),
            "hedge": round(pnl_acc["hedge"], 2),
            "fees": round(pnl_acc["fees"], 2), "borrow": round(pnl_acc["borrow"], 2),
        }
        return MultiLegResult(eq, pd.DataFrame(port_rows), pd.DataFrame(leg_rows),
                              pnl_by_type, metrics, cfg)

    def _metrics(self, eq: pd.Series, leg_rows: list) -> Dict:
        if len(eq) < 2:
            return {"n_legs": len(leg_rows)}
        ret = eq.pct_change().dropna()
        roll = eq.cummax(); dd = float(((eq - roll) / roll).min())
        monthly = eq.resample("M").last().pct_change().dropna()
        net = pd.Series([r["net_pnl"] for r in leg_rows]) if leg_rows else pd.Series(dtype=float)
        pos = net[net > 0].sum(); neg = -net[net < 0].sum()
        pf = float(pos / neg) if neg > 1e-9 else (float("inf") if pos > 0 else 0.0)
        cvar = float(ret[ret <= ret.quantile(0.05)].mean()) if len(ret) else 0.0
        return {
            "total_return": float(eq.iloc[-1] / eq.iloc[0] - 1),
            "pf": pf, "max_drawdown": dd,
            "roi_month_median": float(monthly.median()) if len(monthly) else 0.0,
            "roi_month_p10": float(monthly.quantile(0.10)) if len(monthly) else 0.0,
            "cvar_95": cvar, "n_legs": len(leg_rows),
            "sharpe": float(ret.mean() / (ret.std() + 1e-9) * np.sqrt(8760)) if len(ret) else 0.0,
        }
