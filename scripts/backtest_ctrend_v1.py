#!/usr/bin/env python3
"""
scripts/backtest_ctrend_v1.py
─────────────────────────────────────────────────────────────────────────────
CTREND v1 — cross-sectional trend, UNIVERS POINT-IN-TIME. Verdict final.

Corrige les limites documentées de v0 (reports/ctrend/CTREND_V0.md) :

  Univers point-in-time (aucune information future) :
  - 787 perps USDT um Vision, CONTRATS DÉLISTÉS INCLUS ;
  - éligibilité à t : ≥31 jours d'historique, barre présente à t,
    volume médian GLISSANT 30 j (quote) décalé à t−1 ≥ 5 M$ ;
  - univers(t) = top 50 par volume médian t−1 ; stable/fiat exclus ;
  - aucune donnée avant listing (les klines commencent au listing) ;
  - après delisting : plus de barre → sortie forcée au dernier close
    (2 jours sans barre), coût appliqué ;
  - aucun forward-fill de rendement (pct_change fill_method=None).

  Portefeuille self-financing, cash explicite :
  - NAV = cash + Σ positions ; frais déduits du cash, positions = w × NAV
    net de frais ; frais UNIQUEMENT sur |Δ positions| (15 bps par côté,
    30 bps A/R, rapporté ×1 et ×2) ;
  - exécution barre suivante (open t+1 = close t en crypto continu) ;
    robustesse : délai +1 barre supplémentaire ;
  - long-only top-5 équipondéré parmi scores > 0, gate BTC>MA20 (protocole
    v0 conservé, non retouché).

  Overlay de risque PRÉ-ENREGISTRÉ (seul autorisé) :
  - vol-targeting 20 % ann. (vol réalisée 60 j de la stratégie, causale,
    expo ∈ [0,1], surcoût de turnover facturé) ;
  - cap par actif 25 % NAV (appliqué au rebalance) ;
  - hystérésis de classement : entrée top-5, sortie si rang > 10.

  Rapporté séparément (aucune optimisation de filtres) :
  - CAGR, CMGR, moyenne/médiane mensuelles ; années 2024/2025/2026 ;
  - coûts ×1/×2 ; délai +1 barre ; DD brut et après risk-targeting ;
  - contribution top-3 mois / top-10 jours ;
  - DSR (essais = grille sensibilité + primaire + v0), PBO (CSCV S=10) ;
  - sensibilité one-at-a-time (12 variantes, verdict sur le primaire) ;
  - corrélation + contribution marginale vs les 3 jambes existantes
    (V1.2 socle, STACK MH, BASIS_TERM).

Sortie : reports/ctrend/CTREND_V1_RESULT.json + CTREND_V1.md
"""
from __future__ import annotations

import hashlib
import itertools
import json
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
from scipy import stats as sps

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DATA_DIR = ROOT / "data" / "derivatives_backfill" / "um_klines_1d"
OUT_DIR = ROOT / "reports" / "ctrend"
LIQ_DIR = ROOT / "reports" / "liq_cascade"

STABLE_FIAT = {"USDCUSDT", "TUSDUSDT", "BUSDUSDT", "FDUSDUSDT", "USDPUSDT",
               "DAIUSDT", "EURUSDT", "GBPUSDT", "AEURUSDT", "USD1USDT",
               "USDEUSDT", "XUSDUSDT", "USDFUSDT"}

START = "2020-06-01"          # 30 j de volume + 31 j d'historique dispo
DELIST_GRACE_DAYS = 2


@dataclass(frozen=True)
class Config:
    lookbacks: tuple = (1, 3, 7, 14, 30)
    top_k: int = 5
    exit_rank: int = 10           # hystérésis : sortie si rang > exit_rank
    rebalance_days: int = 7
    gate_ma: int = 20             # 0 = pas de gate
    universe_size: int = 50
    min_median_qv: float = 5e6
    cost_rt: float = 0.0030
    cost_mult: float = 1.0
    delay_extra: int = 0          # +1 = barre de délai supplémentaire
    vol_target: float = 0.0       # 0 = pas de vol-targeting ; sinon ann.
    vol_lookback: int = 60
    asset_cap: float = 0.25


PRIMARY = Config()


# ───────────────────────────── données ─────────────────────────────

