"""
validation_lib — noyau commun de l'ALPHA VALIDATION FACTORY (wave 2, 2026-09-03).

Implémente le gate de validation du briefing wave 2 (§3) UNE seule fois, pour que
chaque candidat n'ait plus à écrire que sa définition de signal :

  §3.1  gross/net14/net28, pf, n_raw, n_L1/L2/L3, t_stat_declustered,
        bootstrap_ci95, year_by_year, ex_best_year, worst_episode, max_drawdown
  §3.4  historical/recent/conservative_event_rate, n_required_statistical
        (block-bootstrap, unilatéral alpha=5%, puissance 80%, edge haircuté 50%),
        minimum_calendar_days, eta_p50, eta_conservative, confirmable_in_horizon

DISCIPLINE D'INDÉPENDANCE (briefing §2) : ce module ne lit AUCUN script/evidence de
découverte. Il ne contient aucune constante de seuil propre à un candidat — les
seuils viennent des PREREGISTRATION.md, passés en argument.

Conventions de coût du projet : 7 bps one-way par jambe.
  long-only  : net14 = gross - 14   (entrée + sortie)
  long-short : net28 = gross - 28   (2 jambes × entrée+sortie)
Le champ `net_bps_stress28` du briefing = coût doublé par rapport au coût nominal
du mécanisme (14 -> 28 pour un LO, 28 -> 42 pour un LS) : voir `cost_pair()`.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field, asdict
from typing import Callable, Iterable, Sequence

import numpy as np
import pandas as pd

# ── constantes de projet (jamais des seuils de candidat) ────────────────────
ONE_WAY_BPS = 7.0
Z_ALPHA_ONE_SIDED_5PCT = 1.6448536269514722   # z_{0.95}
Z_POWER_80PCT = 0.8416212335729143            # z_{0.80}
EDGE_HAIRCUT = 0.50                           # briefing §3.4 : edge haircuté de 50 %
CONFIRMABLE_HORIZON_DAYS = 1095               # 3 ans
BOOTSTRAP_RESAMPLES = 10_000
BOOTSTRAP_SEED = 20260903


def cost_pair(n_legs: int) -> tuple[float, float]:
    """(coût nominal, coût de stress) en bps pour un mécanisme à `n_legs` jambes.

    n_legs=1 (long-only)  -> (14, 28)
    n_legs=2 (long-short) -> (28, 56) ; le briefing note aussi 42 (+50 %) pour les
    mécanismes LS — les deux sont produits, `net_bps_stress28` = coût doublé.
    """
    nominal = 2.0 * ONE_WAY_BPS * n_legs
    return nominal, 2.0 * nominal


# ═══════════════════════════════════════════════════════════════════════════
# 1. Panel quotidien + éligibilité PIT
# ═══════════════════════════════════════════════════════════════════════════

DEFAULT_PANEL_GLOB = "/home/qbee/futur-data-v2/data_v2/normalized/perp_ohlcv/**/*.parquet"
DEFAULT_LISTINGS = "/home/qbee/futur/data/listings_backfill/binance/listings_calendar.parquet"


def duckdb_connect(memory_limit: str = "1200MB", threads: int = 2):
    """Connexion DuckDB sous les contraintes de ressources du briefing (§8)."""
    import duckdb

    con = duckdb.connect()
    con.execute(f"SET memory_limit='{memory_limit}'")
    con.execute(f"SET threads={threads}")
    con.execute("SET TimeZone='UTC'")
    return con


def build_daily_panel(
    panel_glob: str = DEFAULT_PANEL_GLOB,
    *,
    con=None,
    symbol_col: str = "symbol",
    ts_col: str = "timestamp",
    close_col: str = "close",
    qv_col: str = "quote_asset_volume",
) -> pd.DataFrame:
    """Barres 5m -> panel quotidien UTC, agrégation indépendante (jamais une
    feature pré-calculée).

    close_d = close de la DERNIÈRE barre 5m du jour UTC
    dv_d    = somme de quote_asset_volume sur le jour
    nbar_d  = nombre de barres 5m (diagnostic de trous)

    Retour : DataFrame [symbol, date, close, dv, nbar] trié, sans réindexation
    (les trous restent des trous — jamais remplis).
    """
    own = con is None
    if own:
        con = duckdb_connect()
    try:
        q = f"""
        WITH b AS (
            SELECT {symbol_col} AS symbol,
                   CAST({ts_col} AS TIMESTAMP) AS ts,
                   {close_col} AS close,
                   {qv_col} AS qv
            FROM read_parquet('{panel_glob}', union_by_name=true)
        )
        SELECT symbol,
               CAST(ts AS DATE) AS date,
               arg_max(close, ts) AS close,
               sum(qv)            AS dv,
               count(*)           AS nbar
        FROM b
        GROUP BY 1, 2
        ORDER BY 1, 2
        """
        return con.execute(q).df()
    finally:
        if own:
            con.close()


def load_onboard_ts(
    panel: pd.DataFrame, listings_path: str = DEFAULT_LISTINGS
) -> tuple[pd.Series, list[str]]:
    """onboard_ts par symbole depuis le calendrier de listings, fallback = première
    date de prix réelle (même convention que la production). Retourne aussi la
    liste des symboles tombés en fallback (à reporter dans la checklist §3.2)."""
    first_price = panel.groupby("symbol")["date"].min()
    onboard = first_price.copy()
    fallback = sorted(first_price.index.tolist())
    if os.path.exists(listings_path):
        cal = pd.read_parquet(listings_path)
        col = next(
            (c for c in ("onboard_ts", "onboard_date", "listing_ts", "listed_at") if c in cal.columns),
            None,
        )
        sym = next((c for c in ("symbol", "pair", "ticker") if c in cal.columns), None)
        if col is not None and sym is not None:
            cal = cal[[sym, col]].dropna()
            cal[col] = pd.to_datetime(cal[col], utc=True, errors="coerce").dt.tz_localize(None)
            m = cal.dropna(subset=[col]).groupby(sym)[col].min()
            m.index.name = "symbol"
            hit = onboard.index.intersection(m.index)
            onboard.loc[hit] = m.loc[hit].dt.date.astype("datetime64[ns]").values
            fallback = sorted(set(onboard.index) - set(hit))
    return onboard, fallback


def add_causal_liquidity(panel: pd.DataFrame, window: int = 30) -> pd.DataFrame:
    """Médiane roulante causale du dollar-volume sur les `window` jours SE TERMINANT
    à d inclus, fenêtre pleine exigée (pas de min_periods relâché).

    Le panel est réindexé sur un calendrier quotidien sans trou PAR SYMBOLE pour que
    la fenêtre soit calendaire et non "en nombre de barres présentes" — les jours
    manquants restent NaN et ne sont jamais remplis.
    """
    out = []
    for sym, g in panel.groupby("symbol", sort=False):
        g = g.set_index("date").sort_index()
        idx = pd.date_range(g.index.min(), g.index.max(), freq="D")
        g = g.reindex(idx)
        g["symbol"] = sym
        g["dv_med30"] = g["dv"].rolling(window, min_periods=window).median()
        g.index.name = "date"
        out.append(g.reset_index())
    return pd.concat(out, ignore_index=True)


def pit_eligible(
    day: pd.DataFrame,
    d: pd.Timestamp,
    onboard: pd.Series,
    *,
    min_listing_age_days: int = 30,
    liquidity_floor: float = 1_000_000.0,
) -> pd.Index:
    """Univers PIT éligible à la date de rebalancement `d`.

    (a) âge de listing >= min_listing_age_days ; (b) médiane causale 30 j du
    dollar-volume >= liquidity_floor ; (c) close_d présent.
    Aucune condition ne regarde après `d`.
    """
    ok = day["close"].notna() & (day["dv_med30"] >= liquidity_floor)
    age_ok = day.index.map(lambda s: (d - onboard.get(s, pd.Timestamp.max)).days >= min_listing_age_days)
    return day.index[ok.values & np.asarray(age_ok, dtype=bool)]


# ═══════════════════════════════════════════════════════════════════════════
# 2. Déclustering L1 / L2 / L3
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class Declustering:
    """Compte des unités indépendantes aux 3 niveaux du briefing §3.1."""

    n_raw: int
    n_independent_L1: int
    n_independent_L2: int
    n_independent_L3: int
    l3_definition: str

    def as_dict(self) -> dict:
        return asdict(self)


def decluster(
    obs: pd.DataFrame,
    *,
    l1_keys: Sequence[str],
    l2_keys: Sequence[str],
    l3_keys: Sequence[str],
    l3_definition: str,
) -> Declustering:
    """Compte les unités indépendantes. `obs` = une ligne par observation brute.

    L1 = même symbole / horizon, L2 = date de rebalancement (tous symboles),
    L3 = unité macro du mécanisme (mois, épisode chaîné, régime...).
    """
    return Declustering(
        n_raw=len(obs),
        n_independent_L1=int(obs.groupby(list(l1_keys), dropna=False).ngroups),
        n_independent_L2=int(obs.groupby(list(l2_keys), dropna=False).ngroups),
        n_independent_L3=int(obs.groupby(list(l3_keys), dropna=False).ngroups),
        l3_definition=l3_definition,
    )


def chain_episodes(times: pd.Series, gap: pd.Timedelta) -> np.ndarray:
    """Chaîne des événements en épisodes : deux événements séparés de moins de
    `gap` appartiennent au même épisode (unité L3 des mécanismes événementiels —
    cascades, chocs). Retourne un id d'épisode entier par ligne.

    `times` doit être trié croissant.
    """
    t = pd.to_datetime(pd.Series(times).reset_index(drop=True))
    if len(t) == 0:
        return np.array([], dtype=int)
    new = (t.diff() > gap).fillna(True).to_numpy()
    return np.cumsum(new) - 1


def regime_episodes(flag: pd.Series) -> np.ndarray:
    """Épisodes = plages contiguës d'un même état de régime (leçon wave 1
    LIQ_REPEAT_VOL_GATE : un régime de vol est un état macro LENT, chaque plage
    contiguë compte pour UNE observation indépendante, pas chaque jour)."""
    f = pd.Series(flag).reset_index(drop=True)
    new = (f != f.shift()).fillna(True).to_numpy()
    return np.cumsum(new) - 1


# ═══════════════════════════════════════════════════════════════════════════
# 3. Inférence : t cluster-robuste, block bootstrap, N_required
# ═══════════════════════════════════════════════════════════════════════════

def cluster_robust_t(x: np.ndarray, clusters: np.ndarray) -> tuple[float, float, float]:
    """t de Student cluster-robuste (Liang-Zeger) sur la moyenne de `x`.

    Pour une moyenne simple, la variance CR est
        Var = (G/(G-1)) · Σ_g (Σ_{i∈g} (x_i − x̄))² / N²
    Retourne (mean, se, t).
    """
    x = np.asarray(x, dtype=float)
    c = np.asarray(clusters)
    n = len(x)
    if n == 0:
        return float("nan"), float("nan"), float("nan")
    mean = float(x.mean())
    resid = x - mean
    sums = pd.Series(resid).groupby(pd.Series(c)).sum().to_numpy()
    g = len(sums)
    if g < 2:
        return mean, float("nan"), float("nan")
    var = (g / (g - 1.0)) * float((sums ** 2).sum()) / (n ** 2)
    se = math.sqrt(var) if var > 0 else float("nan")
    t = mean / se if se and se > 0 else float("nan")
    return mean, se, t


def block_bootstrap_mean(
    x: np.ndarray,
    clusters: np.ndarray,
    *,
    n_resamples: int = BOOTSTRAP_RESAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> dict:
    """Block bootstrap : rééchantillonne les CLUSTERS L3 avec remise (blocs =
    unités macro), pas les observations. Retourne moyenne, CI95, 5e centile
    unilatéral et l'écart-type bootstrap de la moyenne."""
    x = np.asarray(x, dtype=float)
    c = np.asarray(clusters)
    if len(x) == 0:
        return {"mean": float("nan"), "ci95": [float("nan")] * 2, "p05": float("nan"), "se": float("nan")}
    groups = [x[c == k] for k in pd.unique(c)]
    g = len(groups)
    rng = np.random.default_rng(seed)
    means = np.empty(n_resamples, dtype=float)
    for i in range(n_resamples):
        pick = rng.integers(0, g, size=g)
        vals = np.concatenate([groups[j] for j in pick])
        means[i] = vals.mean()
    return {
        "mean": float(x.mean()),
        "ci95": [float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))],
        "p05": float(np.percentile(means, 5.0)),
        "se": float(means.std(ddof=1)),
    }


