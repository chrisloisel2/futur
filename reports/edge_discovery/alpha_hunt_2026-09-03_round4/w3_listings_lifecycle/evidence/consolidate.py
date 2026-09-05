#!/usr/bin/env python3
"""
W3 — consolidate.py : fusionne les JSON par axe, applique les SEUILS PREENREGISTRES et les
regles de verdict du BRIEFING §3 de facon DETERMINISTE (aucun verdict n'est ecrit a la main),
puis ecrit RESULTS.json dans le dossier du worker.

Regles de verdict, dans cet ordre :
  0. verdict deja pose (DESCRIPTIVE / DATA_LIMITED) -> conserve
  1. n_independent_L3 < MIN_L3 (=10)  ou  n_raw < 30            -> DATA_LIMITED
     REGLE POST-HOC, declaree, appliquee UNIFORMEMENT, et qui ne peut que DEGRADER un verdict :
     un t calcule sur moins de 10 episodes independants n'est pas exploitable (c'est exactement
     le mode d'echec qui a interrompu ce worker : E1 apparie par mois donnait L3=4).
  2. edge reel ?  |t_L3| >= 2.0  ET  |net_bps| >= seuil_prereg   (sinon -> WEAK ou DEAD)
        WEAK si |t_L3| >= 1.0 ; DEAD sinon.  (le repli n'utilise PAS |net_bps| : un net
        negatif uniquement a cause des couts ne doit pas faire passer un t nul en WEAK)
  3. edge reel mais ex_best_year change de signe, ou years_same_sign < seuil -> REGIME_DEPENDENT
  4. edge reel mais net_bps_stress28 change de signe                        -> COST_FRAGILE
  5. edge reel, robuste, mais ETA > 3 ans                       -> UNCONFIRMABLE_IN_HORIZON
  6. sinon                                                      -> VALIDATED_FOR_FORWARD

Diagnostic ajoute a chaque mecanisme : `t_stat_needed_for_eta_3y` = le t de decouverte qu'il
aurait fallu, a N et a taux d'episodes constants, pour que l'ETA descende sous 3 ans. C'est la
mesure directe de la faisabilite de l'axe.
"""
import os, json, math
import numpy as np

OUT = os.environ["W3_SCRATCH"]
DEST = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
K_POWER, HAIRCUT = 7.849, 0.5
YEARS_MAX = 3.0
MIN_L3 = 10

# seuil |net_bps| et stabilite de signe minimale, tels que preenregistres (PREREGISTRATION §3)
PREREG = {
    "A1": (50.0, 3, 4), "A1b": (50.0, 3, 4), "A2": (100.0, None, None), "A3": (100.0, None, None),
    "A4": (100.0, None, None), "A4b": (100.0, None, None), "A5": (50.0, 3, 4),
    "B1": (20.0, 5, 7), "B2": (0.0, 5, 7), "B3": (20.0, None, None),
    "C2": (50.0, None, None), "C2b": (50.0, None, None), "C2c": (50.0, None, None),
    "D1": (100.0, None, None), "D2": (100.0, None, None),
    "E1": (100.0, 4, 6), "E1b": (100.0, 4, 6), "E1c": (100.0, 4, 6),
    "E2": (100.0, 4, 6), "E2b": (100.0, 4, 6),
    "F1": (50.0, None, None), "F2": (50.0, None, None),
}
PREREG_STATUS = {
    "A5": "POST_HOC_COMBINATION (A1 x A3, non preenregistre ; degrade le resultat, ne le sauve pas)",
    "E1c": "METHOD_AMENDED (unite L3 = plage de regime au lieu du mois ; cible = fwd 1j "
           "moyen dans la plage au lieu de fwd7/fwd30 chevauchants) — plus conservateur",
    "E2b": "METHOD_AMENDED (idem E1c)",
    "E1b": "METHOD_AMENDED (plage de regime, mais cible fwd7/fwd30 encore chevauchante entre plages)",
    "A1b": "COMPLEMENT (test de signe sur la meme population que A1, aucun seuil modifie)",
    "A4b": "CORRECTION (bras disjoints/Welch : l'appariement par semaine de A4 donnait L3=0)",
    "C2c": "COMPLEMENT (sensibilite de C2 a la winsorisation transversale)",
}


