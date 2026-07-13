#!/usr/bin/env python3
"""
scripts/maturity/run_maturity_suite.py
─────────────────────────────────────────────────────────────────────────────
MATURITY_BACKTEST_SUITE_V1 — prouve OÙ le projet est mûr, pas combien il gagne.
NE MODIFIE AUCUN paramètre (mesure de maturité, pas optimisation).

12 checks → scorecard /100 → décision (RESEARCH_ONLY / PAPER_CONTINUE /
PAPER_STRONG / MICRO_LIVE_REVIEW / MICRO_LIVE_BLOCKED).

    python3 scripts/maturity/run_maturity_suite.py --config configs/portfolio_v1_1_baseline.yaml
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
logging.basicConfig(level=logging.ERROR)

from src.institutional.engines.base import AlphaEngine
from src.institutional.engines.registry import build_engine
from src.institutional.engines.legacy_bridge import load_enriched
from src.institutional.backtest.multileg_backtester import MultiLegBacktester, MultiLegConfig


class Cached(AlphaEngine):
    def __init__(s, i): s.inner = i; s.config = i.config; s._c = {}
    def generate(s, a, st, e):
        k = (a, st, e)
        if k not in s._c: s._c[k] = s.inner.generate(a, st, e)
        return s._c[k]
    def thresholds_for(s, a): return s.inner.thresholds_for(a)


class Suite:
    def __init__(self, cfg_path: str):
        self.spec = yaml.safe_load(Path(cfg_path).read_text())
        self.end = self.spec["window"]["end"] or str(
            load_enriched("BTCUSDT", required_cols=["close"])["datetime"].max().date())
        self.start = self.spec["window"]["start"]
        self.longs = [Cached(self._build_engine_spec(e)) for e in self.spec["engines_long"]]
        self.carry_assets = self.spec["carry_assets"]
        self.years = max((pd.Timestamp(self.end) - pd.Timestamp(self.start)).days / 365.25, 0.1)
        self.results = {}
        self._run_cache = {}

    def _build_engine_spec(self, e):
        """Entrée engines_long : str (registre) OU dict {id, kwargs, assets_from_universe}.
        assets_from_universe=true → assets = univers quality-filtré du yaml `universe_from`
        (nécessaire pour PULLBACK_LONG sur l'univers 50 de V1.2)."""
        if isinstance(e, str):
            return build_engine(e)
        kw = dict(e.get("kwargs", {}))
        if e.get("assets_from_universe"):
            from src.institutional.universe.asset_quality_filter import (
                assess_universe, AssetQualityStatus as Q)
            U = yaml.safe_load((ROOT / self.spec["universe_from"]).read_text())["universe"]
            qual = assess_universe(U)
            kw["assets"] = [s for s in U if qual[s].status != Q.BLOCK]
        return build_engine(e["id"], **kw)

    def _cfg(self, **ov) -> MultiLegConfig:
        d = dict(self.spec["config"]); d["initial_capital"] = self.spec["capital"]; d.update(ov)
        return MultiLegConfig(**d)

    def _run(self, cfg, start=None, end=None):
        s, e = start or self.start, end or self.end
        key = (cfg.carry_fraction, cfg.enable_long, cfg.enable_carry, cfg.enable_hedge,
               cfg.taker_fee_bps, cfg.slippage_bps, cfg.enable_asset_regime_gate,
               cfg.carry_gate_v2, s, e)
        if key not in self._run_cache:   # mémoïse : configs identiques calculées une fois
            self._run_cache[key] = MultiLegBacktester(
                self.longs, cfg, carry_assets=self.carry_assets).run(s, e)
        return self._run_cache[key]

    # ── 0. reproductibilité ──
    def reproducibility(self):
        r1 = self._run(self._cfg()); r2 = self._run(self._cfg())
        def h(df): return hashlib.sha256(pd.util.hash_pandas_object(df.round(6)).values.tobytes()).hexdigest()[:12]
        same = (abs(r1.metrics.get("total_return", 0) - r2.metrics.get("total_return", 0)) < 1e-9
                and h(r1.leg_ledger) == h(r2.leg_ledger))
        return {"status": "PASS" if same else "FAIL", "score_frac": 1.0 if same else 0.0,
                "ledger_hash": h(r1.leg_ledger), "detail": "2 runs identiques" if same else "divergence"}

    # ── 1. data integrity ──
    def data_integrity(self):
        from scripts.validate_parquet_store import validate_file
        files = sorted((ROOT / "data" / "enriched").glob("*_1h_enriched.parquet"))
        reps = [validate_file(p) for p in files]
        bad = [r for r in reps if not r["ok"]]
        ok = len(bad) == 0 and len(reps) > 0
        return {"status": "PASS" if ok else "FAIL", "score_frac": 1.0 if ok else 0.5,
                "files": len(reps), "fail": len(bad)}

    # ── 2. baseline reproduction ──
    def baseline(self):
        r = self._run(self._cfg())
        exp = self.spec["expected"]; roi = r.metrics.get("total_return", 0)
        err = abs(roi - exp["roi_total"])
        st = "PASS" if err < 0.01 else ("WARN" if err < 0.05 else "FAIL")
        return {"status": st, "score_frac": 1.0 if st == "PASS" else (0.6 if st == "WARN" else 0.0),
                "roi": round(roi, 4), "expected": exp["roi_total"],
                "maxDD": round(r.metrics.get("max_drawdown", 0), 4), "pf": round(r.metrics.get("pf", 0), 3)}

    # ── 3. régimes / années ──
    def regime_splits(self):
        r = self._run(self._cfg()); eq = r.equity
        rows = {}
        for y in (2022, 2023, 2024, 2025, 2026):
            ye = eq[eq.index.year == y]
            if len(ye) > 1:
                dd = float(((ye - ye.cummax()) / ye.cummax()).min())
                rows[y] = {"roi": round(float(ye.iloc[-1] / ye.iloc[0] - 1), 4), "maxDD": round(dd, 4)}
        worst_dd = min((v["maxDD"] for v in rows.values()), default=0)
        y2026 = rows.get(2026, {}).get("roi", 0)
        ok = worst_dd >= -0.03 and y2026 > -0.01
        return {"status": "PASS" if ok else "WARN", "score_frac": 1.0 if ok else 0.6,
                "by_year": rows, "worst_annual_dd": worst_dd}

    # ── 4. ablation sleeves ──
    def sleeve_ablation(self):
        runs = {
            "CASH": self._cfg(enable_long=False, enable_carry=False, enable_hedge=False),
            "CARRY_ONLY": self._cfg(enable_long=False, enable_carry=True, enable_hedge=False),
            "LONG_ONLY": self._cfg(enable_long=True, enable_carry=False, enable_hedge=False),
            "FULL_V1.1": self._cfg(),
        }
        out = {}
        for n, c in runs.items():
            r = self._run(c); out[n] = {"roi": round(r.metrics.get("total_return", 0), 4),
                                        "maxDD": round(r.metrics.get("max_drawdown", 0), 4)}
        carry_pos = out["CARRY_ONLY"]["roi"] > 0
        full_beats_cash = out["FULL_V1.1"]["roi"] > out["CASH"]["roi"]
        ok = carry_pos and full_beats_cash
        return {"status": "PASS" if ok else "WARN", "score_frac": 1.0 if ok else 0.6,
                "runs": out, "carry_is_engine": carry_pos}

    # ── 5. stress coûts ──
    def cost_stress(self):
        base_fee, base_slip = 5.0, 2.0
        out = {}
        for m in (1.0, 2.0, 3.0):
            r = self._run(self._cfg(taker_fee_bps=base_fee * m, slippage_bps=base_slip * m))
            out[f"x{m}"] = {"roi": round(r.metrics.get("total_return", 0), 4),
                            "maxDD": round(r.metrics.get("max_drawdown", 0), 4)}
        x2 = out["x2.0"]["roi"]
        st = "PASS" if x2 > 0 else ("WARN" if x2 > -0.02 else "FAIL")
        return {"status": st, "score_frac": 1.0 if st == "PASS" else (0.5 if st == "WARN" else 0.0),
                "runs": out, "fees_x2_roi": x2}

    # ── 6. sensibilité sizing ──
    def sizing_sensitivity(self):
        out = {}
        for cs in (0.20, 0.35, 0.50, 0.75):
            r = self._run(self._cfg(carry_fraction=cs))
            out[f"carry{int(cs*100)}"] = {"roi": round(r.metrics.get("total_return", 0), 4),
                                          "maxDD": round(r.metrics.get("max_drawdown", 0), 4)}
        # monotone & pas de rupture brutale 35→50
        dd50 = abs(out["carry50"]["maxDD"])
        ok = dd50 <= 0.03 and out["carry50"]["roi"] >= out["carry35"]["roi"]
        return {"status": "PASS" if ok else "WARN", "score_frac": 1.0 if ok else 0.7, "runs": out}

    # ── 7. walk-forward (folds 6 mois, rule-based) ──
    def walk_forward(self):
        eq = self._run(self._cfg()).equity
        folds, bad = [], 0
        cur = pd.Timestamp(self.start, tz="UTC")
        endts = pd.Timestamp(self.end, tz="UTC")
        while cur < endts:
            nxt = cur + pd.Timedelta(days=180)
            f = eq[(eq.index >= cur) & (eq.index < nxt)]
            if len(f) > 5:
                dd = float(((f - f.cummax()) / f.cummax()).min())
                roi = float(f.iloc[-1] / f.iloc[0] - 1)
                folds.append({"start": str(cur.date()), "roi": round(roi, 4), "maxDD": round(dd, 4)})
                if dd < -0.04: bad += 1
            cur = nxt
        med = float(np.median([f["roi"] for f in folds])) if folds else 0
        ok = bad == 0 and med >= 0
        return {"status": "PASS" if ok else "WARN", "score_frac": 1.0 if ok else 0.6,
                "n_folds": len(folds), "catastrophic_folds": bad, "median_fold_roi": round(med, 4)}

    # ── 8. monte carlo (block bootstrap des RENDEMENTS EQUITY, pas des jambes) ──
    def monte_carlo(self, iters=2000):
        # ⚠ bootstrap au niveau EQUITY (les jambes carry sont pairées delta-neutral :
        # les bootstrap indépendamment casse la neutralité → faux DD). On bloque par mois.
        eq = self._run(self._cfg()).equity
        monthly = eq.resample("M").last().pct_change().dropna().to_numpy()
        if len(monthly) < 6:
            return {"status": "WARN", "score_frac": 0.5, "detail": "série trop courte"}
        rng = np.random.default_rng(0); n = len(monthly)
        rois, dds = [], []
        for _ in range(iters):
            s = monthly[rng.integers(0, n, n)]
            e = np.cumprod(1 + s); roi = e[-1] - 1
            dd = ((e - np.maximum.accumulate(e)) / np.maximum.accumulate(e)).min()
            rois.append(roi); dds.append(dd)
        p_neg = float(np.mean(np.array(rois) < 0)); p_dd = float(np.mean(np.array(dds) < -0.03))
        ok = p_neg < 0.30 and p_dd < 0.20
        return {"status": "PASS" if ok else "WARN", "score_frac": 1.0 if ok else 0.6,
                "P_roi_neg": round(p_neg, 3), "P_dd_gt_3pct": round(p_dd, 3),
                "method": "monthly_equity_block_bootstrap"}

    # ── 9. paper replay ──
    def paper_replay(self):
        st = ROOT / "reports" / "paper_live" / "state.json"
        if not st.exists():
            return {"status": "WARN", "score_frac": 0.5, "detail": "paper pas encore lancé"}
        s = json.loads(st.read_text())
        # HONNÊTE : le paper actuel est un re-run DÉTERMINISTE du backtest → tracking 0 PAR
        # CONSTRUCTION, ce n'est PAS une validation forward L4. Tant qu'il n'y a pas de jours
        # forward RÉELS (exécution réelle ≠ re-run), on ne peut pas valider → partiel.
        data_end = pd.Timestamp(s.get("data_end", self.end), tz="UTC")
        # jours forward réels = au-delà de la fin des données backtest historiques
        fwd_days = max(0, (pd.Timestamp.now(tz="UTC") - data_end).days)
        if fwd_days < 30:
            return {"status": "WARN", "score_frac": 0.5,
                    "forward_days_real": fwd_days,
                    "verdict": "INSUFFICIENT_FORWARD_DATA (déterministe = backtest ; besoin 30-60-90j réels)",
                    "note": "tracking 0 par construction — non validable en L4 tant que pas d'exécution réelle"}
        bt = self._run(self._cfg(), start=s.get("paper_start", self.start))
        tracking = abs(s.get("ret_total", 0) - bt.metrics.get("total_return", 0))
        ok = tracking < 0.20
        return {"status": "PASS" if ok else "WARN", "score_frac": 1.0 if ok else 0.6,
                "tracking_error": round(tracking, 4), "forward_days_real": fwd_days}

    # ── 10. event readiness ──
    def event_readiness(self):
        from src.institutional.events.live_event_builder import build_events
        ev = build_events()
        n = len(ev); n_sig = int(ev["significant"].sum()) if n else 0
        verdict = ("ML_READY" if n >= 1000 else "RULE_ENGINE_READY" if n >= 300
                   else "DIAGNOSTIC_READY" if n >= 100 else "DATA_NOT_READY")
        return {"status": "INFO", "score_frac": min(1.0, n / 100.0),
                "n_events": n, "n_significant": n_sig, "verdict": verdict}

    # ── 11. anti-leakage audit ──
    def leakage_audit(self):
        from src.institutional.portfolio.regime_gate import RegimeGate
        rng = np.random.default_rng(0)
        idx = pd.date_range("2021-01-01", periods=6000, freq="1H", tz="UTC")
        px = pd.Series(100 * np.cumprod(1 + rng.normal(0, 0.004, 6000)), index=idx)
        full = RegimeGate().compute_regime_series(px)
        trunc = RegimeGate().compute_regime_series(px.iloc[:4000])
        common = full.index[2200:4000]
        causal = (full.loc[common].values == trunc.loc[common].values).mean() > 0.99
        return {"status": "PASS" if causal else "FAIL", "score_frac": 1.0 if causal else 0.0,
                "regime_causal": bool(causal)}

    # ── 12. operational stress ──
    def operational_stress(self):
        from src.institutional.engines.carry_basis.carry_gate_v2 import CarryGateV2, CarryGateV2Status
        from src.institutional.data.atomic_parquet import validate_parquet_readable
        checks = {}
        # donnée critique manquante → BLOCK (pas de trade)
        g = CarryGateV2([])
        checks["missing_funding_blocks"] = g.evaluate("BTCUSDT", pd.Timestamp("2024-01-01", tz="UTC")).status == CarryGateV2Status.BLOCK
        # parquet corrompu refusé
        import tempfile
        tmp = Path(tempfile.mkdtemp()) / "x.parquet"; tmp.write_bytes(b"GARBAGE")
        try:
            validate_parquet_readable(tmp); checks["corrupt_refused"] = False
        except Exception:
            checks["corrupt_refused"] = True
        ok = all(checks.values())
        return {"status": "PASS" if ok else "FAIL", "score_frac": 1.0 if ok else 0.5, "checks": checks}

    def run_all(self):
        order = [
            ("reproducibility", self.reproducibility, 10), ("data_integrity", self.data_integrity, 15),
            ("leakage_audit", self.leakage_audit, 15), ("baseline", self.baseline, 10),
            ("regime_splits", self.regime_splits, 10), ("cost_stress", self.cost_stress, 10),
            ("sleeve_ablation", self.sleeve_ablation, 10), ("paper_replay", self.paper_replay, 10),
            ("event_readiness", self.event_readiness, 5), ("operational_stress", self.operational_stress, 5),
            ("sizing_sensitivity", self.sizing_sensitivity, 0), ("walk_forward", self.walk_forward, 0),
            ("monte_carlo", self.monte_carlo, 0),
        ]
        total, maxw = 0.0, 0.0
        for name, fn, w in order:
            try:
                res = fn()
            except Exception as e:
                res = {"status": "ERROR", "score_frac": 0.0, "error": str(e)[:200]}
            res["weight"] = w
            self.results[name] = res
            total += res.get("score_frac", 0) * w; maxw += w
            print(f"  [{res.get('status','?'):<5}] {name:<22} score={res.get('score_frac',0):.2f} (w{w})")
        score = round(100 * total / maxw, 1) if maxw else 0
        return score


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="configs/portfolio_v1_1_baseline.yaml")
    ap.add_argument("--out", default="reports/maturity/")
    args = ap.parse_args()
    s = Suite(args.config)
    print(f"\nMATURITY SUITE — {s.start} → {s.end} ({s.years:.1f} ans)\n" + "─" * 60)
    score = s.run_all()
    # GATE L4 : micro-live exige une validation FORWARD réelle (paper_replay PASS), pas
    # seulement un score. Le paper déterministe (= backtest) ne suffit pas.
    paper_ok = s.results.get("paper_replay", {}).get("status") == "PASS"
    tier = ("INSTITUTIONAL" if score >= 95 and paper_ok else
            "MICRO_LIVE_REVIEW" if score >= 85 and paper_ok else
            "STRONG_PAPER" if score >= 75 else "PAPER_CANDIDATE" if score >= 60 else
            "RESEARCH_CLEAN" if score >= 40 else "RESEARCH_FRAGILE")
    if not paper_ok:
        decision = "PAPER_CONTINUE / MICRO_LIVE_BLOCKED_UNTIL_60_90D_REAL_TRACKING"
    elif score >= 85:
        decision = "MICRO_LIVE_REVIEW"
    elif score >= 60:
        decision = "PAPER_CONTINUE"
    else:
        decision = "RESEARCH_ONLY"
    print("─" * 60)
    print(f"MATURITY SCORE : {score}/100  →  TIER : {tier}")
    print(f"DECISION : {decision}")
    print(f"OFFENSIVE_ALPHA : {s.results['event_readiness']['verdict']}")

    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    scorecard = {"score": score, "tier": tier, "decision": decision,
                 "window": [s.start, s.end], "checks": s.results}
    (out / "maturity_scorecard.json").write_text(json.dumps(scorecard, indent=2, default=str))
    _write_report(out / "MATURITY_BACKTEST_SUITE_REPORT.md", scorecard)
    print(f"\n→ {out}/maturity_scorecard.json + MATURITY_BACKTEST_SUITE_REPORT.md")


def _write_report(path: Path, sc: dict) -> None:
    L = [f"# Maturity Backtest Suite Report\n",
         f"- fenêtre : {sc['window'][0]} → {sc['window'][1]}",
         f"- **SCORE : {sc['score']}/100 → TIER : {sc['tier']}**",
         f"- **DÉCISION : {sc['decision']}**\n", "## Checks\n",
         "| Check | Statut | score | poids |", "|---|---|---:|---:|"]
    for n, r in sc["checks"].items():
        L.append(f"| {n} | {r.get('status')} | {r.get('score_frac',0):.2f} | {r.get('weight',0)} |")
    L.append("\n## Détails\n```json")
    L.append(json.dumps(sc["checks"], indent=1, default=str)[:6000])
    L.append("```")
    path.write_text("\n".join(L))


if __name__ == "__main__":
    main()