def load_panel():
    closes, qvs = {}, {}
    for pq in sorted(DATA_DIR.glob("*_1d.parquet")):
        sym = pq.name.replace("_1d.parquet", "")
        if sym in STABLE_FIAT or not sym.endswith("USDT"):
            continue
        df = pd.read_parquet(pq, columns=["open_time", "close", "quote_volume"])
        df = df.set_index("open_time").sort_index()
        df = df[~df.index.duplicated(keep="last")]
        closes[sym] = df["close"]
        qvs[sym] = df["quote_volume"]
    close = pd.DataFrame(closes).sort_index()
    qv = pd.DataFrame(qvs).sort_index()
    return close.loc["2019-09-01":], qv.loc["2019-09-01":]


def build_membership(close: pd.DataFrame, qv: pd.DataFrame,
                     cfg: Config) -> pd.DataFrame:
    """Univers point-in-time : top-N par volume médian 30 j décalé t−1."""
    med = qv.rolling(30, min_periods=20).median().shift(1)   # info ≤ t−1
    hist_ok = close.notna().rolling(31, min_periods=31).count() >= 31
    elig = (med >= cfg.min_median_qv) & hist_ok & close.notna()
    ranked = med.where(elig).rank(axis=1, ascending=False, method="first")
    return ranked <= cfg.universe_size


def build_scores(close: pd.DataFrame, member: pd.DataFrame,
                 cfg: Config):
    zs = None
    for lb in cfg.lookbacks:
        r = close.pct_change(lb, fill_method=None).where(member)
        z = r.sub(r.mean(axis=1), axis=0).div(r.std(axis=1), axis=0)
        zs = z if zs is None else zs + z
    scores = zs / len(cfg.lookbacks)
    ranks = scores.rank(axis=1, ascending=False, method="first")
    return scores, ranks


# ───────────────────────────── simulation ─────────────────────────────

def simulate(close: pd.DataFrame, member: pd.DataFrame, cfg: Config,
             scores=None, ranks=None) -> pd.DataFrame:
    """Self-financing, cash explicite, exécution barre suivante (+delay).

    Retourne DataFrame quotidien : equity, gross, cash, turnover, n_pos.
    """
    if scores is None:
        scores, ranks = build_scores(close, member, cfg)
    ret = close.pct_change(fill_method=None)
    dates = close.index
    btc = close["BTCUSDT"]
    gate = ((btc > btc.rolling(cfg.gate_ma).mean()).fillna(False)
            if cfg.gate_ma else pd.Series(True, index=dates))

    cash = 1.0
    pos: Dict[str, float] = {}          # valeur courante par symbole
    nan_days: Dict[str, int] = {}
    half_fee = (cfg.cost_rt / 2.0) * cfg.cost_mult
    pending: List[tuple] = []           # (execute_at_index, target_weights)
    last_reb = -10**9
    strat_ret_hist: List[float] = []    # pour vol-targeting causal
    rows = []
    start_i = max(dates.searchsorted(pd.Timestamp(START, tz="UTC")), 35)

    for i in range(start_i, len(dates)):
        t = dates[i]
        nav_before = cash + sum(pos.values())

        # 1) MTM du jour (aucun forward-fill : NaN = pas de rendement)
        r_t = ret.iloc[i]
        for sym in list(pos):
            r = r_t.get(sym)
            if r is not None and np.isfinite(r):
                pos[sym] *= (1.0 + float(r))
                nan_days[sym] = 0
            else:
                nan_days[sym] = nan_days.get(sym, 0) + 1
                if nan_days[sym] >= DELIST_GRACE_DAYS:   # delisting
                    cash += pos.pop(sym) * (1.0 - half_fee)
                    nan_days.pop(sym, None)
        nav = cash + sum(pos.values())
        strat_ret_hist.append(nav / nav_before - 1.0 if nav_before > 0 else 0.0)

        # 2) exécution des ordres arrivés à échéance (barre suivante + délai)
        due = [p for p in pending if p[0] <= i]
        pending = [p for p in pending if p[0] > i]
        if due:
            target_w = due[-1][1]                       # dernier ordre gagne
            # vol-targeting causal (vol réalisée de la stratégie)
            expo = 1.0
            if cfg.vol_target > 0 and len(strat_ret_hist) > cfg.vol_lookback:
                rv = np.std(strat_ret_hist[-cfg.vol_lookback:]) * np.sqrt(365)
                if rv > 1e-9:
                    expo = min(1.0, cfg.vol_target / rv)
            tgt_val = {s: min(w * expo, cfg.asset_cap) * nav
                       for s, w in target_w.items()}
            turnover = 0.0
            for sym in set(pos) | set(tgt_val):
                turnover += abs(tgt_val.get(sym, 0.0) - pos.get(sym, 0.0))
            fees = turnover * half_fee
            cash = nav - fees - sum(tgt_val.values())
            if cash < 0:                                 # self-financing strict
                scale = (nav - fees) / max(sum(tgt_val.values()), 1e-12)
                tgt_val = {s: v * scale for s, v in tgt_val.items()}
                cash = nav - fees - sum(tgt_val.values())
            pos = {s: v for s, v in tgt_val.items() if v > 0}
            nan_days = {s: 0 for s in pos}
            nav = cash + sum(pos.values())
        else:
            turnover = 0.0

        # 3) décision au close t (exécutée à i+1+delay)
        if i - last_reb >= cfg.rebalance_days and i + 1 < len(dates):
            if not bool(gate.iloc[i]):
                target = {}
            else:
                s_t = scores.iloc[i]
                rk_t = ranks.iloc[i]
                held_keep = [s for s in pos
                             if np.isfinite(rk_t.get(s, np.nan))
                             and rk_t[s] <= cfg.exit_rank
                             and s_t.get(s, 0) > 0]
                cand = (s_t[(s_t > 0) & (rk_t <= cfg.top_k)]
                        .sort_values(ascending=False).index.tolist())
                names = list(dict.fromkeys(held_keep + cand))[:cfg.top_k]
                target = {s: 1.0 / cfg.top_k for s in names}
            pending.append((i + 1 + cfg.delay_extra, target))
            last_reb = i

        rows.append((t, nav, sum(pos.values()) / nav if nav > 0 else 0.0,
                     cash / nav if nav > 0 else 1.0,
                     turnover / nav if nav > 0 else 0.0, len(pos)))

    df = pd.DataFrame(rows, columns=["date", "equity", "gross", "cash",
                                     "turnover", "n_pos"]).set_index("date")
    df["equity"] /= df["equity"].iloc[0]
    return df


