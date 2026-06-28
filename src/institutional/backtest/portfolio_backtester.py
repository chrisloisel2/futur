"""
src/institutional/backtest/portfolio_backtester.py
─────────────────────────────────────────────────────────────────────────────
Backtester PORTEFEUILLE — multi-moteur, multi-position, multi-actif.

Le backtest système (≠ backtest moteur) : simule plusieurs moteurs alpha en
parallèle, positions simultanées, frais + slippage, cooldown LOCAL
(asset, engine, direction), contraintes d'exposition et de corrélation, exits
(time / stop / take-profit / exit-engine), kill switch.

⚠️ Sizing : en backtest on dimensionne à la taille "normale"
(max_position_fraction) ; l'échelle de promotion live (STATUS_SIZE_FRACTION)
gouverne le CAPITAL RÉEL, pas la simulation.

Sortie : equity curve horaire, trades, PnL par moteur, métriques + gate
portefeuille (réutilise backtest/metrics.compute_equity_metrics).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from src.institutional.backtest.metrics import compute_equity_metrics
from src.institutional.contracts import Opportunity
from src.institutional.engines.base import AlphaEngine
from src.institutional.engines.legacy_bridge import load_enriched

logger = logging.getLogger(__name__)

_DIR_SIGN = {"LONG": 1.0, "SHORT_HEDGE": -1.0, "CASH": 0.0}


@dataclass
class PortfolioBacktestConfig:
    initial_capital: float = 10_000.0
    taker_fee_bps: float = 5.0
    slippage_bps: float = 2.0
    # contraintes portefeuille
    max_open_positions: int = 4
    max_positions_per_asset: int = 1
    max_positions_per_bucket: int = 2
    max_gross_exposure: float = 0.75
    max_single_asset_exposure: float = 0.25
    max_single_engine_exposure: float = 0.35
    position_fraction: float = 0.25         # taille de base par trade
    # cooldown : local (asset,engine,dir)=horizon  vs  global (legacy)
    cooldown_mode: str = "local"            # "local" | "global"
    global_cooldown_hours: float = 8.0
    # exits
    use_stop: bool = True
    use_take_profit: bool = True
    # hook exit-engine optionnel : f(position_dict, bar_row) -> bool (True=exit)
    exit_hook: Optional[Callable] = None
    # hook sizing/allocator optionnel : f(candidates, ctx) -> List[(opp, frac)]
    allocator_hook: Optional[Callable] = None
    # hook risk governor optionnel : f(timestamp, ctx) -> float (mult global) ou 0 = halt
    governor_hook: Optional[Callable] = None

    @property
    def roundtrip_cost(self) -> float:
        return 2.0 * (self.taker_fee_bps + self.slippage_bps) / 10_000.0


@dataclass
class _OpenPosition:
    engine_id: str
    asset: str
    direction: str
    bucket: str
    entry_time: pd.Timestamp
    entry_price: float
    notional: float
    planned_exit: pd.Timestamp
    stop_price: Optional[float]
    tp_price: Optional[float]

    def unrealized(self, price: float) -> float:
        sign = _DIR_SIGN.get(self.direction, 0.0)
        return self.notional * (price / self.entry_price - 1.0) * sign


@dataclass
class PortfolioBacktestResult:
    equity: pd.Series
    trades: pd.DataFrame
    metrics: Dict
    per_engine_pnl: Dict[str, float]
    config: PortfolioBacktestConfig
    gate: Dict = field(default_factory=dict)

    def summary(self) -> str:
        m = self.metrics
        lines = [
            f"Equity {self.equity.iloc[0]:.0f} → {self.equity.iloc[-1]:.0f}  "
            f"({(self.equity.iloc[-1]/self.equity.iloc[0]-1)*100:+.1f}%)",
            f"trades={m.get('n_trades',0)}  PF={m.get('pf',0):.2f}  "
            f"Sharpe={m.get('sharpe',0):.2f}  maxDD={m.get('max_drawdown',0)*100:.1f}%  "
            f"CVaR95={m.get('cvar_95',0)*100:.2f}%",
            f"ROI/mois med={m.get('roi_month_median',0)*100:+.2f}%  "
            f"p10={m.get('roi_month_p10',0)*100:+.2f}%  trades/mois={m.get('trades_per_month',0):.1f}",
            f"contribution moteur: {self.per_engine_pnl}",
            f"GATE: {self.gate.get('verdict','?')}  ({'; '.join(self.gate.get('fails', []) or ['OK'])})",
        ]
        return "\n".join(lines)


class PortfolioBacktester:
    def __init__(self, engines: List[AlphaEngine], config: Optional[PortfolioBacktestConfig] = None):
        self.engines = engines
        self.config = config or PortfolioBacktestConfig()

    # ── données ──────────────────────────────────────────────────────────────
    def _load_prices(self, assets: List[str], start: str, end: str) -> Dict[str, pd.Series]:
        prices: Dict[str, pd.Series] = {}
        for a in assets:
            df = load_enriched(a, required_cols=["close"], start=start, end=end)
            if df is not None and not df.empty:
                prices[a] = df.set_index("datetime")["close"].sort_index()
        return prices

    def _collect_actionable(self, start: str, end: str) -> pd.DataFrame:
        """Récupère toutes les Opportunity A_TRADE de tous les moteurs."""
        rows = []
        for eng in self.engines:
            for opp in eng.generate_all(start, end):
                if opp.decision_zone != "A_TRADE" or opp.direction == "CASH":
                    continue
                rows.append({
                    "timestamp": pd.Timestamp(opp.timestamp),
                    "engine_id": opp.engine_id, "asset": opp.asset,
                    "direction": opp.direction, "bucket": opp.correlation_bucket,
                    "p_success": opp.p_success, "expected_return": opp.expected_return,
                    "expected_vol": opp.expected_vol, "expected_cost": opp.expected_cost,
                    "score_net": opp.score_net, "confidence": opp.confidence,
                    "holding_hours": opp.expected_holding_hours,
                    "max_position_fraction": opp.max_position_fraction,
                    "stop_loss": opp.stop_loss, "take_profit": opp.take_profit,
                    "_opp": opp,
                })
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows).sort_values("timestamp").reset_index(drop=True)
        return df

    # ── simulation ───────────────────────────────────────────────────────────
    def run(self, assets: Optional[List[str]], start: str, end: str) -> PortfolioBacktestResult:
        cfg = self.config
        # assets = union des assets des moteurs si non spécifié
        if assets is None:
            assets = sorted({a for e in self.engines for a in e.assets})
        prices = self._load_prices(assets, start, end)
        if not prices:
            raise ValueError("Aucun prix chargé pour les assets demandés")

        grid = sorted(set().union(*[set(s.index) for s in prices.values()]))
        grid = [t for t in grid if pd.Timestamp(start, tz="UTC") <= t <= pd.Timestamp(end, tz="UTC")]
        opps = self._collect_actionable(start, end)
        opps_by_ts: Dict[pd.Timestamp, pd.DataFrame] = (
            {ts: g for ts, g in opps.groupby("timestamp")} if not opps.empty else {}
        )

        equity = cfg.initial_capital
        open_positions: List[_OpenPosition] = []
        cooldowns: Dict[Tuple[str, str, str], pd.Timestamp] = {}
        global_cooldown_until: Optional[pd.Timestamp] = None
        equity_curve: List[Tuple[pd.Timestamp, float]] = []
        trades: List[dict] = []
        per_engine_pnl: Dict[str, float] = {}

        def price_at(asset: str, ts: pd.Timestamp) -> Optional[float]:
            s = prices.get(asset)
            if s is None:
                return None
            idx = s.index.searchsorted(ts, side="right") - 1
            return float(s.iloc[idx]) if idx >= 0 else None

        for ts in grid:
            # 1. mark-to-market + exits sur positions ouvertes
            still_open: List[_OpenPosition] = []
            for pos in open_positions:
                px = price_at(pos.asset, ts)
                if px is None:
                    still_open.append(pos)
                    continue
                exit_reason = None
                if cfg.use_stop and pos.stop_price and px <= pos.stop_price and pos.direction == "LONG":
                    exit_reason = "STOP"
                elif cfg.use_take_profit and pos.tp_price and px >= pos.tp_price and pos.direction == "LONG":
                    exit_reason = "TAKE_PROFIT"
                elif ts >= pos.planned_exit:
                    exit_reason = "HORIZON"
                elif cfg.exit_hook is not None:
                    try:
                        if cfg.exit_hook(pos, pos.asset, ts, px):
                            exit_reason = "EXIT_ENGINE"
                    except Exception:
                        pass
                if exit_reason:
                    sign = _DIR_SIGN.get(pos.direction, 0.0)
                    gross = (px / pos.entry_price - 1.0) * sign
                    pnl_frac = gross - cfg.roundtrip_cost
                    pnl_usd = pos.notional * pnl_frac
                    equity += pnl_usd
                    per_engine_pnl[pos.engine_id] = per_engine_pnl.get(pos.engine_id, 0.0) + pnl_usd
                    trades.append({
                        "engine_id": pos.engine_id, "asset": pos.asset, "direction": pos.direction,
                        "entry_time": pos.entry_time, "exit_time": ts,
                        "entry_price": pos.entry_price, "exit_price": px,
                        "holding_bars": int((ts - pos.entry_time) / pd.Timedelta(hours=1)),
                        "pnl_net": pnl_usd, "pnl_frac": pnl_frac, "exit_reason": exit_reason,
                        "notional": pos.notional,
                    })
                    if cfg.cooldown_mode == "local":
                        cooldowns[(pos.asset, pos.engine_id, pos.direction)] = ts + pd.Timedelta(
                            hours=self._engine_horizon(pos.engine_id))
                    else:
                        global_cooldown_until = ts + pd.Timedelta(hours=cfg.global_cooldown_hours)
                else:
                    still_open.append(pos)
            open_positions = still_open

            # equity mark-to-market
            mtm = equity + sum(p.unrealized(price_at(p.asset, ts) or p.entry_price) for p in open_positions)
            equity_curve.append((ts, mtm))

            # 2. governor (halt global éventuel)
            gov_mult = 1.0
            if cfg.governor_hook is not None:
                try:
                    gov_mult = float(cfg.governor_hook(ts, {"equity": mtm, "positions": open_positions}))
                except Exception:
                    gov_mult = 1.0
            if gov_mult <= 0.0:
                continue

            # 3. nouvelles entrées
            if global_cooldown_until is not None and ts < global_cooldown_until:
                continue
            cands = opps_by_ts.get(ts)
            if cands is None or cands.empty:
                continue

            selected = self._select(cands, open_positions, ts, cooldowns, mtm, gov_mult)
            for opp_row, frac in selected:
                px = price_at(opp_row["asset"], ts)
                if px is None or frac <= 0:
                    continue
                notional = frac * mtm
                pos = _OpenPosition(
                    engine_id=opp_row["engine_id"], asset=opp_row["asset"],
                    direction=opp_row["direction"], bucket=opp_row["bucket"],
                    entry_time=ts, entry_price=px, notional=notional,
                    planned_exit=ts + pd.Timedelta(hours=float(opp_row["holding_hours"])),
                    stop_price=px * (1 - opp_row["stop_loss"]) if opp_row["stop_loss"] else None,
                    tp_price=px * (1 + opp_row["take_profit"]) if opp_row["take_profit"] else None,
                )
                open_positions.append(pos)
                if cfg.cooldown_mode == "local":
                    cooldowns[(pos.asset, pos.engine_id, pos.direction)] = ts + pd.Timedelta(
                        hours=self._engine_horizon(pos.engine_id))

        # close residual positions at last price
        last_ts = grid[-1]
        for pos in open_positions:
            px = price_at(pos.asset, last_ts) or pos.entry_price
            sign = _DIR_SIGN.get(pos.direction, 0.0)
            pnl_usd = pos.notional * ((px / pos.entry_price - 1.0) * sign - cfg.roundtrip_cost)
            equity += pnl_usd
            per_engine_pnl[pos.engine_id] = per_engine_pnl.get(pos.engine_id, 0.0) + pnl_usd
            trades.append({
                "engine_id": pos.engine_id, "asset": pos.asset, "direction": pos.direction,
                "entry_time": pos.entry_time, "exit_time": last_ts, "entry_price": pos.entry_price,
                "exit_price": px, "holding_bars": int((last_ts - pos.entry_time) / pd.Timedelta(hours=1)),
                "pnl_net": pnl_usd, "pnl_frac": (px/pos.entry_price-1)*sign - cfg.roundtrip_cost,
                "exit_reason": "END", "notional": pos.notional,
            })

        eq = pd.Series(dict(equity_curve)).sort_index()
        eq.iloc[-1] = equity if len(eq) else cfg.initial_capital
        trades_df = pd.DataFrame(trades)
        metrics = self._metrics(eq, trades_df)
        gate = self._gate(metrics, per_engine_pnl)
        return PortfolioBacktestResult(
            equity=eq, trades=trades_df, metrics=metrics,
            per_engine_pnl={k: round(v, 2) for k, v in per_engine_pnl.items()},
            config=cfg, gate=gate,
        )

    # ── sélection (contraintes) ────────────────────────────────────────────────
    def _select(self, cands, open_positions, ts, cooldowns, equity, gov_mult):
        cfg = self.config
        # tri par score net décroissant (allocator hook prioritaire)
        if cfg.allocator_hook is not None:
            try:
                return cfg.allocator_hook(cands, {
                    "open_positions": open_positions, "equity": equity, "ts": ts, "gov_mult": gov_mult,
                })
            except Exception:
                pass
        cands = cands.sort_values("score_net", ascending=False)

        open_assets = {p.asset for p in open_positions}
        engine_exp = {}
        bucket_count = {}
        for p in open_positions:
            engine_exp[p.engine_id] = engine_exp.get(p.engine_id, 0.0) + p.notional / max(equity, 1e-9)
            bucket_count[p.bucket] = bucket_count.get(p.bucket, 0) + 1
        gross = sum(p.notional for p in open_positions) / max(equity, 1e-9)
        n_open = len(open_positions)

        selected = []
        for _, row in cands.iterrows():
            if n_open >= cfg.max_open_positions:
                break
            key = (row["asset"], row["engine_id"], row["direction"])
            if key in cooldowns and ts < cooldowns[key]:
                continue
            if cfg.max_positions_per_asset and row["asset"] in open_assets:
                continue
            if bucket_count.get(row["bucket"], 0) >= cfg.max_positions_per_bucket:
                continue
            frac = min(cfg.position_fraction, cfg.max_single_asset_exposure) * gov_mult
            if engine_exp.get(row["engine_id"], 0.0) + frac > cfg.max_single_engine_exposure:
                frac = max(0.0, cfg.max_single_engine_exposure - engine_exp.get(row["engine_id"], 0.0))
            if gross + frac > cfg.max_gross_exposure:
                frac = max(0.0, cfg.max_gross_exposure - gross)
            if frac <= 1e-6:
                continue
            selected.append((row, frac))
            open_assets.add(row["asset"])
            engine_exp[row["engine_id"]] = engine_exp.get(row["engine_id"], 0.0) + frac
            bucket_count[row["bucket"]] = bucket_count.get(row["bucket"], 0) + 1
            gross += frac
            n_open += 1
        return selected

    def _engine_horizon(self, engine_id: str) -> float:
        for e in self.engines:
            if e.engine_id == engine_id:
                return e.horizon_hours
        return self.config.global_cooldown_hours

    # ── métriques + gate ───────────────────────────────────────────────────────
    def _metrics(self, eq: pd.Series, trades_df: pd.DataFrame) -> Dict:
        if len(eq) < 2:
            return {"n_trades": int(len(trades_df))}
        try:
            rep = compute_equity_metrics(eq, trades_df if not trades_df.empty else None)
            m = rep.to_dict()
        except Exception as e:
            logger.warning("compute_equity_metrics échec: %s", e)
            m = {"n_trades": int(len(trades_df))}
        # extras portefeuille
        monthly = eq.resample("M").last().pct_change().dropna()
        m["roi_month_median"] = float(monthly.median()) if len(monthly) else 0.0
        m["roi_month_p10"] = float(monthly.quantile(0.10)) if len(monthly) else 0.0
        hourly = eq.pct_change().dropna()
        if len(hourly):
            var95 = hourly.quantile(0.05)
            m["cvar_95"] = float(hourly[hourly <= var95].mean())
        else:
            m["cvar_95"] = 0.0
        n_months = max(len(eq) / (24 * 30), 1e-9)
        m["trades_per_month"] = float(len(trades_df) / n_months)
        return m

    def _gate(self, m: Dict, per_engine_pnl: Dict[str, float]) -> Dict:
        """Gate portefeuille (cf. brief Étape 4)."""
        total_pnl = sum(per_engine_pnl.values())
        max_share = (max((abs(v) for v in per_engine_pnl.values()), default=0.0) /
                     abs(total_pnl)) if total_pnl else 0.0
        checks = {
            "roi_month_median>=3%": m.get("roi_month_median", 0) >= 0.03,
            "roi_month_p10>0": m.get("roi_month_p10", 0) > 0,
            "pf_net>=1.30": m.get("pf", 0) >= 1.30,
            "max_dd<=3%": abs(m.get("max_drawdown", 1)) <= 0.03,
            "cvar95<=1.5%": abs(m.get("cvar_95", 1)) <= 0.015,
            "trades/month>=30": m.get("trades_per_month", 0) >= 30,
            "no_engine>60%pnl": (max_share <= 0.60) if total_pnl else False,
        }
        fails = [k for k, ok in checks.items() if not ok]
        return {
            "verdict": "PASS" if not fails else "FAIL",
            "checks": checks, "fails": fails,
            "max_engine_pnl_share": round(max_share, 3),
        }
