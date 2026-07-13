"""
Agent CORRELATOR — construit les features quotidiennes world (Parquet) et
teste leur relation CAUSALE avec les rendements BTC/ETH, avec RIGUEUR :
  • rendements (pas niveaux) ; feature à t-k vs rendement à t (prédictif) ;
  • Pearson r + IC 95% par bootstrap (2000 rééch.) + p-value approx (t) ;
  • scan de lag 0..5 j ; ne retient un candidat que si l'IC exclut 0 ;
  • AVERTISSEMENT multiple-testing explicite : N tests → faux positifs ;
    un candidat n'est JAMAIS un edge tant que non validé en walk-forward.
Gate : ne tourne que si le dernier quality_report est data_trustworthy.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from src.institutional.worldmon.bigdata_store import BigDataStore

ROOT_ASSETS = ["BTCUSDT", "ETHUSDT"]
LAGS = [0, 1, 2, 3, 5]
BOOT = 2000


def _price(symbol: str) -> Optional[pd.Series]:
    try:
        from src.institutional.engines.legacy_bridge import load_enriched
        df = load_enriched(symbol, required_cols=["close"])
        s = df.set_index(pd.to_datetime(df["datetime"], utc=True))["close"]
        return s.resample("D").last().dropna()
    except Exception:
        return None


def _boot_ci(x: np.ndarray, y: np.ndarray, n_boot=BOOT, seed=0):
    rng = np.random.default_rng(seed)
    n = len(x)
    rs = np.empty(n_boot)
    for i in range(n_boot):
        idx = rng.integers(0, n, n)
        xs, ys = x[idx], y[idx]
        sx, sy = xs.std(), ys.std()
        rs[i] = np.mean((xs - xs.mean()) * (ys - ys.mean())) / (sx * sy + 1e-12)
    return float(np.percentile(rs, 2.5)), float(np.percentile(rs, 97.5))


class Correlator:
    name = "correlator"

    def __init__(self, store: BigDataStore):
        self.store = store

    def _daily_features(self) -> pd.DataFrame:
        df = self.store.events_df(since_days=3650)
        if df.empty:
            return pd.DataFrame()
        df["day"] = df["ts"].dt.floor("D")
        feats = {}
        # métriques média GDELT (valeur moyenne/jour)
        for metric in ("gdelt_vol", "gdelt_tone"):
            m = df[df.get("metric") == metric]
            if not m.empty:
                feats[metric] = m.groupby("day")["value"].mean()
        # macro
        for metric in ("btc_dominance", "total_mcap_usd", "mcap_change_24h"):
            m = df[df.get("metric") == metric]
            if not m.empty:
                feats[metric] = m.groupby("day")["value"].mean()
        # séismes : énergie ~ 10^(1.5*mag) sommée/jour
        q = df[df.get("kind") == "geophysical"]
        if not q.empty:
            q = q.assign(energy=10 ** (1.5 * q["value"]))
            feats["quake_energy"] = q.groupby("day")["energy"].sum()
        # sentiment news agrégé
        nw = df[df.get("kind") == "news"]
        if not nw.empty and "sentiment" in nw.columns:
            feats["news_sentiment"] = nw.groupby("day")["sentiment"].mean()
        if not feats:
            return pd.DataFrame()
        return pd.DataFrame(feats).sort_index()

    def run(self) -> Dict:
        q = self.store.latest_doc("quality_reports")
        if not q or not q.get("data_trustworthy"):
            return {"agent": self.name, "ok": True, "skipped": True,
                    "reason": "quality gate: data non fiable"}
        feats = self._daily_features()
        if feats.empty:
            return {"agent": self.name, "ok": True, "candidates": 0,
                    "reason": "features insuffisantes"}
        try:
            self.store.write_features("world_daily_features", feats)
        except Exception:
            pass

        candidates: List[Dict] = []
        n_tests = 0
        for asset in ROOT_ASSETS:
            px = _price(asset)
            if px is None:
                continue
            ret = px.pct_change()
            for col in feats.columns:
                fchg = feats[col].pct_change().replace([np.inf, -np.inf], np.nan)
                for lag in LAGS:
                    n_tests += 1
                    joined = pd.DataFrame(
                        {"f": fchg.shift(lag), "r": ret}).dropna()
                    if len(joined) < 40:
                        continue
                    x, y = joined["f"].values, joined["r"].values
                    if x.std() < 1e-12:
                        continue
                    r = float(np.corrcoef(x, y)[0, 1])
                    lo, hi = _boot_ci(x, y)
                    sig = (lo > 0) or (hi < 0)   # IC 95% exclut 0
                    if sig and abs(r) >= 0.1:
                        t = r * np.sqrt(len(x) - 2) / np.sqrt(max(1 - r * r, 1e-9))
                        candidates.append({
                            "asset": asset, "feature": col, "lag_days": lag,
                            "pearson": round(r, 3), "ci95": [round(lo, 3), round(hi, 3)],
                            "t_stat": round(float(t), 2), "n": int(len(x))})
        candidates.sort(key=lambda c: -abs(c["pearson"]))
        doc = {"agent": self.name, "ok": True,
               "n_tests": n_tests, "n_candidates": len(candidates),
               "candidates": candidates[:20],
               "WARNING": (f"{n_tests} tests effectués → attendus ~{n_tests*0.05:.0f} "
                           "faux positifs à 5%. Un candidat n'est PAS un edge : "
                           "validation walk-forward obligatoire avant toute jambe."),
               "generated_at": datetime.now(timezone.utc).isoformat()}
        self.store.write_doc("signal_candidates", dict(doc))
        return {"agent": self.name, "ok": True, "n_tests": n_tests,
                "n_candidates": len(candidates), "top": candidates[:5]}