# ───────────────────────────── métriques ─────────────────────────────

def metrics(eq: pd.Series, turnover: Optional[pd.Series] = None) -> dict:
    ret_d = eq.pct_change().dropna()
    years = (eq.index[-1] - eq.index[0]).days / 365.25
    cagr = float(eq.iloc[-1] ** (1 / years) - 1)
    monthly = eq.resample("M").last().pct_change().dropna()
    dd = (eq / eq.cummax() - 1.0)
    sharpe = float(ret_d.mean() / ret_d.std() * np.sqrt(365)) if ret_d.std() else 0.0
    lr_d, lr_m = np.log1p(ret_d), np.log1p(monthly)
    tot = float(lr_d.sum())
    per_year = {str(y): round(float(v), 4) for y, v in
                ((1 + ret_d).groupby(ret_d.index.year).prod() - 1).items()}
    out = {
        "cagr": round(cagr, 4),
        "cmgr": round((1 + cagr) ** (1 / 12) - 1, 4),
        "monthly_mean": round(float(monthly.mean()), 4),
        "monthly_median": round(float(monthly.median()), 4),
        "monthly_positive_share": round(float((monthly > 0).mean()), 4),
        "sharpe": round(sharpe, 2),
        "max_dd": round(float(dd.min()), 4),
        "per_year": per_year,
        "top3_months_share_of_pnl": (round(float(lr_m.nlargest(3).sum() / tot), 3)
                                     if tot else None),
        "top10_days_share_of_pnl": (round(float(lr_d.nlargest(10).sum() / tot), 3)
                                    if tot else None),
        "n_months": int(len(monthly)),
    }
    if turnover is not None:
        out["turnover_ann"] = round(float(turnover.mean() * 365), 1)
    return out


def deflated_sharpe(ret_d: pd.Series, trial_sharpes_daily: List[float]) -> dict:
    """DSR (Bailey & López de Prado 2014). Essais = grille + primaire + v0."""
    sr = float(ret_d.mean() / ret_d.std())            # daily, non annualisé
    T = len(ret_d)
    g3 = float(sps.skew(ret_d))
    g4 = float(sps.kurtosis(ret_d, fisher=False))
    N = len(trial_sharpes_daily)
    var_tr = float(np.var(trial_sharpes_daily, ddof=1)) if N > 1 else 0.0
    em = 0.5772156649
    sr0 = np.sqrt(var_tr) * ((1 - em) * sps.norm.ppf(1 - 1.0 / N)
                             + em * sps.norm.ppf(1 - 1.0 / (N * np.e)))
    denom = np.sqrt(max(1 - g3 * sr + (g4 - 1) / 4 * sr ** 2, 1e-12))
    dsr = float(sps.norm.cdf((sr - sr0) * np.sqrt(T - 1) / denom))
    return {"sr_daily": round(sr, 4), "sr0_daily": round(float(sr0), 4),
            "n_trials": N, "T": T, "skew": round(g3, 2),
            "kurt": round(g4, 2), "dsr": round(dsr, 4)}


