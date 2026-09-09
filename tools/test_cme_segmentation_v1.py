#!/usr/bin/env python3
"""
test_cme_segmentation_v1.py -- CME_SEGMENTATION_V1, le test unique.

Hypothese : reports/loop/prereg/CME_SEGMENTATION_V1.md, scellee sur la branche
orpheline `prereg/cme-segmentation-v1`. Ce fichier est pinne par SHA-256 dans le
pre-enregistrement et se refuse lui-meme si une pin ne correspond plus.

Ordre impose : ledger AVANT calcul -> temoin exige -> pins verifiees -> calcul.
`--synthetic` fabrique des series aleatoires pour valider le pipeline : aucune
donnee reelle n'est lue dans ce mode.
"""
from __future__ import annotations

import argparse
import calendar
import hashlib
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path
from statistics import NormalDist

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import look_ledger as L                  # noqa: E402  (pinne)

PREREG = ROOT / "reports" / "loop" / "prereg" / "CME_SEGMENTATION_V1.md"
BRANCH = "prereg/cme-segmentation-v1"
DATA = ROOT / "data" / "cme_backfill"

# ---- configuration derivee du mecanisme (justifiee dans le prereg) ----------
N_HYPOTHESES = 2                # Bitfinex examinee et refusee avant : n = 2
ALPHA = 0.05
Z_WINDOW = 90                   # un cycle de contrat CME = un trimestre
Z_MIN_PERIODS = 60
Z_THRESHOLD = 1.0               # agir aux extremes seulement
COST_BPS_PER_UNIT = 10.0        # aller simple sur BTC perp, hypothese declaree
NW_LAG = 5
START = "2020-01-01"            # premiere barre 1h perp Binance sur Vision
ETF_DATE = "2024-01-11"         # ETF spot US : le canal conforme s'elargit
PINNED = ["tools/test_cme_segmentation_v1.py", "scripts/fetch_cme_basis_inputs.py",
          "tools/look_ledger.py", "src/institutional/live_alpha_lab/preregistration.py"]


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def pins_now():
    return {f: sha256(ROOT / f) for f in PINNED}


def pins_in_prereg():
    if not PREREG.exists():
        raise SystemExit("REFUS : pre-enregistrement absent -- rien ne se calcule sans lui")
    m = re.search(r"```json pins\n(.*?)\n```", PREREG.read_text(), re.S)
    if not m:
        raise SystemExit("prereg sans bloc de pins : refus")
    return json.loads(m.group(1))


def last_friday(y: int, m: int) -> date:
    d = date(y, m, calendar.monthrange(y, m)[1])
    return d - timedelta(days=(d.weekday() - 4) % 7)


def days_to_expiry(d: date) -> int:
    """Front-month CME : echeance le dernier vendredi du mois courant, sinon du suivant."""
    e = last_friday(d.year, d.month)
    if d >= e:
        y, m = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
        e = last_friday(y, m)
    return max((e - d).days, 1)


def load_real():
    yf = pd.read_parquet(DATA / "BTC_F_yahoo_daily.parquet")
    yf["date"] = yf["ts_utc"].dt.floor("D").dt.date
    F = yf.set_index("date")["close"].astype(float)
    def leg(name):
        # ALIGNEMENT EXACT, corrige avant scellement : la barre Yahoo est bornee a
        # minuit New York -- 04:00Z en ete, 05:00Z en hiver. Une heure fixe aurait
        # decale la jambe Binance six mois sur douze. On prend la bougie 1 h dont
        # la CLOTURE tombe sur l'horodatage exact de la barre Yahoo du jour.
        k = pd.read_parquet(DATA / name)
        k["close_time"] = k["open_time"] + pd.Timedelta(hours=1)
        m = yf[["date", "ts_utc"]].merge(k[["close_time", "close"]], left_on="ts_utc",
                                          right_on="close_time", how="inner")
        return m.set_index("date")["close"].astype(float)
    S, P = leg("BTCUSDT_spot_1h.parquet"), leg("BTCUSDT_um_1h.parquet")
    # funding quotidien BTCUSDT : somme des taux 8h (panel), annualise x365
    meta = json.load(open(ROOT / "data/_cache/panel_daily_meta.json"))
    z = np.load(ROOT / "data/_cache/panel_daily.npz")
    idx = pd.to_datetime(meta["index"], utc=True).date
    fund = pd.Series(z["fund"][:, meta["symbols"].index("BTCUSDT")], index=idx).astype(float)
    return F, S, P, fund


def load_synthetic(seed=0, n=1500):
    rng = np.random.default_rng(seed)
    d0 = date(2020, 1, 1)
    dates = [d0 + timedelta(days=i) for i in range(n)]
    S = pd.Series(30000 * np.exp(np.cumsum(rng.normal(0, 0.03, n))), index=dates)
    P = S * (1 + rng.normal(0, 0.0005, n))
    F = S * (1 + np.abs(rng.normal(0.01, 0.01, n)) * np.array([days_to_expiry(d) for d in dates]) / 365)
    fund = pd.Series(rng.normal(0.0003, 0.0004, n), index=dates)
    return F, S, P, fund