def n_required(
    x: np.ndarray,
    clusters: np.ndarray,
    *,
    haircut: float = EDGE_HAIRCUT,
    boot: dict | None = None,
) -> float:
    """Nombre d'unités L3 indépendantes requises pour reconfirmer l'edge en forward
    (unilatéral alpha=5 %, puissance 80 %, effet haircuté de `haircut`).

        N_req = (z_a + z_b)² · σ²_cluster / (haircut · mean)²

    σ_cluster est déduit de l'écart-type bootstrap de la moyenne : σ = se · sqrt(G),
    ce qui préserve la corrélation intra-cluster (une simple std(x) la sous-estimerait).
    """
    x = np.asarray(x, dtype=float)
    c = np.asarray(clusters)
    g = len(pd.unique(c))
    if g < 2 or len(x) == 0:
        return float("nan")
    mean = float(x.mean())
    if boot is None:
        boot = block_bootstrap_mean(x, c)
    sigma = boot["se"] * math.sqrt(g)
    effect = haircut * mean
    if not np.isfinite(sigma) or effect <= 0:
        return float("inf")
    return float(((Z_ALPHA_ONE_SIDED_5PCT + Z_POWER_80PCT) ** 2) * (sigma ** 2) / (effect ** 2))


# ═══════════════════════════════════════════════════════════════════════════
# 4. Fréquence, ETA
# ═══════════════════════════════════════════════════════════════════════════