def pbo_cscv(ret_matrix: pd.DataFrame, n_blocks: int = 10) -> dict:
    """PBO par CSCV (Bailey et al. 2015) sur la matrice variantes×temps."""
    M = ret_matrix.dropna().values
    T, N = M.shape
    blocks = np.array_split(np.arange(T), n_blocks)
    lambdas = []
    for combo in itertools.combinations(range(n_blocks), n_blocks // 2):
        is_idx = np.concatenate([blocks[b] for b in combo])
        oos_idx = np.concatenate([blocks[b] for b in range(n_blocks)
                                  if b not in combo])
        mu_is = M[is_idx].mean(0) / (M[is_idx].std(0) + 1e-12)
        mu_oos = M[oos_idx].mean(0) / (M[oos_idx].std(0) + 1e-12)
        best = int(np.argmax(mu_is))
        rank = float((mu_oos < mu_oos[best]).sum()) / (N - 1)  # 1 = meilleur OOS
        omega = min(max(rank, 1e-6), 1 - 1e-6)
        lambdas.append(np.log(omega / (1 - omega)))
    lambdas = np.array(lambdas)
    return {"pbo": round(float((lambdas < 0).mean()), 3),
            "n_combos": len(lambdas), "n_variants": N,
            "lambda_median": round(float(np.median(lambdas)), 3)}


# ───────────────────────── jambes existantes ─────────────────────────

def existing_legs() -> Dict[str, pd.Series]:
    legs = {}
    v12 = pd.read_parquet(LIQ_DIR / "v12_equity_daily.parquet")
    legs["V1.2"] = pd.Series(v12["equity"].values,
                             index=pd.to_datetime(v12["date"], utc=True))
    basis = pd.read_parquet(LIQ_DIR / "basis_term_equity_daily.parquet")
    legs["BASIS_TERM"] = pd.Series(basis["equity"].values,
                                   index=pd.to_datetime(basis["date"], utc=True))
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "ovl", ROOT / "scripts" / "measure_v12_plus_stack_overlay.py")
    ovl = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ovl)
    legs["STACK_MH"] = ovl.stack_equity_daily("mh")
    return legs


def vs_legs(ctrend_eq: pd.Series) -> dict:
    legs = existing_legs()
    start = max([s.index[0] for s in legs.values()]
                + [ctrend_eq.index[0], pd.Timestamp("2023-01-01", tz="UTC")])
    end = min([s.index[-1] for s in legs.values()] + [ctrend_eq.index[-1]])
    idx = pd.date_range(start, end, freq="D", tz="UTC")
    norm = {}
    for k, s in {**legs, "CTREND_V1": ctrend_eq}.items():
        x = s.sort_index().resample("D").last().ffill().reindex(idx).ffill()
        norm[k] = x / x.iloc[0]
    rets = pd.DataFrame({k: x.pct_change() for k, x in norm.items()}).dropna()
    corr = rets.corr().round(3)

    def _stats(eq):
        r = eq.pct_change().dropna()
        yrs = (eq.index[-1] - eq.index[0]).days / 365.25
        return {"roi_ann": round(float(eq.iloc[-1] ** (1 / yrs) - 1), 4),
                "maxdd": round(float((eq / eq.cummax() - 1).min()), 4),
                "sharpe": round(float(r.mean() / max(r.std(), 1e-12)
                                      * np.sqrt(365)), 2)}

    combo3 = norm["V1.2"] * norm["STACK_MH"] * norm["BASIS_TERM"]
    combo4 = combo3 * norm["CTREND_V1"]
    return {"window": [str(start.date()), str(end.date())],
            "daily_corr": corr.to_dict(),
            "combo_3legs": _stats(combo3),
            "combo_3legs_plus_ctrend": _stats(combo4)}


# ───────────────────────────── main ─────────────────────────────