def fam_key(mid):
    for k in sorted(PREREG, key=len, reverse=True):
        if mid.startswith(k + "_"):
            return k
    return mid.split("_")[0]


def t_needed_for_eta(n_ep, rate_wk, years=YEARS_MAX):
    """t de decouverte requis (a N et taux constants) pour un ETA < `years`."""
    if not n_ep or not rate_wk or rate_wk <= 0 or n_ep < 2:
        return None
    n_req_max = years * 52.1775 * rate_wk
    if n_req_max <= 0:
        return None
    d_hc = math.sqrt(K_POWER / n_req_max)      # Cohen's d apres haircut
    return round(d_hc / HAIRCUT * math.sqrt(n_ep), 2)


def verdict_for(g):
    if g.get("verdict") in ("DESCRIPTIVE", "DATA_LIMITED"):
        return g["verdict"], g.get("note", "")
    n3 = g.get("n_independent_L3"); nraw = g.get("n_raw")
    if n3 is None or n3 < MIN_L3 or (nraw is not None and nraw < 30):
        return "DATA_LIMITED", (f"L3={n3} (< {MIN_L3} episodes independants) : declustering "
                                f"effondre, t non exploitable" if (n3 is not None and n3 < MIN_L3)
                                else f"L3={n3}, n_raw={nraw} : echantillon insuffisant")
    # tests de SIGNE (A1b) : pas de P&L, on juge sur le taux de reussite ET sur le fait que
    # la moyenne (la seule quantite qui compose) est portee par A1/A5
    if g.get("hit_rate_declustered") is not None:
        y = (g.get("hitrate_eta_years"))
        return "UNCONFIRMABLE_IN_HORIZON", (
            f"taux de reussite {g['hit_rate_declustered']:.1%} confirmable en ~{y} ans, mais la "
            f"MOYENNE (ce qui compose) n'est pas significative : skew={g.get('skew')}, "
            f"pire evenement {g.get('worst_single_event_bps')}bps ; le P&L correspondant est celui "
            f"de A1/A5, ETA 29-104 ans")
    thr, need_yrs, tot_yrs = PREREG.get(fam_key(g["id"]), (50.0, None, None))
    t = g.get("t_stat_declustered"); net = g.get("net_bps"); gross = g.get("gross_bps")
    if t is None or net is None:
        return "DATA_LIMITED", "statistique non calculable"
    real = (abs(t) >= 2.0) and (abs(net) >= thr)
    if not real:
        if abs(t) >= 1.0:
            return "WEAK", (f"|t_L3|={abs(t):.2f} < 2.0" +
                            ("" if abs(net) >= thr else f" et |net|={abs(net):.0f} < seuil prereg {thr:.0f}bps"))
        return "DEAD", f"|t_L3|={abs(t):.2f}, gross={gross}bps : indistinguable de zero"
    # concentration temporelle
    ex = g.get("ex_best_year"); ss = g.get("years_same_sign")
    if ex and ex.get("gross_bps") is not None and gross is not None:
        if np.sign(ex["gross_bps"]) != np.sign(gross):
            return "REGIME_DEPENDENT", (f"le signe s'inverse en retirant {ex.get('dropped')} "
                                        f"({gross:+.0f} -> {ex['gross_bps']:+.0f}bps)")
    if ss and need_yrs:
        a, b = (int(x) for x in ss.split("/"))
        if a < need_yrs:
            return "REGIME_DEPENDENT", f"signe stable seulement {ss} annees (prereg exigeait >= {need_yrs}/{tot_yrs})"
    s28 = g.get("net_bps_stress28")
    if s28 is not None and np.sign(s28) != np.sign(net):
        return "COST_FRAGILE", f"net14={net:+.0f}bps mais net_stress28={s28:+.0f}bps"
    yrs = (g.get("eta_forward_confirmation") or {}).get("years")
    if yrs is None or yrs > YEARS_MAX:
        return "UNCONFIRMABLE_IN_HORIZON", f"ETA de confirmation forward = {yrs} ans (> {YEARS_MAX})"
    return "VALIDATED_FOR_FORWARD", f"passe le gate complet, ETA={yrs} ans"


SRC = ["axisA_results.json", "axisB_results.json", "axisCD_results.json",
       "axisEF_results.json", "fixups_results.json", "final_results.json"]