def event_rates(event_dates: Sequence, asof: pd.Timestamp | None = None) -> dict:
    """Taux d'événements L3 par jour sur 2 ans / 1 an / 6 mois, + taux conservateur
    (le min des trois, briefing §3.4)."""
    s = pd.Series(pd.to_datetime(list(event_dates))).sort_values()
    if len(s) == 0:
        return {"historical": 0.0, "recent": 0.0, "conservative": 0.0, "windows": {}}
    asof = pd.Timestamp(asof) if asof is not None else s.max()
    out = {}
    for label, days in (("last_2y", 730), ("last_1y", 365), ("last_6m", 182)):
        lo = asof - pd.Timedelta(days=days)
        span = min(days, max(1, (asof - s.min()).days))
        out[label] = float((s > lo).sum()) / span
    cons = min(out.values())
    return {
        "historical": out["last_2y"],
        "recent": out["last_6m"],
        "conservative": cons,
        "windows": out,
    }


def eta(
    n_req: float,
    rates: dict,
    *,
    minimum_calendar_days: int,
) -> dict:
    """ETA de reconfirmation forward. `minimum_calendar_days` = plancher structurel
    (182 j hebdo, 365 j mensuel, 60 j événementiel) : un mécanisme hebdomadaire ne
    peut pas être reconfirmé plus vite que son propre pas de temps."""
    def _days(rate: float) -> float:
        if not np.isfinite(n_req) or rate <= 0:
            return float("inf")
        return max(n_req / rate, float(minimum_calendar_days))

    p50 = _days(rates.get("historical", 0.0))
    cons = _days(rates.get("conservative", 0.0))
    return {
        "n_required_statistical": None if not np.isfinite(n_req) else round(n_req, 1),
        "minimum_calendar_days": minimum_calendar_days,
        "eta_p50_days": None if not np.isfinite(p50) else round(p50, 1),
        "eta_p50": "unbounded" if not np.isfinite(p50) else f"{p50:.0f} days (~{p50/365.25:.1f} years)",
        "eta_conservative_days": None if not np.isfinite(cons) else round(cons, 1),
        "eta_conservative": "unbounded" if not np.isfinite(cons) else f"{cons:.0f} days (~{cons/365.25:.1f} years)",
        "confirmable_in_horizon": bool(np.isfinite(cons) and cons < CONFIRMABLE_HORIZON_DAYS),
    }