def build(F, S, P, fund):
    # Le funding du jour D (panel, 00:00Z) somme les reglements de 00:00, 08:00 et
    # 16:00 UTC de D : a 04:00Z de D, deux sont encore a venir. On prend D-1.
    fund = fund.copy(); fund.index = [d + timedelta(days=1) for d in fund.index]
    df = pd.DataFrame({"F": F, "S": S, "P": P, "fund": fund}).dropna()
    df = df[[d >= date.fromisoformat(START) for d in df.index]]
    dte = np.array([days_to_expiry(d) for d in df.index])
    df["basis_cme_ann"] = (df.F / df.S - 1.0) * 365.0 / dte
    df["fund_ann"] = df["fund"] * 365.0
    df["d"] = df.basis_cme_ann - df.fund_ann                      # institutionnel - retail
    mu = df.d.rolling(Z_WINDOW, min_periods=Z_MIN_PERIODS).mean()
    sd = df.d.rolling(Z_WINDOW, min_periods=Z_MIN_PERIODS).std()
    df["z"] = (df.d - mu) / sd
    pos = np.where(df.z >= Z_THRESHOLD, -1.0, np.where(df.z <= -Z_THRESHOLD, 1.0, 0.0))
    pos = np.where(np.isfinite(df.z), pos, 0.0)
    df["pos"] = pos
    # ALIGNEMENT, corrige avant scellement : signal decide sur la barre 04:00Z de t,
    # position ETABLIE a 04:00Z de t+1 (lag 1), rendement porte de t+1 a t+2.
    # La version precedente appliquait pos[t] au rendement t->t+1 : une execution
    # sur la barre du signal, le biais que le harnais evite depuis v4.
    r_next = df.P.shift(-1) / df.P - 1.0                           # rendement t -> t+1, sur le perp
    df["gross"] = df.pos.shift(1) * r_next * 1e4                   # ligne t+1 : pos[t] x (t+1 -> t+2)
    df["cost"] = np.abs(df.pos.diff()).shift(1).fillna(0.0) * COST_BPS_PER_UNIT   # paye a l'etablissement
    df["net"] = df.gross - df.cost
    return df.dropna(subset=["gross"])


def nw_t(x, lag):
    x = np.asarray(x, float); x = x[np.isfinite(x)]; n = len(x)
    if n < 60:
        return float("nan")
    mu = x.mean(); e = x - mu; s = (e @ e) / n
    for k in range(1, min(lag, n - 1) + 1):
        s += 2 * (1 - k / (lag + 1)) * ((e[k:] @ e[:-k]) / n)
    return float(mu / np.sqrt(max(s, 1e-18) / n))


def stats(df):
    net = df.net.to_numpy(); act = df[df.pos.shift(1).fillna(0) != 0]
    eq = (1 + net / 1e4).cumprod(); dd = float((eq / np.maximum.accumulate(eq) - 1).min())
    seg = np.array_split(net, 4)
    pre = df[[d < date.fromisoformat(ETF_DATE) for d in df.index]].net.mean()
    post = df[[d >= date.fromisoformat(ETF_DATE) for d in df.index]].net.mean()
    return {"n_days": int(len(net)), "n_days_en_position": int(len(act)),
            "gross_bps": round(float(df.gross.mean()), 3), "cost_bps": round(float(df.cost.mean()), 3),
            "net_bps": round(float(net.mean()), 3), "t_net_nw": round(nw_t(net, NW_LAG), 4),
            "t_gross_nw": round(nw_t(df.gross.to_numpy(), NW_LAG), 4),
            "sharpe_arith": round(float(net.mean() / net.std() * np.sqrt(365.25)), 4) if net.std() > 0 else None,
            "max_dd_1x": round(dd, 4), "skew_daily": round(float(pd.Series(net).skew()), 3),
            "sous_periodes_positives": int(sum(s.mean() > 0 for s in seg)),
            "net_bps_avant_ETF": round(float(pre), 3) if pre == pre else None,
            "net_bps_apres_ETF": round(float(post), 3) if post == post else None,
            "part_jours_short": round(float((df.pos < 0).mean()), 3), "part_jours_long": round(float((df.pos > 0).mean()), 3)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()
    thr = NormalDist().inv_cdf(1 - ALPHA / N_HYPOTHESES)         # unilateral, n = 2
    if a.synthetic:
        F, S, P, fund = load_synthetic(a.seed)
    else:
        want, have = pins_in_prereg(), pins_now()
        diff = [k for k in want if want[k] != have.get(k)]
        if diff:
            raise SystemExit(f"REFUS : code modifie depuis le scellement : {diff}")
        man = DATA / "MANIFEST.json"
        if not man.exists():
            raise SystemExit("REFUS : pas de MANIFEST.json -- les entrees n'ont pas ete acquises")
        cfg = {"hypothese": "CME_SEGMENTATION_V1", "n": N_HYPOTHESES, "threshold_one_sided": round(thr, 4),
               "z_window": Z_WINDOW, "z_threshold": Z_THRESHOLD, "cost_bps": COST_BPS_PER_UNIT,
               "dir": "contrarien a l'encombrement institutionnel", "inputs_manifest": json.load(open(man))}
        try:
            ent = L.record("confirm", (START, str(date.today())), [cfg], prereg=str(PREREG),
                           require_witness=True, witness_branch=BRANCH, note="CME_SEGMENTATION_V1 : le regard unique")
        except L.LedgerError as e:
            raise SystemExit(f"REFUS : {e}")
        print(f"ledger seq={ent['seq']} temoin pushed={ent['prereg']['pushed']}", flush=True)
        F, S, P, fund = load_real()
    df = build(F, S, P, fund)
    st = stats(df)
    st.update({"mode": "SYNTHETIQUE" if a.synthetic else "REEL", "threshold_one_sided_n2": round(thr, 4),
               "passe": None if a.synthetic else bool(st["t_net_nw"] >= thr),
               "window": [str(df.index[0]), str(df.index[-1])]})
    out = ROOT / "reports" / "loop" / "cme"; out.mkdir(parents=True, exist_ok=True)
    (out / f"CME_SEGMENTATION_V1_{'synthetic' if a.synthetic else 'REEL'}.json").write_text(json.dumps(st, indent=2))
    for k, v in st.items():
        print(f"  {k:26s} {v}")


if __name__ == "__main__":
    main()
