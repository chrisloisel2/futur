"""Writing results out: metrics.json, the parquets, and verdict.md.

Non-finite numbers are written as ``null`` with an explicit note rather than as
``Infinity``: an infinite profit factor is a statement about sample size, and it
should not be readable as a performance number.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

import pandas as pd

from research_kernel.verdict import Verdict


def json_safe(obj: Any) -> Any:
    if isinstance(obj, Mapping):
        return {k: json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [json_safe(v) for v in obj]
    if isinstance(obj, float):
        if math.isnan(obj):
            return None
        if math.isinf(obj):
            return None
    return obj


def write_json(payload: Any, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(json_safe(payload), fh, indent=2, sort_keys=True, default=str)
    return path


def write_parquet(df: pd.DataFrame, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
    return path


def _fmt(x: Any, nd: int = 2) -> str:
    if x is None:
        return "n/a"
    if isinstance(x, float):
        if math.isnan(x):
            return "n/a"
        if math.isinf(x):
            return "infinite"
        return ("%%.%df" % nd) % x
    return str(x)


def render_verdict_md(
    verdict: Verdict,
    metrics: Dict[str, Any],
    placebos: Sequence[Dict[str, Any]],
    cost_rows: Sequence[Dict[str, Any]],
    spec_summary: Dict[str, Any],
    quality: Optional[Dict[str, Any]] = None,
) -> str:
    lines: List[str] = []
    a = lines.append

    a("# %s — %s" % (verdict.mechanism_id, verdict.status.value))
    a("")
    a(verdict.decision or "(no decision recorded)")
    a("")
    a("## Hypothesis")
    a("")
    a(spec_summary.get("hypothesis", ""))
    a("")
    a("**Why it should exist.** %s" % spec_summary.get("economic_reason", ""))
    a("")
    a(
        "Family `%s`, horizon `%s`, side `%s`, universe of %d symbols."
        % (
            spec_summary.get("multiplicity_family", ""),
            spec_summary.get("horizon", ""),
            spec_summary.get("side_mode", ""),
            len(spec_summary.get("universe", []) or []),
        )
    )
    a("")

    a("## Numbers")
    a("")
    a("| quantity | value |")
    a("| --- | --- |")
    a("| gross edge | %s bps |" % _fmt(metrics.get("gross_edge_bps")))
    a("| cost | %s bps |" % _fmt(metrics.get("cost_bps")))
    a("| net edge | %s bps |" % _fmt(metrics.get("net_edge_bps")))
    a("| net edge at double cost | %s bps |" % _fmt(metrics.get("net_edge_bps_cost_x2")))
    a("| breakeven capture | %s |" % _fmt(metrics.get("breakeven_capture")))
    a("| profit factor | %s |" % _fmt(metrics.get("profit_factor"), 3))
    a("| profit factor at double cost | %s |" % _fmt(metrics.get("profit_factor_cost_x2"), 3))
    a("| Sharpe | %s |" % _fmt(metrics.get("sharpe"), 2))
    a("| max drawdown | %s bps |" % _fmt(metrics.get("max_drawdown_bps"), 0))
    a("| decisions | %s |" % _fmt(metrics.get("n_raw"), 0))
    a("| independent episodes | %s |" % _fmt(metrics.get("n_independent"), 0))
    a("| t on gross | %s |" % _fmt(metrics.get("t_stat"), 3))
    a("| threshold for this family | %s |" % _fmt(verdict.multiplicity_adjusted_threshold, 3))
    a("| placebo percentile (worst) | %s |" % _fmt(verdict.placebo_percentile, 1))
    a("")
    a(
        "The t is computed on the gross series. The same t on the net series would be "
        "%s, which measures the cost constant rather than the signal."
        % _fmt(metrics.get("t_stat_net_artefact"), 3)
    )
    a("")

    a("## Gates")
    a("")
    for g in verdict.passed_gates:
        a("- passed — %s" % g)
    for g in verdict.failed_gates:
        a("- FAILED — %s" % g)
    a("")

    if cost_rows:
        a("## Cost sensitivity")
        a("")
        a("| multiplier | cost bps | net bps |")
        a("| --- | --- | --- |")
        for r in cost_rows:
            a(
                "| %sx | %s | %s |"
                % (_fmt(r["cost_multiplier"], 1), _fmt(r["cost_bps"]), _fmt(r["net_edge_bps"]))
            )
        a("")

    if placebos:
        a("## Placebo")
        a("")
        a("| test | status | observed | null 2.5% | null 97.5% | percentile |")
        a("| --- | --- | --- | --- | --- | --- |")
        for p in placebos:
            a(
                "| %s | %s | %s | %s | %s | %s |"
                % (
                    p.get("test"),
                    p.get("status"),
                    _fmt(p.get("observed_bps")),
                    _fmt(p.get("null_p2_5_bps")),
                    _fmt(p.get("null_p97_5_bps")),
                    _fmt(p.get("percentile"), 1),
                )
            )
        a("")
        a(
            "The reference is the null interval of this design, not zero: a design that "
            "produces plus or minus nine basis points on random baskets has not shown "
            "anything at plus five."
        )
        a("")

    if quality:
        a("## Data quality")
        a("")
        a("- rows: %s" % _fmt(quality.get("n_rows"), 0))
        for v in quality.get("violations", []) or []:
            a("- VIOLATION — %s" % v)
        for w in quality.get("warnings", []) or []:
            a("- warning — %s" % w)
        a("")

    a("## Provenance")
    a("")
    a("- rules hash: `%s`" % verdict.rules_hash)
    a("- kernel version: %s" % verdict.kernel_version)
    a("- code commit: `%s`" % verdict.code_commit_sha)
    a("- run recorded at: %s" % verdict.created_at)
    a("- forward seal: %s" % (verdict.forward_seal_id or "none"))
    if verdict.data_manifests:
        a("- data manifests: %s" % ", ".join(verdict.data_manifests))
    for w in verdict.warnings:
        a("- warning: %s" % w)
    a("")
    return "\n".join(lines)


def write_verdict_md(text: str, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path