# ═══════════════════════════════════════════════════════════════════════════
# 5. Statistiques de performance (§3.1)
# ═══════════════════════════════════════════════════════════════════════════

def perf_stats(
    per_period_bps: pd.Series,
    *,
    dates: pd.Series,
    cost_nominal: float,
    cost_stress: float,
) -> dict:
    """Bloc §3.1 complet à partir du gross en bps par période (déjà agrégé au
    niveau de la période de rebalancement/épisode).

    `pf` (profit factor) est calculé sur le NET nominal — un PF calculé sur le gross
    flatte systématiquement les mécanismes à petit edge.
    """
    g = pd.Series(per_period_bps, dtype=float).reset_index(drop=True)
    d = pd.to_datetime(pd.Series(dates).reset_index(drop=True))
    net = g - cost_nominal
    stress = g - cost_stress

    wins = net[net > 0].sum()
    losses = -net[net < 0].sum()
    pf = float(wins / losses) if losses > 0 else float("inf")

    years = d.dt.year
    by_year = (g - cost_nominal).groupby(years).mean().round(2).to_dict()
    by_year = {int(k): float(v) for k, v in by_year.items()}
    n_pos_years = sum(1 for v in by_year.values() if v > 0)

    if by_year:
        best = max(by_year, key=lambda k: by_year[k])
        ex_best = float(net[years != best].mean()) if (years != best).any() else float("nan")
    else:
        best, ex_best = None, float("nan")

    cum = net.cumsum()
    dd = float((cum - cum.cummax()).min()) if len(cum) else float("nan")

    return {
        "gross_bps": round(float(g.mean()), 2),
        "net_bps": round(float(net.mean()), 2),
        "net_bps_stress28": round(float(stress.mean()), 2),
        "cost_nominal_bps": cost_nominal,
        "cost_stress_bps": cost_stress,
        "pf": round(pf, 3) if np.isfinite(pf) else None,
        "n_periods": int(len(g)),
        "year_by_year": by_year,
        "n_years_positive": n_pos_years,
        "n_years": len(by_year),
        "best_year": None if best is None else int(best),
        "ex_best_year_net_bps": None if not np.isfinite(ex_best) else round(ex_best, 2),
        "worst_episode_bps": round(float(net.min()), 2) if len(net) else None,
        "max_drawdown_bps_cumule": round(dd, 2) if np.isfinite(dd) else None,
    }


