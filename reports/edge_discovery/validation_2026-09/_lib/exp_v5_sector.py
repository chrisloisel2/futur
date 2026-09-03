"""V5 — SECTOR_ROTATION + SECTOR_RELATIVE_STRENGTH_REVERSAL.

Exécute la spec de ../SECTOR_ROTATION/PREREGISTRATION.md : rotation sectorielle 7 j
(continuation, tiers haut de secteurs) et force relative intra-secteur (reversal),
avec les 8 perturbations préenregistrées et le contrôle « même facteur ? » obligatoire
contre le momentum cross-sectionnel.
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np
import pandas as pd
from scipy import stats as sps

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import validation_lib as vl          # noqa: E402
import run_xsec_family as rf         # noqa: E402
import sector_map_v5 as smap         # noqa: E402

OUT = "/home/qbee/futur/reports/edge_discovery/validation_2026-09/_lib/out"


def sector_signal(sector_of: dict, form_days: int = 7, min_members: int = 3,
                  drop_other: bool = False, reversal: bool = False):
    """Score de panier sectoriel (continuation) ou écart au panier (reversal).

    Un secteur doit avoir >= min_members membres éligibles ce jour-là, sinon il est
    écarté (jamais fusionné silencieusement). BTC/ETH sont exclus du classement.
    """
    def f(xp, d, elig):
        prev = d - pd.Timedelta(days=form_days)
        if prev not in xp.close.index:
            return pd.Series(np.nan, index=elig)
        use = [s for s in elig if s not in smap.EXCLUDED_FROM_RANKING]
        if drop_other:
            use = [s for s in use if s in sector_of]
        if len(use) < 20:
            return pd.Series(np.nan, index=elig)
        ret = xp.close.loc[d, use] / xp.close.loc[prev, use] - 1.0
        sec = pd.Series({s: sector_of.get(s, "OTHER") for s in use})
        counts = sec.value_counts()
        keep = counts[counts >= min_members].index
        mask = sec.isin(keep)
        ret, sec = ret[mask.values], sec[mask.values]
        if len(ret) < 20:
            return pd.Series(np.nan, index=elig)
        basket = ret.groupby(sec).mean()
        if reversal:
            out = ret - sec.map(basket)          # force relative intra-secteur
        else:
            out = sec.map(basket)                # rotation : le nom hérite du secteur
        return out.reindex(elig)
    return f


def gate(runs, col="excess_gross_bps", n_legs=1, min_days=182, **kw):
    return rf.gate_from_runs(runs, column=col, n_legs=n_legs,
                             minimum_calendar_days=min_days, **kw)


def main():
    xp, onboard, _ = rf.load_panel()
    base = vl.XSecConfig(formation_days=7, holding_days=7, quantile=1 / 3)
    res = {}

    print("[SECTOR_ROTATION]", flush=True)
    primary = vl.run_xsec(xp, base, sector_signal(smap.SECTOR_OF))
    print(f"  PRIMARY periods={len(primary)}", flush=True)
    R = {
        "PRIMARY_excess": gate(primary),
        "PRIMARY_raw": gate(primary, "raw_gross_bps"),
        "P4_ex2021": gate(primary, exclude_years=[2021]),
        "P5_cost150pct": gate(primary, cost_multiplier=1.5),
    }
    p1 = vl.run_xsec(xp, base, sector_signal(smap.COARSE_OF))
    R["P1_coarse_map"] = gate(p1)
    p2 = vl.run_xsec(xp, base, sector_signal(smap.SECTOR_OF, min_members=5))
    R["P2_min5_members"] = gate(p2)
    p3 = vl.run_xsec(xp, base, sector_signal(smap.SECTOR_OF, drop_other=True))
    R["P3_drop_OTHER"] = gate(p3)
    p7 = vl.run_xsec(xp, vl.XSecConfig(formation_days=7, holding_days=7, quantile=1 / 3,
                                       liquidity_floor=2_000_000.0),
                     sector_signal(smap.SECTOR_OF))
    R["P7_floor2M"] = gate(p7)
    p8 = vl.run_xsec(xp, vl.XSecConfig(formation_days=7, holding_days=7, quantile=0.20),
                     sector_signal(smap.SECTOR_OF))
    R["P8_quintile"] = gate(p8)
    anchors = []
    for a in range(7):
        r = vl.run_xsec(xp, vl.XSecConfig(formation_days=7, holding_days=7,
                                          quantile=1 / 3, anchor=a),
                        sector_signal(smap.SECTOR_OF))
        if not r.empty:
            anchors.append(round(float(r["excess_gross_bps"].mean()), 2))
    R["P6_anchors"] = {"per_anchor": anchors, "pooled": round(float(np.mean(anchors)), 2),
                       "n_positive": int(sum(1 for x in anchors if x > 0)), "n": len(anchors)}
    res["SECTOR_ROTATION"] = R

    print("[SECTOR_RELATIVE_STRENGTH_REVERSAL]", flush=True)
    rev_cfg = vl.XSecConfig(formation_days=7, holding_days=7, quantile=0.20, descending=False)
    rev = vl.run_xsec(xp, rev_cfg, sector_signal(smap.SECTOR_OF, reversal=True))
    print(f"  PRIMARY periods={len(rev)}", flush=True)
    V = {
        "PRIMARY_excess": gate(rev),
        "PRIMARY_raw": gate(rev, "raw_gross_bps"),
        "P4_ex2021": gate(rev, exclude_years=[2021]),
        "P5_cost150pct": gate(rev, cost_multiplier=1.5),
    }
    rp1 = vl.run_xsec(xp, rev_cfg, sector_signal(smap.COARSE_OF, reversal=True))
    V["P1_coarse_map"] = gate(rp1)
    res["SECTOR_RELATIVE_STRENGTH_REVERSAL"] = V

    # ── même facteur ? contre le momentum 7 j ─────────────────────────────
    print("[same-factor vs momentum]", flush=True)
    mom7 = vl.run_xsec(xp, vl.XSecConfig(formation_days=7, holding_days=7, quantile=1 / 3),
                       rf.sig_momentum(7))
    port = pd.DataFrame({
        "sector_rotation": primary.set_index("date")["excess_gross_bps"],
        "sector_reversal": rev.set_index("date")["excess_gross_bps"],
        "mom7": mom7.set_index("date")["excess_gross_bps"],
    }).dropna()
    corr = json.loads(port.corr().round(3).to_json())

    rank = {"sector_score_vs_mom7": [], "sector_reversal_vs_mom7": []}
    fs, fr, fm = (sector_signal(smap.SECTOR_OF), sector_signal(smap.SECTOR_OF, reversal=True),
                  rf.sig_momentum(7))
    em = xp.eligibility(1_000_000.0)
    for d in xp.grid(base):
        e = em.columns[em.loc[d].to_numpy()]
        if len(e) < 20:
            continue
        a, b, m = fs(xp, d, e), fr(xp, d, e), fm(xp, d, e)
        for key, (u, v) in {"sector_score_vs_mom7": (a, m),
                            "sector_reversal_vs_mom7": (b, m)}.items():
            df = pd.concat([u, v], axis=1).dropna()
            if len(df) >= 20:
                rank[key].append(float(sps.spearmanr(df.iloc[:, 0], df.iloc[:, 1]).statistic))
    res["same_factor_checks"] = {
        "rank_corr": {k: {"mean": round(float(np.mean(v)), 3), "std": round(float(np.std(v)), 3),
                          "n": len(v)} for k, v in rank.items() if v},
        "portfolio_return_corr": corr,
        "n_common_periods": int(len(port)),
        "mom7_reference_excess": gate(mom7),
    }

    os.makedirs(OUT, exist_ok=True)
    with open(f"{OUT}/v5_raw.json", "w") as f:
        json.dump(res, f, indent=2, default=str)

    print("\n=== SYNTHÈSE V5 ===")
    for cand in ("SECTOR_ROTATION", "SECTOR_RELATIVE_STRENGTH_REVERSAL"):
        print(f"\n{cand}")
        for k, v in res[cand].items():
            if isinstance(v, dict) and "net_bps" in v:
                print(f"  {k:22s} net={v['net_bps']:9.2f} net28={v['net_bps_stress28']:9.2f} "
                      f"t_L3={str(v['t_stat_declustered']):>7s} L3={v['n_independent_L3']:4d} "
                      f"p05={v['bootstrap_p05']:9.2f} yrs+={v['n_years_positive']}/{v['n_years']}")
    print("\nP6 anchors:", res["SECTOR_ROTATION"]["P6_anchors"])
    print("same-factor:", json.dumps(res["same_factor_checks"]["rank_corr"]))
    print("portfolio corr:", json.dumps(corr))
    m7 = res["same_factor_checks"]["mom7_reference_excess"]
    print(f"mom7 reference: net={m7['net_bps']} t_L3={m7['t_stat_declustered']}")


if __name__ == "__main__":
    main()