def main():
    close, qv = load_panel()
    print(f"panel: {close.shape[1]} symboles, {close.index[0].date()} → "
          f"{close.index[-1].date()}", flush=True)

    member = build_membership(close, qv, PRIMARY)
    scores, ranks = build_scores(close, member, PRIMARY)

    runs = {
        "primary_x1": PRIMARY,
        "primary_x2": replace(PRIMARY, cost_mult=2.0),
        "delay_plus1_x1": replace(PRIMARY, delay_extra=1),
        "voltarget20_x1": replace(PRIMARY, vol_target=0.20),
        "voltarget20_x2": replace(PRIMARY, vol_target=0.20, cost_mult=2.0),
    }
    out = {"strategy": "CTREND_V1", "preregistered": True,
           "protocol": {**PRIMARY.__dict__, "start": START,
                        "delist_grace_days": DELIST_GRACE_DAYS,
                        "stable_fiat_excluded": sorted(STABLE_FIAT)},
           "runs": {}}
    sims = {}
    for name, cfg in runs.items():
        need_own = cfg.lookbacks != PRIMARY.lookbacks
        sim = simulate(close, member, cfg,
                       None if need_own else scores,
                       None if need_own else ranks)
        sims[name] = sim
        out["runs"][name] = metrics(sim["equity"], sim["turnover"])
        print(f"  {name:16} cagr={out['runs'][name]['cagr']:+.1%} "
              f"dd={out['runs'][name]['max_dd']:.1%} "
              f"sharpe={out['runs'][name]['sharpe']}", flush=True)
        sim.to_parquet(OUT_DIR / f"v1_equity_{name}.parquet")

    # ── sensibilité one-at-a-time (coûts ×1, verdict sur primaire) ──
    variants = {
        "lb_3_7_14": replace(PRIMARY, lookbacks=(3, 7, 14)),
        "lb_7_14_30": replace(PRIMARY, lookbacks=(7, 14, 30)),
        "lb_plus90": replace(PRIMARY, lookbacks=(1, 3, 7, 14, 30, 90)),
        "topk_3": replace(PRIMARY, top_k=3, exit_rank=6),
        "topk_7": replace(PRIMARY, top_k=7, exit_rank=14),
        "reb_3j": replace(PRIMARY, rebalance_days=3),
        "reb_14j": replace(PRIMARY, rebalance_days=14),
        "no_gate": replace(PRIMARY, gate_ma=0),
        "no_hysteresis": replace(PRIMARY, exit_rank=5),
        "univ_30": replace(PRIMARY, universe_size=30),
        "univ_80": replace(PRIMARY, universe_size=80),
        "minqv_2M": replace(PRIMARY, min_median_qv=2e6),
    }
    sens = {}
    variant_rets = {"primary_x1": sims["primary_x1"]["equity"].pct_change()}
    for name, cfg in variants.items():
        mem_v = (member if (cfg.universe_size == PRIMARY.universe_size
                            and cfg.min_median_qv == PRIMARY.min_median_qv)
                 else build_membership(close, qv, cfg))
        sim = simulate(close, mem_v, cfg)
        m = metrics(sim["equity"])
        sens[name] = {k: m[k] for k in ("cagr", "max_dd", "sharpe")}
        variant_rets[name] = sim["equity"].pct_change()
        print(f"  sens {name:14} cagr={m['cagr']:+.1%} dd={m['max_dd']:.1%} "
              f"sharpe={m['sharpe']}", flush=True)
    out["sensitivity"] = sens

    # ── DSR + PBO ──
    prim_ret = sims["primary_x1"]["equity"].pct_change().dropna()
    ann = np.sqrt(365)
    trial_srs = [float(r.dropna().mean() / r.dropna().std())
                 for r in variant_rets.values()]
    v0_sr_daily = 1.39 / ann                     # v0 = essai antérieur documenté
    out["dsr"] = deflated_sharpe(prim_ret, trial_srs + [v0_sr_daily])
    out["pbo"] = pbo_cscv(pd.DataFrame(variant_rets))

    # ── vs jambes existantes (version vol-target, celle qui s'intégrerait) ──
    try:
        out["vs_existing_legs"] = vs_legs(sims["voltarget20_x1"]["equity"])
    except Exception as e:                        # noqa: BLE001
        out["vs_existing_legs"] = {"error": f"{type(e).__name__}: {e}"}

    # ── environnement ──
    h = hashlib.sha256()
    for pq in sorted(DATA_DIR.glob("*_1d.parquet")):
        h.update(pq.name.encode())
        h.update(str(pq.stat().st_size).encode())
    out["environment"] = {
        "cutoff_data": str(close.index[-1].date()),
        "generated_at": pd.Timestamp.utcnow().isoformat(),
        "data_hash_names_sizes_sha256_16": h.hexdigest()[:16],
        "n_symbols_panel": int(close.shape[1]),
        "python": sys.version.split()[0],
        "command": ".venv/bin/python scripts/backtest_ctrend_v1.py",
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "CTREND_V1_RESULT.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False, default=str))
    print(json.dumps({k: out[k] for k in ("runs", "dsr", "pbo")},
                     indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