def full_gate(
    per_period_bps: pd.Series,
    *,
    dates: pd.Series,
    l3: np.ndarray,
    cost_nominal: float,
    cost_stress: float,
    l3_definition: str,
    minimum_calendar_days: int,
    n_raw: int | None = None,
    n_l1: int | None = None,
    n_l2: int | None = None,
) -> dict:
    """Applique le gate §3.1 + §3.4 d'un coup sur une série de rendements par période.

    `per_period_bps` = gross bps par période ; `l3` = id de cluster macro par période.
    Le t, le bootstrap et N_required portent tous sur le NET nominal (c'est la
    quantité qui doit être > 0, pas le gross).
    """
    g = pd.Series(per_period_bps, dtype=float).reset_index(drop=True)
    d = pd.to_datetime(pd.Series(dates).reset_index(drop=True))
    net = (g - cost_nominal).to_numpy()
    l3 = np.asarray(l3)

    stats = perf_stats(g, dates=d, cost_nominal=cost_nominal, cost_stress=cost_stress)
    mean, se, t = cluster_robust_t(net, l3)
    boot = block_bootstrap_mean(net, l3)
    nreq = n_required(net, l3, boot=boot)

    l3_dates = pd.Series(d).groupby(pd.Series(l3)).min()
    rates = event_rates(l3_dates.tolist(), asof=d.max())
    eta_block = eta(nreq, rates, minimum_calendar_days=minimum_calendar_days)

    stats.update(
        {
            "n_raw": int(n_raw if n_raw is not None else len(g)),
            "n_independent_L1": int(n_l1 if n_l1 is not None else len(g)),
            "n_independent_L2": int(n_l2 if n_l2 is not None else len(g)),
            "n_independent_L3": int(len(pd.unique(l3))),
            "n_validation_independent": int(len(pd.unique(l3))),
            "l3_definition": l3_definition,
            "t_stat_declustered": None if not np.isfinite(t) else round(float(t), 3),
            "cluster_robust_se": None if not np.isfinite(se) else round(float(se), 3),
            "bootstrap_ci95": [round(v, 2) for v in boot["ci95"]],
            "bootstrap_p05": round(boot["p05"], 2),
            "historical_event_rate": f"{rates['historical']*7:.3f}/week",
            "recent_event_rate": f"{rates['recent']*7:.3f}/week",
            "conservative_event_rate": f"{rates['conservative']*7:.3f}/week",
            "event_rates_per_day": {k: round(v, 5) for k, v in rates["windows"].items()},
        }
    )
    stats.update(eta_block)
    return stats


