"""
src/alpha20/tournament/dashboard.py — DASHBOARD quotidien MACHINE-READABLE.

Une ligne par runner, dérivée UNIQUEMENT du ledger append-only du runner + du
gate de réconciliation + du log de disponibilité opérationnel. Rien n'est
inventé : tout champ non calculable faute d'historique suffisant est `null`
avec un motif explicite (même discipline qu'ES99 en R0).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from src.alpha20 import ROOT
from src.alpha20.accounting import net_nav
from src.alpha20.tournament import reconciliation
from src.alpha20.tournament import orchestrator
from src.alpha20.tournament.paper_account import PaperAccount
from src.alpha20.tournament.runner_registry import RunnerSpec, runnable_specs
from src.alpha20.validation.promotion_gate import deflated_sharpe_ratio

OUT_DIR = ROOT / "reports" / "alpha20" / "tournament"
MIN_HISTORY_FOR_STATS = 30


def _sharpe_sortino(daily_ret: pd.Series) -> Dict[str, Optional[float]]:
    if len(daily_ret) < MIN_HISTORY_FOR_STATS:
        return {"sharpe_ann": None, "sortino_ann": None,
                "note": f"< {MIN_HISTORY_FOR_STATS} points"}
    mu, sd = daily_ret.mean(), daily_ret.std(ddof=1)
    down = daily_ret[daily_ret < 0]
    dsd = down.std(ddof=1) if len(down) > 1 else np.nan
    return {"sharpe_ann": round(float(mu / sd * np.sqrt(365)), 3) if sd > 0 else None,
            "sortino_ann": round(float(mu / dsd * np.sqrt(365)), 3)
            if dsd and dsd > 0 else None}


def _es(daily_ret: pd.Series, q: float) -> Optional[float]:
    if len(daily_ret) < 100:
        return None
    var = np.quantile(daily_ret, 1 - q)
    tail = daily_ret[daily_ret <= var]
    return round(float(-tail.mean()) if len(tail) else float(-var), 5)


def _pf_expectancy(pnl: pd.Series) -> Dict[str, Optional[float]]:
    if len(pnl) == 0:
        return {"profit_factor": None, "expectancy_usdt": None}
    pos, neg = pnl[pnl > 0].sum(), abs(pnl[pnl < 0].sum())
    return {"profit_factor": round(float(pos / neg), 3) if neg > 0 else None,
            "expectancy_usdt": round(float(pnl.mean()), 4)}


def _availability(runner_id: str) -> Dict:
    p = orchestrator.CYCLE_LOG_DIR / f"{runner_id}.jsonl"
    if not p.exists():
        return {"n_cycles": 0, "availability": None}
    rows = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
    ok = sum(1 for r in rows if r["status"] == "ok")
    return {"n_cycles": len(rows), "availability": round(ok / len(rows), 4)
            if rows else None}


def _weekly_regime_asset_cuts(account: PaperAccount) -> Dict:
    marks = account.read(kinds=["mark"])
    fills = account.read(kinds=["fill"])
    out: Dict = {"by_week": {}, "by_regime": {}, "by_asset": {}}
    if len(marks) >= 2:
        marks = marks.sort_values("ts")
        h = pd.Series(marks["meta"].apply(lambda m: (m or {}).get("nav_usdt")).values,
                      index=pd.to_datetime(marks["ts"], utc=True)).dropna().astype(float)
        ret = h.pct_change().dropna()
        if len(ret):
            out["by_week"] = {str(k): round(float((1 + v).prod() - 1), 5)
                              for k, v in ret.groupby(ret.index.to_period("W"))}
        regimes = pd.Series(marks["meta"].apply(lambda m: (m or {}).get("regime")).values,
                            index=pd.to_datetime(marks["ts"], utc=True))
        joined = pd.DataFrame({"ret": h.pct_change(), "regime": regimes}).dropna()
        if len(joined):
            out["by_regime"] = {str(k): round(float((1 + g["ret"]).prod() - 1), 5)
                                for k, g in joined.groupby("regime")}
    if len(fills):
        sym = fills["meta"].apply(lambda m: (m or {}).get("symbol"))
        joined = pd.DataFrame({"pnl": fills["amount_usdt"], "symbol": sym}).dropna()
        if len(joined):
            out["by_asset"] = {str(k): round(float(v), 2) for k, v in
                               joined.groupby("symbol")["pnl"].sum().items()}
    return out


def _cost_scenarios(account: PaperAccount) -> Dict:
    """Moyenne des frais (bp) par scénario simulé simultanément (robustesse,
    jamais utilisée pour ajuster une décision) — lue dans les décisions."""
    dec = account.read(kinds=["decision"])
    if dec.empty:
        return {}
    acc: Dict[str, list] = {}
    for meta in dec["meta"]:
        for key in ("scenarios", "scenarios_spot", "scenarios_quarterly"):
            scn = (meta or {}).get(key)
            if not scn:
                continue
            for name, v in scn.items():
                acc.setdefault(name, []).append(v.get("fee_bp"))
    return {name: round(float(np.mean([x for x in vals if x is not None])), 3)
            for name, vals in acc.items() if vals}


def runner_row(spec: RunnerSpec, n_trials_dsr: int) -> Dict:
    account = PaperAccount(spec.runner_id, spec.capital_standalone_eur)
    op_state = orchestrator.load_state(spec.runner_id)
    h = account.nav_history()
    ret = account.daily_returns()
    r_net = net_nav.r_net(spec.capital_standalone_eur,
                          since="1970-01-01", ledger_dir=account.ledger_dir)
    fills = account.read(kinds=["fill"])
    decisions = account.read(kinds=["decision"])
    rejects = account.read(kinds=["reject"])
    gate = reconciliation.runner_gate(spec.runner_id, spec.capital_standalone_eur)
    dsr = (deflated_sharpe_ratio(ret, n_trials_dsr) if len(ret) >= MIN_HISTORY_FOR_STATS
          else None)
    maxdd = float(-((h - h.cummax()) / h.cummax()).min()) if len(h) >= 2 else 0.0

    return {
        "runner_id": spec.runner_id, "family": spec.family, "status": spec.status,
        "config_hash": spec.config_hash, "git_commit": spec.git_commit,
        "age_days": round(account.age_days(), 2), "n_events": account.n_events(),
        "n_decisions": int(len(decisions)), "n_fills": int(len(fills)),
        "n_rejects": int(len(rejects)),
        "availability": _availability(spec.runner_id),
        "exposure": {"gross_usdt": op_state.get("gross_usdt"),
                    "margin_used_frac": round(op_state.get("gross_usdt", 0.0) * 0.10
                                              / max(account.nav_usdt(), 1.0), 4)
                                        if op_state.get("gross_usdt") else 0.0,
                    "source": "état opérationnel (cache), proxy marge IM 10%"},
        "nav_usdt": round(account.nav_usdt(), 2),
        "pnl_gross_usdt": round(r_net.get("r_gross", 0.0) * spec.capital_standalone_eur, 2)
                          if r_net.get("by_kind") else 0.0,
        "costs": {k: round(v, 2) for k, v in r_net.get("by_kind", {}).items()
                 if k in ("fee", "borrow", "gas", "infra", "transfer")},
        "tax_provision_usdt": round(r_net.get("tax_drag", 0.0)
                                    * spec.capital_standalone_eur, 2),
        "pnl_net_before_tax_usdt": round((r_net.get("r_gross", 0.0)
                                          - r_net.get("cost_drag", 0.0))
                                         * spec.capital_standalone_eur, 2),
        "pnl_net_after_tax_usdt": round(r_net.get("r_net", 0.0)
                                        * spec.capital_standalone_eur, 2),
        "return_total": round(r_net.get("r_net", 0.0), 5),
        "max_drawdown": round(maxdd, 5),
        **_sharpe_sortino(ret),
        "dsr": round(dsr, 4) if dsr is not None else None,
        "dsr_n_trials": n_trials_dsr,
        "es95_1d": _es(ret, 0.95), "es99_1d": _es(ret, 0.99),
        **_pf_expectancy(fills["amount_usdt"] if len(fills) else pd.Series(dtype=float)),
        "capacity_eur": None,             # non mesurée (nécessite profondeur L2 réelle)
        "execution_quality": {
            "mean_execution_shortfall_bps": round(float(
                fills["meta"].apply(lambda m: (m or {}).get("execution_shortfall"))
                .dropna().mean() * 1e4), 2)
                if len(fills) and fills["meta"].apply(
                    lambda m: (m or {}).get("execution_shortfall")).notna().any()
                else None,
        },
        "reconciliation": {"status": gate["status"], "passed": gate["passed"],
                           "consecutive_ok": gate["consecutive_ok"],
                           "eligible": gate.get("eligible")},
        "cuts": {**_weekly_regime_asset_cuts(account),
                "by_cost_scenario_fee_bp": _cost_scenarios(account)},
    }


def build_dashboard() -> List[Dict]:
    specs = runnable_specs()
    n_trials = max(len(specs), 1)
    return [runner_row(s, n_trials) for s in specs]


def write_daily(rows: List[Dict]) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    date = pd.Timestamp.now(tz="UTC").date().isoformat()
    path = OUT_DIR / f"DASHBOARD_{date}.json"
    path.write_text(json.dumps({"date": date, "runners": rows}, indent=2, default=str))
    (OUT_DIR / "DASHBOARD_latest.json").write_text(
        json.dumps({"date": date, "runners": rows}, indent=2, default=str))
    return path


if __name__ == "__main__":
    from src.alpha20.deployment_guard import assert_deployment_matches_approved
    from src.alpha20.guard import assert_paper_only
    assert_paper_only()
    assert_deployment_matches_approved()
    rows = build_dashboard()
    p = write_daily(rows)
    for r in rows:
        print(f"{r['runner_id']}: nav={r['nav_usdt']} net_after_tax="
              f"{r['pnl_net_after_tax_usdt']} dd={r['max_drawdown']:.2%} "
              f"reconciliation={r['reconciliation']['status']}")
    print(f"-> {p}")
