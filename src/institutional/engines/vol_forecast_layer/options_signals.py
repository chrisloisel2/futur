"""
src/institutional/engines/vol_forecast_layer/options_signals.py
─────────────────────────────────────────────────────────────────────────────
Les deux signaux bruts basés sur les trades Deribit derrière
VOL_FORECAST_LAYER_V1 (Live Alpha Lab), reproduisant -- pas re-dérivant -- les
mécanismes mesurés dans reports/edge_discovery/alpha_hunt_2026-08-30/
w6_options/REPORT.md (le 3e signal, M2 rv_iv_spread, est calculé dans
panel.py à partir de atm_iv_traded ci-dessous + realized_vol.py) :

  M6  far_otm_put_share(day) = notional(cp=='P' & strike<=0.85*index_price)
                                / notional(tous les trades) ce jour-là
      ("far-OTM put activity share -> forward RV", IC brut +0.237,
      IC partiel confound-checked +0.158 vs sameday_rv. Part HAUTE ->
      historiquement RV forward PLUS HAUTE.)

  M17 block_count_24h(day)   = count(is_block==True) dans le jour calendaire
                                `day`
      ("hourly block-trade count -> forward RV at 4h/24h", IC brut ~+0.22 à
      +0.31 en horaire, IC partiel confound-checked +0.0996 vs RV glissante
      24h. Count HAUT -> historiquement RV forward PLUS HAUTE.)
      ⚠ SIMPLIFICATION DÉLIBÉRÉE : le rapport source teste M17 à granularité
      HORAIRE. VOL_FORECAST_LAYER_V1 émet UN SEUL forecast par jour
      calendaire (pour combiner proprement avec M2/M6, tous deux nativement
      quotidiens) -- ce module agrège donc le comptage de block trades en
      bucket QUOTIDIEN. Ce n'est PAS une re-vérification de l'IC horaire
      spécifique de M17 ; c'est une adaptation documentée du même signal
      sous-jacent (activité block-trade brute) à une cadence plus grossière.
      Voir freeze_spec.json.

atm_iv_traded(day) est déjà calculé et tenu à jour par le pipeline existant
(build_deribit_positioning_features.py, dans futur-deribit-options.timer) --
réutilisé tel quel ici, jamais recalculé.

Lit data/options_backfill/deribit/ (trades/BTC/*.parquet +
features/BTC_daily.parquet), rafraîchi quotidiennement par le timer systemd
existant futur-deribit-options.timer (03:20) -- aucun nouveau collecteur
démarré ici.
"""
from __future__ import annotations

import glob
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[4]
DERIBIT_DIR = ROOT / "data" / "options_backfill" / "deribit"
TRADES_DIR = DERIBIT_DIR / "trades"
FEATURES_DAILY = DERIBIT_DIR / "features" / "BTC_daily.parquet"

FAR_OTM_PUT_MONEYNESS = 0.85   # strike <= 0.85 * index_price, IDENTIQUE à M6 (w6 REPORT.md)

_TRADE_COLUMNS = ["ts", "cp", "strike", "index_price", "amount", "is_block"]


def load_atm_iv_daily() -> pd.DataFrame:
    """atm_iv_traded(day) -- réutilisé tel quel depuis
    features/BTC_daily.parquet (jamais recalculé ici)."""
    if not FEATURES_DAILY.exists():
        return pd.DataFrame(columns=["day", "atm_iv_traded"])
    df = pd.read_parquet(FEATURES_DAILY, columns=["day", "atm_iv_traded"])
    df["day"] = pd.to_datetime(df["day"], utc=True).dt.floor("D")
    return df.sort_values("day").reset_index(drop=True)


def _iter_trade_files(currency: str = "BTC") -> list[str]:
    d = TRADES_DIR / currency
    return sorted(glob.glob(str(d / "*.parquet")))


def compute_daily_options_flow_signals(currency: str = "BTC") -> pd.DataFrame:
    """Un seul passage sur les fichiers de trades mensuels (~44 fichiers,
    ~584MB au total, colonnes projetées -- jamais tout chargé en mémoire
    d'un coup) -- calcule PAR JOUR calendaire UTC :
      far_otm_put_notional, total_notional, far_otm_put_share,
      block_count_24h, n_trades.

    Traitement mois par mois (accumulation d'agrégats journaliers
    seulement, les lignes brutes de chaque mois sont libérées après
    agrégation) -- borne la mémoire à ~1 mois de trades à la fois (max
    quelques centaines de milliers de lignes).

    Sanity-check fait à la construction : sum(amount) par jour == colonne
    `notional_btc` de features/BTC_daily.parquet (vérifié exactement égal
    sur 2026-08-30, 9984.2 des deux côtés) -- confirme que `amount` est bien
    la même unité de notional BTC que le pipeline existant utilise.
    """
    files = _iter_trade_files(currency)
    empty = pd.DataFrame(columns=[
        "day", "far_otm_put_notional", "total_notional", "far_otm_put_share",
        "block_count_24h", "n_trades",
    ])
    if not files:
        return empty

    daily_frames = []
    for f in files:
        try:
            df = pd.read_parquet(f, columns=_TRADE_COLUMNS)
        except Exception:
            continue  # fichier mensuel corrompu/partiel -- ignoré, pas fatal (comme panel.py)
        if df.empty:
            continue
        df["ts"] = pd.to_datetime(df["ts"], utc=True)
        df["day"] = df["ts"].dt.floor("D")
        is_far_otm_put = (df["cp"] == "P") & (df["strike"] <= FAR_OTM_PUT_MONEYNESS * df["index_price"])
        df["far_otm_notional"] = df["amount"].where(is_far_otm_put, 0.0)

        agg = df.groupby("day").agg(
            far_otm_put_notional=("far_otm_notional", "sum"),
            total_notional=("amount", "sum"),
            block_count_24h=("is_block", "sum"),
            n_trades=("amount", "size"),
        ).reset_index()
        daily_frames.append(agg)

    if not daily_frames:
        return empty

    out = pd.concat(daily_frames, ignore_index=True)
    # Un même jour calendaire ne devrait apparaître que dans un seul fichier
    # mensuel (fichiers découpés par mois calendaire exact) -- regroupé par
    # prudence quand même, jamais d'hypothèse silencieuse.
    out = out.groupby("day", as_index=False).sum(numeric_only=True)
    out["far_otm_put_share"] = out["far_otm_put_notional"] / out["total_notional"].replace(0.0, pd.NA)
    return out.sort_values("day").reset_index(drop=True)