# ═══════════════════════════════════════════════════════════════════════════
# 6. Backtest cross-sectionnel générique (V3/V4/V5)
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class XSecConfig:
    """Paramètres d'un backtest cross-sectionnel — TOUS préenregistrés."""

    formation_days: int
    holding_days: int
    quantile: float = 0.20
    liquidity_floor: float = 1_000_000.0
    min_listing_age_days: int = 30
    min_eligible: int = 20
    winsorize: tuple[float, float] | None = (0.01, 0.99)
    long_short: bool = False
    descending: bool = True          # True = rang décroissant (momentum), False = fade/reversal
    anchor: int = 0
    exec_lag_days: int = 0


class XSecPanel:
    """Panel cross-sectionnel matriciel + masque d'éligibilité PIT vectorisé.

    Construit UNE fois, réutilisé par toutes les perturbations (le masque dépend du
    plancher de liquidité, donc `eligibility()` est mémoïsé par plancher).
    """

    def __init__(self, panel: pd.DataFrame, onboard: pd.Series, *, min_listing_age_days: int = 30):
        self.close = panel.pivot(index="date", columns="symbol", values="close").sort_index()
        self.dvm = panel.pivot(index="date", columns="symbol", values="dv_med30").sort_index()
        self.dv = panel.pivot(index="date", columns="symbol", values="dv").sort_index()
        self.days = self.close.index
        self.symbols = self.close.columns
        self.min_listing_age_days = min_listing_age_days

        ob = pd.to_datetime(onboard.reindex(self.symbols))
        cutoff = ob + pd.Timedelta(days=min_listing_age_days)
        # age_ok[d, s] = d >= onboard_s + 30j   (aucune information postérieure à d)
        self._age_ok = pd.DataFrame(
            self.days.values[:, None] >= cutoff.values[None, :],
            index=self.days, columns=self.symbols,
        )
        self._elig_cache: dict[float, pd.DataFrame] = {}

    def eligibility(self, liquidity_floor: float) -> pd.DataFrame:
        key = float(liquidity_floor)
        if key not in self._elig_cache:
            self._elig_cache[key] = (
                self.close.notna() & (self.dvm >= key) & self._age_ok
            )
        return self._elig_cache[key]

    def grid(self, cfg: "XSecConfig") -> pd.DatetimeIndex:
        """Grille non chevauchante. Ancre 0 = PREMIÈRE date où `n_eligible >= min_eligible`
        (définition du prereg), décalée de `cfg.anchor` jours pour les phases."""
        elig = self.eligibility(cfg.liquidity_floor)
        n_elig = elig.sum(axis=1)
        ok = n_elig[n_elig >= cfg.min_eligible]
        if ok.empty:
            return pd.DatetimeIndex([])
        start = ok.index[0] + pd.Timedelta(days=cfg.anchor)
        return pd.DatetimeIndex(
            [d for d in pd.date_range(start, self.days.max(), freq=f"{cfg.holding_days}D")
             if d in self.close.index]
        )

    def forward_return(self, entry_d: pd.Timestamp, exit_d: pd.Timestamp) -> pd.Series:
        """Rendement simple close-to-close, tous symboles. Si le close de sortie est
        absent (délistage / trou), on prend le DERNIER close disponible dans
        (entry_d, exit_d] — un nom délisté n'est jamais silencieusement retiré
        (biais du survivant)."""
        entry = self.close.loc[entry_d]
        window = self.close.loc[(self.close.index > entry_d) & (self.close.index <= exit_d)]
        if window.empty:
            return pd.Series(np.nan, index=self.symbols)
        return window.ffill().iloc[-1] / entry - 1.0


