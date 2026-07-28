"""
src/alpha20/tournament/runner_adapters.py — adaptateurs des 5 runners ACTIVE.

Contrat commun (pur, testable SANS ledger ni réseau — l'orchestrateur seul
touche le ledger et le bus) :

    events, new_state = adapter.decide(snapshot, broker, state)

`events` : List[LedgerEvent] prêts à être émis dans le compte du runner par
l'orchestrateur (jamais émis ici). `state` : dict opérationnel persisté par
l'orchestrateur (positions courantes) — un CACHE reconstructible en principe
depuis le ledger, jamais la source de vérité économique.

Chaque décision, prise ou abstention, produit un événement `kind="decision"`
référençant market_event_id, runner_id, config_hash, cutoff, signal, position
avant/après, prix d'arrivée et motif — exigence explicite de la mission.
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

from src.alpha20.contracts import LedgerEvent, clamp_scale
from src.alpha20.execution.paper_broker import Order, PaperBroker
from src.alpha20.tournament.market_bus import MarketSnapshot
from src.alpha20.tournament.runner_registry import RunnerSpec

ROOT = Path(__file__).resolve().parents[3]
ANCHOR_SYMBOL = "BTCUSDT"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _decision_event(spec: RunnerSpec, snapshot: MarketSnapshot, symbol: str,
                    signal: str, before: float, after: float,
                    arrival_price, reason: str,
                    scale: float = None) -> LedgerEvent:
    meta = {"market_event_id": snapshot.market_event_id,
            "runner_id": spec.runner_id, "config_hash": spec.config_hash,
            "cutoff": snapshot.cutoff, "signal": signal,
            "position_before_usdt": round(before, 2),
            "position_after_usdt": round(after, 2),
            "arrival_price": arrival_price, "reason": reason}
    if scale is not None:
        meta["governor_scale"] = scale
    return LedgerEvent(
        ts=_now_iso(), kind="decision", sleeve=symbol, venue=spec.venue or "n/a",
        amount_usdt=0.0, ref="tournament_cycle", meta=meta)


# ── carry/basis (V1.2, SOL, BNB — même mécanisme, config différente) ────────
class CarryBasisAdapter:
    """Re-run déterministe du MultiLegBacktester du paper_start du runner à la
    dernière barre enrichie — même discipline que le paper 200k V1.1. Émet le
    Δ des buckets pnl_by_type (carry_funding/fees/borrow/directional/hedge)
    depuis le dernier cycle. Granularité AGGRÉGÉE par bucket (pas par jambe) —
    limite assumée et documentée, pas un chiffre inventé plus fin."""

    BUCKET_KIND = {"carry_funding": "funding", "fees": "fee", "borrow": "borrow",
                   "directional": "fill", "hedge": "fill"}

    def __init__(self, spec: RunnerSpec):
        self.spec = spec

    def required_universe(self, state: dict) -> List[str]:
        return []                 # source = enriched historique, pas le bus live

    def required_funding(self, state: dict) -> List[str]:
        return []

    def required_quarterly_pairs(self, state: dict) -> List[str]:
        return []

    def _latest_enriched_ts(self) -> str:
        from src.institutional.engines.legacy_bridge import load_enriched
        df = load_enriched(ANCHOR_SYMBOL, required_cols=["close"])
        return str(df["datetime"].max().date()) if df is not None and len(df) \
            else datetime.now(timezone.utc).date().isoformat()

    def decide(self, snapshot: MarketSnapshot, broker: PaperBroker,
              state: dict, risk_state: str = "risk_on",
              scale: float = 1.0) -> Tuple[List[LedgerEvent], dict]:
        # `scale` accepted for signature parity with the other 2 adapters
        # (orchestrator._run_one calls all 3 identically) but INTENTIONALLY
        # NOT applied here -- this adapter doesn't route through PaperBroker
        # or construct Order objects at all; it marks-to-market an internal
        # MultiLegBacktester replay. Wiring the governor's output into that
        # replay is a separate, deeper change, tracked as a known open gap
        # in docs/v2/PHASE1_DIAGNOSTIC.md §6 -- not silently fixed here.
        from src.institutional.engines.registry import build_engine
        from src.institutional.backtest.multileg_backtester import (
            MultiLegBacktester, MultiLegConfig)

        cfg_d = dict(self.spec.config)
        start = state.get("paper_start") or datetime.now(timezone.utc).date().isoformat()
        end = self._latest_enriched_ts()
        if state.get("last_run_end") == end:
            return [], state          # même barre : rien de neuf à marquer
        longs = [build_engine(e if isinstance(e, str) else e["id"])
                 for e in cfg_d.get("engines_long", [])]
        cfg = MultiLegConfig(
            initial_capital=self.spec.capital_standalone_eur,
            enable_long=bool(cfg_d.get("engines_long")) and cfg_d.get("enable_long", True),
            enable_asset_regime_gate=cfg_d.get("enable_asset_regime_gate", False),
            enable_regime_flip_exit=cfg_d.get("enable_regime_flip_exit", False),
            enable_intra_position_governor=cfg_d.get("enable_intra_position_governor", False),
            enable_carry=True, carry_fraction=cfg_d.get("carry_fraction", 0.0),
            long_fraction=cfg_d.get("long_fraction", 0.0),
            max_open_longs=cfg_d.get("max_open_longs", 3),
            enable_hedge=cfg_d.get("enable_hedge", False),
            enable_ranker=cfg_d.get("enable_ranker", False),
            ranker_max_per_bucket=cfg_d.get("ranker_max_per_bucket", 2),
            ranker_max_meme=cfg_d.get("ranker_max_meme", 1),
            ranker_max_alt=cfg_d.get("ranker_max_alt", 5),
            enable_asset_edge_gate=cfg_d.get("enable_asset_edge_gate", False),
            asset_edge_min_net=cfg_d.get("asset_edge_min_net", 0.0),
            asset_edge_min_signals=cfg_d.get("asset_edge_min_signals", 20),
            carry_gate_v2=False,           # REJECTED_AS_EXECUTION_GATE — verrouillé
        )
        try:
            res = MultiLegBacktester(longs, cfg,
                                     carry_assets=cfg_d.get("carry_assets", [])
                                     ).run(start, end)
        except Exception as e:              # noqa: BLE001 — isolation runner
            return [self._decision_abstain(snapshot, f"backtest_error: {e}")], state

        last_cum = state.get("last_cum_pnl_by_type", {})
        events = []
        equity_before = state.get("last_equity", self.spec.capital_standalone_eur)
        equity_after = float(res.equity.iloc[-1]) if len(res.equity) else equity_before
        for bucket, kind in self.BUCKET_KIND.items():
            cum = float(res.pnl_by_type.get(bucket, 0.0))
            delta = cum - float(last_cum.get(bucket, 0.0))
            if abs(delta) < 1e-9:
                continue
            events.append(LedgerEvent(ts=_now_iso(), kind=kind, sleeve=bucket,
                                      venue=self.spec.venue or "binance_usdm",
                                      amount_usdt=round(delta, 6), ref="mtm_cycle",
                                      meta={"cumulative_usdt": round(cum, 2)}))
        ev = _decision_event(
            self.spec, snapshot, "portfolio", "mark_to_market",
            equity_before, equity_after, None,
            f"MultiLegBacktester {start}->{end}, Δpnl={{"
            + ",".join(f"{k}:{round(res.pnl_by_type.get(k, 0.0) - last_cum.get(k, 0.0), 2)}"
                       for k in self.BUCKET_KIND) + "}")
        # NB honnête : ce runner ne route PAS ses jambes par le broker paper
        # partagé — le coût vient du modèle interne, déjà réaliste et validé,
        # de MultiLegBacktester (mêmes ordres de grandeur que fee_registry :
        # taker 5bp/maker 1bp/slippage 2bp). basis_term_v0 et mh_events_exec,
        # eux, routent CHAQUE ordre par le broker (voir leurs adaptateurs).
        ev.meta["broker_routed"] = False
        ev.meta["cost_model"] = "multileg_backtester_internal"
        events.append(ev)
        gross_usdt = 0.0
        if len(res.portfolio_ledger):
            last_row = res.portfolio_ledger.iloc[-1]
            for col in ("gross_exposure", "gross_notional", "gross"):
                if col in last_row.index:
                    gross_usdt = float(last_row[col])
                    break
        new_state = dict(state, paper_start=start, last_run_end=end,
                         last_cum_pnl_by_type={k: float(res.pnl_by_type.get(k, 0.0))
                                               for k in self.BUCKET_KIND},
                         last_equity=equity_after, gross_usdt=gross_usdt)
        return events, new_state

    def _decision_abstain(self, snapshot, reason):
        return _decision_event(self.spec, snapshot, "portfolio", "abstain",
                               0.0, 0.0, None, reason)


# ── BASIS_TERM : cash-and-carry trimestriel via le broker paper ────────────
class BasisTermAdapter:
    """Règle DÉCLARÉE (scripts/backtest_basis_term.py) rejouée en live sur la
    découverte dynamique des trimestriels. Chaque ouverture/clôture passe par
    broker.execute_pair (spot + quarterly) — coûts du fee_registry, pas un
    forfait codé en dur."""

    def __init__(self, spec: RunnerSpec):
        self.spec = spec

    def required_universe(self, state: dict) -> List[str]:
        return list(self.spec.assets)

    def required_funding(self, state: dict) -> List[str]:
        return []

    def required_quarterly_pairs(self, state: dict) -> List[str]:
        return list(self.spec.assets)

    def decide(self, snapshot: MarketSnapshot, broker: PaperBroker,
              state: dict, risk_state: str = "risk_on",
              scale: float = 1.0) -> Tuple[List[LedgerEvent], dict]:
        scale = clamp_scale(scale)
        cfg = self.spec.config
        events = []
        positions = dict(state.get("positions", {}))
        capital = self.spec.capital_standalone_eur
        for asset in self.spec.assets:
            spot = snapshot.price(asset)
            contracts = snapshot.quarterlies.get(asset, [])
            pos = positions.get(asset)
            if pos is not None:
                days_left = pos["days_to_expiry_at_open"] - pos.get("cycles_elapsed", 0)
                still = [c for c in contracts if c["symbol"] == pos["symbol"]]
                converged = (not still) or still[0]["days_to_expiry"] <= 0.5
                if converged:
                    exit_spot = broker.execute(
                        Order(self.spec.runner_id, asset, self.spec.venue, -1,
                             pos["notional_usdt"], "spot", "taker", is_exit=True),
                        snapshot, risk_state)["observed"]
                    exit_q = broker.execute(
                        Order(self.spec.runner_id, pos["symbol"], self.spec.venue, +1,
                             pos["notional_usdt"], "quarterly", "taker", is_exit=True),
                        snapshot, risk_state)["observed"]
                    capture = pos["notional_usdt"] * pos["basis_entry"]
                    events.append(LedgerEvent(
                        ts=_now_iso(), kind="fill", sleeve=f"basis_{asset}",
                        venue=self.spec.venue, amount_usdt=round(capture, 6),
                        ref="basis_convergence",
                        meta={"basis_entry": pos["basis_entry"]}))
                    for f, leg in ((exit_spot, "spot"), (exit_q, "quarterly")):
                        if not f.rejected and f.fee_usdt:
                            events.append(LedgerEvent(
                                ts=_now_iso(), kind="fee", sleeve=f"basis_{asset}",
                                venue=self.spec.venue, amount_usdt=-f.fee_usdt,
                                ref=f"exit_{leg}"))
                    events.append(_decision_event(
                        self.spec, snapshot, asset, "close_convergence",
                        pos["notional_usdt"], 0.0, spot, "échéance atteinte"))
                    positions.pop(asset, None)
                else:
                    positions[asset]["cycles_elapsed"] = pos.get("cycles_elapsed", 0) + 1
                continue

            candidate = next((c for c in contracts
                              if cfg["min_days_to_expiry"] <= c["days_to_expiry"]
                              <= cfg["max_days_to_expiry"] and c["price"]), None)
            if not candidate or not spot:
                events.append(_decision_event(self.spec, snapshot, asset, "abstain",
                                              0.0, 0.0, spot,
                                              "aucun contrat éligible ou prix manquant"))
                continue
            basis_ann = ((candidate["price"] / spot - 1) * 365 / candidate["days_to_expiry"])
            if basis_ann < cfg["entry_threshold_ann"]:
                events.append(_decision_event(
                    self.spec, snapshot, asset, "abstain", 0.0, 0.0, spot,
                    f"basis annualisé {basis_ann:.2%} < seuil {cfg['entry_threshold_ann']:.2%}"))
                continue
            if risk_state == "kill" or scale <= 0.0:
                reason = "kill_switch_active" if risk_state == "kill" else "governor_scale_zero"
                events.append(LedgerEvent(ts=_now_iso(), kind="reject",
                                          sleeve=f"basis_{asset}", venue=self.spec.venue,
                                          amount_usdt=0.0, ref="kill_switch",
                                          meta={"reason": reason, "governor_scale": scale}))
                events.append(_decision_event(self.spec, snapshot, asset, "abstain",
                                              0.0, 0.0, spot,
                                              f"ordre interdit: {reason}", scale))
                continue
            # scale applied to the REQUESTED notional before Order() is built --
            # nothing downstream (broker, fill scenarios) ever sees the
            # unscaled size, so nothing later can re-inflate it.
            notional = capital * cfg["sizing_frac"] * scale
            scen_spot = broker.execute(Order(self.spec.runner_id, asset, self.spec.venue,
                                             +1, notional, "spot", "taker"),
                                       snapshot, risk_state)
            scen_q = broker.execute(Order(self.spec.runner_id, candidate["symbol"],
                                          self.spec.venue, -1, notional, "quarterly",
                                          "taker"), snapshot, risk_state)
            f_spot, f_q = scen_spot["observed"], scen_q["observed"]
            if f_spot.rejected or f_q.rejected:
                reason = f_spot.reject_reason or f_q.reject_reason or "leg_rejected"
                events.append(LedgerEvent(ts=_now_iso(), kind="reject",
                                          sleeve=f"basis_{asset}", venue=self.spec.venue,
                                          amount_usdt=0.0, ref="entry_rejected",
                                          meta={"reason": reason}))
                events.append(_decision_event(self.spec, snapshot, asset, "abstain",
                                              0.0, 0.0, spot, f"jambe rejetée: {reason}"))
                continue
            for f, leg in ((f_spot, "spot"), (f_q, "quarterly")):
                if f.fee_usdt:
                    events.append(LedgerEvent(ts=_now_iso(), kind="fee",
                                              sleeve=f"basis_{asset}", venue=self.spec.venue,
                                              amount_usdt=-f.fee_usdt, ref=f"entry_{leg}"))
            positions[asset] = {"symbol": candidate["symbol"], "notional_usdt": notional,
                                "basis_entry": basis_ann * candidate["days_to_expiry"] / 365,
                                "days_to_expiry_at_open": candidate["days_to_expiry"],
                                "cycles_elapsed": 0}
            open_ev = _decision_event(self.spec, snapshot, asset, "open",
                                      0.0, notional, spot,
                                      f"basis_ann={basis_ann:.2%} ≥ seuil", scale)
            # scénarios de robustesse (observed/coûts×1.5/×2/latence/fills
            # partiels/panne venue), calculés SIMULTANÉMENT — jamais utilisés
            # pour ajuster la décision, seulement pour l'audit de robustesse
            open_ev.meta["scenarios_spot"] = {n: {"fee_bp": f.fee_bp,
                                                   "rejected": f.rejected}
                                              for n, f in scen_spot.items()}
            open_ev.meta["scenarios_quarterly"] = {n: {"fee_bp": f.fee_bp,
                                                        "rejected": f.rejected}
                                                   for n, f in scen_q.items()}
            events.append(open_ev)
        return events, dict(state, positions=positions)


# ── MH events : LECTURE SEULE du shadow, replay d'exécution décomposé ──────
class MHEventsAdapter:
    """Réutilise EXACTEMENT la math de scripts/run_paper_mh_exec.py (import
    par chemin, pas de réimplémentation) — même décomposition sampling/
    exécution/modèle exigée par la mission. Ne touche jamais le ledger shadow
    (lecture seule sur decisions.parquet)."""

    def __init__(self, spec: RunnerSpec):
        self.spec = spec
        self._rme = None

    def required_universe(self, state: dict) -> List[str]:
        """Peek léger et LECTURE SEULE du ledger shadow : symboles des
        décisions book non encore vues — la même union que decide() verra,
        pour que le snapshot du cycle les couvre déjà."""
        rme = self._mod()
        if not rme.rmh.SHADOW_LEDGER.exists():
            return []
        try:
            paper_start = state.get("paper_start") \
                or datetime.now(timezone.utc).date().isoformat()
            book = rme.rmh.select_book(pd.read_parquet(rme.rmh.SHADOW_LEDGER),
                                       pd.Timestamp(paper_start, tz="UTC"))
            seen = set(state.get("seen_refs", []))
            book = book[~book["event_time"].astype(str).isin(seen)] if len(book) else book
            return sorted(set(book["symbol"])) if len(book) else []
        except Exception:                        # noqa: BLE001
            return []

    def required_funding(self, state: dict) -> List[str]:
        return []

    def required_quarterly_pairs(self, state: dict) -> List[str]:
        return []

    def _mod(self):
        if self._rme is None:
            spec = importlib.util.spec_from_file_location(
                "rme_tournament", ROOT / "scripts" / "run_paper_mh_exec.py")
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            self._rme = mod
        return self._rme

    def decide(self, snapshot: MarketSnapshot, broker: PaperBroker,
              state: dict, risk_state: str = "risk_on",
              scale: float = 1.0) -> Tuple[List[LedgerEvent], dict]:
        scale = clamp_scale(scale)
        rme = self._mod()
        if not rme.rmh.SHADOW_LEDGER.exists():
            return [_decision_event(self.spec, snapshot, "n/a", "abstain",
                                    0.0, 0.0, None, "pas de ledger shadow")], state
        paper_start = state.get("paper_start") or datetime.now(timezone.utc).date().isoformat()
        book = rme.rmh.select_book(pd.read_parquet(rme.rmh.SHADOW_LEDGER),
                                   pd.Timestamp(paper_start, tz="UTC"))
        cfg = self.spec.config
        book = book[book["horizon"].str.startswith(cfg["horizon_filter"])
                    & (book.get("tier", "book") == cfg["tier_filter"])] \
            if len(book) else book
        seen = set(state.get("seen_refs", []))
        book = book[~book["event_time"].astype(str).isin(seen)] if len(book) else book
        if book.empty:
            return [], dict(state, paper_start=paper_start)

        cost_bp = rme.LABEL_COST_RT_BP        # coût réel appliqué séparément via broker ci-dessous
        events, new_seen = [], list(seen)
        for _, r in book.iterrows():
            closes = rme._closes(r["symbol"])
            rep = rme.replay_decision(r, closes, cost_bp)
            new_seen.append(str(r["event_time"]))
            requested_notional = self.spec.capital_standalone_eur * cfg["weight_per_decision"]
            if rep is None:
                events.append(_decision_event(self.spec, snapshot, r["symbol"],
                                              "abstain", 0.0, 0.0, None,
                                              "données de replay indisponibles"))
                continue
            net_exec, entry_ts, exit_ts, gross = rep
            if risk_state == "kill" or scale <= 0.0:
                reason = "kill_switch_active" if risk_state == "kill" else "governor_scale_zero"
                events.append(LedgerEvent(ts=_now_iso(), kind="reject",
                                          sleeve=f"mh_{r['engine']}", venue=self.spec.venue,
                                          amount_usdt=0.0, ref="kill_switch",
                                          meta={"reason": reason, "governor_scale": scale}))
                events.append(_decision_event(self.spec, snapshot, r["symbol"],
                                              "abstain", 0.0, 0.0, None,
                                              f"ordre interdit: {reason}", scale))
                continue
            # scale applied to the REQUESTED notional before Order() is built --
            # nothing downstream (broker, fill scenarios) ever sees the
            # unscaled size, so nothing later can re-inflate it.
            notional = requested_notional * scale
            scen = broker.execute(Order(self.spec.runner_id, r["symbol"],
                                        self.spec.venue, 1, notional, "perp",
                                        "taker"), snapshot, risk_state)
            fill = scen["observed"]
            real_net = gross - (fill.fee_bp + fill.slippage_bp) / 1e4
            pnl = notional * real_net
            net_label = float(r["net_labeled"]) if np.isfinite(
                pd.to_numeric(r["net_labeled"], errors="coerce")) else np.nan
            net_grid = gross - cost_bp / 1e4
            events.append(LedgerEvent(
                ts=_now_iso(), kind="fill", sleeve=f"mh_{r['engine']}",
                venue=self.spec.venue, amount_usdt=round(pnl, 6), ref="mh_replay",
                meta={"symbol": r["symbol"], "net_label": net_label,
                     "net_grid": net_grid, "net_exec": real_net,
                     "sampling_error_1h": net_grid - net_label
                     if np.isfinite(net_label) else None,
                     "execution_shortfall": real_net - net_grid,
                     "entry_ts": str(entry_ts), "exit_ts": str(exit_ts)}))
            dec_ev = _decision_event(
                self.spec, snapshot, r["symbol"], "mh_consensus_replay",
                0.0, notional, fill.avg_price,
                f"engine={r['engine']} score={r['score']:.3f}", scale)
            dec_ev.meta["scenarios"] = {n: {"fee_bp": f.fee_bp, "rejected": f.rejected}
                                        for n, f in scen.items()}
            events.append(dec_ev)
        return events, dict(state, paper_start=paper_start, seen_refs=new_seen[-5000:])


ADAPTERS = {
    "carry_basis_v12": CarryBasisAdapter,
    "carry_solusdt": CarryBasisAdapter,
    "carry_bnbusdt": CarryBasisAdapter,
    "basis_term_v0": BasisTermAdapter,
    "mh_events_exec": MHEventsAdapter,
}


def build_adapter(spec: RunnerSpec):
    cls = ADAPTERS.get(spec.runner_id)
    if cls is None:
        raise KeyError(f"aucun adaptateur pour {spec.runner_id!r}")
    return cls(spec)