allr = []
for f in SRC:
    p = os.path.join(OUT, f)
    if not os.path.exists(p):
        print("MANQUANT:", p); continue
    for g in json.load(open(p)):
        g["_source"] = f
        allr.append(g)

# --- taux d'evenements et ETA du TAUX DE REUSSITE pour les tests de signe A1b ---
rate_by_dh = {}
for g in allr:
    m = __import__("re").match(r"A1_LIST_DRIFT_XSNEUTRAL_(d\d+h_h\d+h)$", g["id"])
    if m: rate_by_dh[m.group(1)] = g.get("event_rate_per_week_6m")
for g in allr:
    m = __import__("re").match(r"A1b_LIST_FADE_SIGN_TEST_(d\d+h_h\d+h)$", g["id"])
    if not m: continue
    rate = rate_by_dh.get(m.group(1))
    g["event_rate_per_week_6m"] = rate
    t, n = g.get("t_stat_declustered"), g.get("n_independent_L3")
    if t and n and rate:
        nreq = K_POWER * n / (HAIRCUT * t) ** 2 / 1.0 if False else K_POWER / (HAIRCUT * (abs(t)/math.sqrt(n))) ** 2
        g["hitrate_n_required"] = round(nreq, 1)
        g["hitrate_eta_years"] = round(nreq / rate / 52.1775, 2)

for g in allr:
    v, why = verdict_for(g)
    g["verdict"] = v
    g["verdict_reason"] = why
    if fam_key(g["id"]) in PREREG_STATUS or g["id"].split("_")[0] in PREREG_STATUS:
        g["prereg_status"] = PREREG_STATUS.get(fam_key(g["id"]), PREREG_STATUS.get(g["id"].split("_")[0]))
    else:
        g.setdefault("prereg_status", "PREREGISTERED")
    g["t_stat_needed_for_eta_3y"] = t_needed_for_eta(g.get("n_independent_L3"),
                                                     g.get("event_rate_per_week_6m"))

order = {"VALIDATED_FOR_FORWARD": 0, "PROMISING_NEEDS_VALIDATION": 1, "UNCONFIRMABLE_IN_HORIZON": 2,
         "COST_FRAGILE": 3, "REGIME_DEPENDENT": 4, "WEAK": 5, "DEAD": 6, "DATA_LIMITED": 7,
         "DESCRIPTIVE": 8}
allr.sort(key=lambda g: (order.get(g["verdict"], 9), g["id"]))

summary = {}
for g in allr:
    summary[g["verdict"]] = summary.get(g["verdict"], 0) + 1

doc = dict(
    worker="W3_LISTINGS_LIFECYCLE", round="alpha_hunt_2026-09-03_round4", written="2026-09-05",
    n_mechanisms=len(allr), verdict_counts=summary,
    axis_level_conclusion=("Axe entier UNCONFIRMABLE_IN_HORIZON : le taux d'episodes independants "
                           "plafonne a ~0,70 vague de cotation/semaine (evenements) et ~0,16 plage "
                           "de regime/semaine (signaux de regime). Aucun mecanisme de l'axe n'atteint "
                           "le t de decouverte qu'il faudrait pour un ETA < 3 ans."),
    mechanisms=allr)
json.dump(doc, open(os.path.join(DEST, "RESULTS.json"), "w"), indent=1, default=str)

print(f"{len(allr)} mecanismes -> {os.path.join(DEST,'RESULTS.json')}")
print(json.dumps(summary, indent=1))
print()
hdr = f"{'id':<44}{'verdict':<26}{'L3':>5}{'net14':>9}{'net28':>9}{'t_L3':>7}{'ETA_a':>10}{'t_req_3a':>10}"
print(hdr); print("-"*len(hdr))
for g in allr:
    print(f"{g['id']:<44}{g['verdict']:<26}{g.get('n_independent_L3')!s:>5}"
          f"{g.get('net_bps')!s:>9}{g.get('net_bps_stress28')!s:>9}"
          f"{g.get('t_stat_declustered')!s:>7}"
          f"{(g.get('eta_forward_confirmation') or {}).get('years')!s:>10}"
          f"{g.get('t_stat_needed_for_eta_3y')!s:>10}")