def run_xsec(
    xp: XSecPanel,
    cfg: XSecConfig,
    signal_fn: Callable[["XSecPanel", pd.Timestamp, pd.Index], pd.Series],
) -> pd.DataFrame:
    """Backtest cross-sectionnel sur grille non chevauchante.

    `signal_fn(xp, d, eligible) -> Series` calcule le signal à la date `d` pour les
    noms éligibles — c'est la SEULE partie propre au candidat.

    Retourne une ligne par période de rebalancement.
    """
    elig_mask = xp.eligibility(cfg.liquidity_floor)
    rows = []
    for d in xp.grid(cfg):
        elig = elig_mask.columns[elig_mask.loc[d].to_numpy()]
        if len(elig) < cfg.min_eligible:
            continue

        sig = signal_fn(xp, d, elig).dropna()
        if len(sig) < cfg.min_eligible:
            continue

        entry_d = d + pd.Timedelta(days=cfg.exec_lag_days)
        if entry_d not in xp.close.index:
            continue
        exit_d = entry_d + pd.Timedelta(days=cfg.holding_days)
        fwd = xp.forward_return(entry_d, exit_d).reindex(sig.index).dropna()
        if len(fwd) < cfg.min_eligible:
            continue
        sig = sig.loc[fwd.index]

        if cfg.winsorize is not None:
            lo, hi = fwd.quantile(cfg.winsorize[0]), fwd.quantile(cfg.winsorize[1])
            fwd = fwd.clip(lo, hi)

        k = max(1, math.ceil(cfg.quantile * len(sig)))
        order = sig.sort_values(ascending=not cfg.descending)
        top, bottom = order.index[:k], order.index[-k:]

        r_top = float(fwd.loc[top].mean())
        r_bot = float(fwd.loc[bottom].mean())
        r_uni = float(fwd.mean())
        rows.append(
            {
                "date": d,
                "n_eligible": len(elig),
                "n_scored": len(sig),
                "k": k,
                "r_top": r_top,
                "r_bottom": r_bot,
                "r_universe": r_uni,
                "excess_gross_bps": (r_top - r_uni) * 1e4,
                "raw_gross_bps": r_top * 1e4,
                "ls_gross_bps": (r_top - r_bot) * 1e4,
                "long_leg": list(top),
            }
        )
    return pd.DataFrame(rows)


def month_clusters(dates: pd.Series) -> np.ndarray:
    """L3 par défaut des mécanismes à rebalancement : le mois calendaire."""
    d = pd.to_datetime(pd.Series(dates).reset_index(drop=True))
    return (d.dt.year * 100 + d.dt.month).to_numpy()


# ═══════════════════════════════════════════════════════════════════════════
# 7. Sérialisation
# ═══════════════════════════════════════════════════════════════════════════

def write_results(path: str, payload: dict) -> None:
    """RESULTS.json du briefing §4.3 (une entrée par candidat)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(payload, f, indent=2, default=str)
