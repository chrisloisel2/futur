#!/usr/bin/env python3
"""W3 — genere la table markdown du gate §2 a partir de RESULTS.json (aucun chiffre saisi a la main)."""
import json, os
D = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ms = json.load(open(os.path.join(D, "RESULTS.json")))["mechanisms"]


def label(mid):
    return "`" + mid.replace("_LIST_", "_").replace("_XSEC_", "_").replace("_LISTING_", "_")\
                     .replace("_AGE_", "_").replace("_DELIST_", "_").replace("_WAVE_", "_") + "`"

def f(x, d=1, plus=False):
    if x is None: return "—"
    if isinstance(x, str): return x
    s = f"{x:+.{d}f}" if plus else f"{x:.{d}f}"
    return s.replace(".0", "") if d == 0 else s


rows = []
for m in ms:
    ci = m.get("bootstrap_ci95")
    ci_s = f"[{ci[0]:.0f} ; {ci[1]:.0f}]" if ci and ci[0] is not None else "—"
    eb = m.get("ex_best_year") or {}
    eb_s = (f"{eb.get('dropped')} → {eb['gross_bps']:+.0f}" if eb.get("gross_bps") is not None else "—")
    eta = (m.get("eta_forward_confirmation") or {}).get("years")
    def fmt_eta(y):
        return f"{y:,.0f}".replace(",", " ") if y >= 100 else f"{y:.2f}"
    if eta is None and m.get("hitrate_eta_years") is not None:
        eta_s = f"{m['hitrate_eta_years']:.2f} (taux)"
    elif eta is None and m.get("event_rate_per_week_6m") == 0.0:
        eta_s = "∞"
    elif eta is None:
        eta_s = "—"
    else:
        eta_s = fmt_eta(eta)
    rows.append("| " + " | ".join([
        label(m["id"]),
        f"`{m['verdict']}`",
        f(m.get("n_raw"), 0),
        f(m.get("n_independent_L1"), 0),
        f(m.get("n_independent_L2"), 0),
        f"**{f(m.get('n_independent_L3'), 0)}**",
        f(m.get("net_bps"), 1, plus=True),
        f(m.get("net_bps_stress28"), 1, plus=True),
        f"**{f(m.get('t_stat_declustered'), 2, plus=True)}**",
        ci_s,
        m.get("years_same_sign") or "—",
        eb_s,
        (f"{m['n_required']:,.0f}".replace(",", " ") if m.get("n_required") is not None
         else (f"{m['hitrate_n_required']:,.0f}".replace(",", " ") if m.get("hitrate_n_required") is not None else "—")),
        f(m.get("event_rate_per_week_6m"), 3),
        f"**{eta_s}**",
        f(m.get("t_stat_needed_for_eta_3y"), 2),
    ]) + " |")

hdr = ("| mécanisme | verdict | n_raw | L1 | L2 | **L3** | net | net_stress | **t_L3** | IC95 bootstrap | "
       "ans même signe | ex_best_year | n_req | ép./sem. | **ETA (ans)** | t_req 3a |")
sep = "|" + "---|" * 16
print("\n".join([hdr, sep] + rows))
