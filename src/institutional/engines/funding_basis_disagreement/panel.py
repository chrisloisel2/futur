"""
src/institutional/engines/funding_basis_disagreement/panel.py
─────────────────────────────────────────────────────────────────────────────
Construction CAUSALE du panel quotidien funding vs. basis trimestriel pour
FUNDING_BASIS_DISAGREEMENT_V1 (Live Alpha Lab, Mode A SIGNAL SHADOW).

Réimplémentation (pas un import) de la logique de
reports/edge_discovery/alpha_hunt_2026-08-30/w4_calendar_basis/build_panel.py
(lu comme référence, ce fichier de recherche est read-only et ne doit jamais
être modifié ni importé -- section "HARD RULES" de la mission). Les formules
(basis annualisé, funding annualisé) sont IDENTIQUES ; deux différences
volontaires et documentées dans freeze_spec.json :

1. Jambe perp/prix : build_panel.py (recherche) utilise le kline perp
   quotidien de data/derivatives_backfill/um_klines_1d/ (figé, arrêté au
   2026-06-30 -- confirmé mort lors de l'investigation de fraîcheur). Ce
   module utilise à la place `mark_price` du flux LIVE
   data/derivatives_raw/.../stream=open_interest/symbol=<SYM>/ (issu du même
   endpoint Binance premiumIndex que funding_rate, sondé en continu par le
   collecteur scripts/run_derivatives_collector.py). Agrégation quotidienne :
   dernière valeur de mark_price observée dans la journée (proxy du close).
2. Jambe funding : idem, utilise funding_rate du flux LIVE ci-dessus (moyenne
   quotidienne, même agrégation que l'original) plutôt que le backfill
   statique data/derivatives_backfill/binance/funding/ (arrêté le
   2026-08-14 -- confirmé stale).

La jambe trimestrielle (contrats binance_vision_quarterly) est, elle,
réellement tenue à jour en continu par le timer systemd existant
futur-basis.timer (scripts/backfill_binance_quarterly_vision.py, quotidien
09:15 CEST) -- vérifié lors de l'investigation de fraîcheur, aucune action
requise.

Historique disponible pour le flux live funding/mark_price : seulement depuis
2026-06-28 (~2 mois, début de la collecte actuelle) -- BEAUCOUP plus court que
l'historique multi-année des contrats trimestriels. Les seuils de régime
restent néanmoins FIGÉS (voir disagreement.py), jamais recalculés depuis cette
fenêtre courte -- ce serait du data-snooping sur un échantillon minuscule.
"""
from __future__ import annotations

import glob
import json
from pathlib import Path
from typing import Optional

import pandas as pd

ROOT = Path(__file__).resolve().parents[4]
QDIR = ROOT / "data" / "derivatives_backfill" / "binance_vision_quarterly"
RAW_ROOT = ROOT / "data" / "derivatives_raw"

MIN_DTE = 7  # identique à w4_calendar_basis/analyze.py::MIN_DTE (pitfall #4 : basis
             # annualisé explose près de l'expiry, floor sur near_dte pour tout usage
             # (fit de seuil ET classification en shadow live).


def load_quarterly_near(symbol: str) -> pd.DataFrame:
    """Contrat trimestriel 'near' (dte positif minimal) par jour, causal.

    Réplique exactement la sélection de build_panel.py::load_quarterly +
    build() (near = day.sort_values("dte").iloc[0]) : troncature date<=expiry
    (les fichiers backfill sont déjà tronqués par ce fix, réappliqué ici par
    prudence comme la recherche l'a fait), trim des closes dupliqués en fin de
    série (stale forward-fill post-expiry), dte = (expiry-date).days,
    dte>0 strict.
    """
    contracts_path = QDIR / "contracts.json"
    if not contracts_path.exists():
        return pd.DataFrame(columns=["date", "near_contract", "near_close", "near_expiry", "near_dte"])
    contracts = json.loads(contracts_path.read_text())

    frames = []
    for c, meta in contracts.items():
        if meta["symbol"] != symbol:
            continue
        f = QDIR / f"{c}_1d.parquet"
        if not f.exists():
            continue
        df = pd.read_parquet(f)
        df["date"] = pd.to_datetime(df["date"]).dt.tz_convert("UTC").dt.normalize()
        expiry = pd.Timestamp(meta["expiry"], tz="UTC")
        df = df[df["date"] <= expiry].copy()
        df = df.sort_values("date")
        keep = len(df)
        while keep > 1 and df["close"].iloc[keep - 1] == df["close"].iloc[keep - 2]:
            keep -= 1
        df = df.iloc[:keep]
        df["contract"] = c
        df["expiry"] = expiry
        df["dte"] = (expiry - df["date"]).dt.days
        df = df[df["dte"] > 0]
        frames.append(df[["date", "contract", "close", "expiry", "dte"]])

    if not frames:
        return pd.DataFrame(columns=["date", "near_contract", "near_close", "near_expiry", "near_dte"])

    q = pd.concat(frames, ignore_index=True)
    rows = []
    for d, day in q.groupby("date"):
        day = day.sort_values("dte")
        near = day.iloc[0]
        rows.append({
            "date": d, "near_contract": near["contract"], "near_close": near["close"],
            "near_expiry": near["expiry"], "near_dte": near["dte"],
        })
    out = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
    return out


def load_live_perp_funding_daily(symbol: str) -> pd.DataFrame:
    """Agrégat quotidien CAUSAL du flux live derivatives_raw (stream
    open_interest, qui porte aussi mark_price/funding_rate -- issus du même
    poll REST premiumIndex, voir collector.py::_poll_rest_once).

    funding_rate : moyenne quotidienne (même agrégation que l'original
    build_panel.py::load_funding -- groupby(date).mean()).
    mark_price : DERNIÈRE valeur observée dans la journée (proxy du close
    perp, la jambe perp du backfill original étant stale -- voir docstring du
    module). Chaque jour n'utilise QUE ses propres timestamps intra-journée,
    aucune info du futur.
    """
    part_dir = RAW_ROOT / "exchange=binance" / "market=usdm" / "stream=open_interest" / f"symbol={symbol}"
    files = sorted(glob.glob(str(part_dir / "date=*" / "part-*.parquet")))
    if not files:
        return pd.DataFrame(columns=["date", "funding_rate_mean", "perp_close"])

    frames = []
    for f in files:
        try:
            frames.append(pd.read_parquet(f, columns=["timestamp", "funding_rate", "mark_price"]))
        except Exception:
            continue  # part corrompue/partielle -- ignorée, pas fatale pour le panel
    if not frames:
        return pd.DataFrame(columns=["date", "funding_rate_mean", "perp_close"])

    df = pd.concat(frames, ignore_index=True)
    df["ts"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df["date"] = df["ts"].dt.normalize()
    df = df.sort_values("ts")

    fund = df.groupby("date")["funding_rate"].mean().rename("funding_rate_mean")
    # dernière valeur de mark_price observée par jour = proxy causal du "close"
    perp = df.groupby("date")["mark_price"].last().rename("perp_close")
    out = pd.concat([fund, perp], axis=1).reset_index()
    return out


def build_panel(symbol: str, quarterly: Optional[pd.DataFrame] = None,
                 live_daily: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """Panel quotidien fusionné, causal, avec les colonnes signal.

    `quarterly`/`live_daily` injectables pour les tests (évite de dépendre du
    disque). Colonnes de sortie : date, symbol, near_contract, near_close,
    near_expiry, near_dte, funding_rate_mean, perp_close, basis_near_pct,
    basis_near_ann, funding_ann_pct, disagreement -- puis filtré à
    near_dte>=MIN_DTE (éligibilité, identique à w4/analyze.py).
    """
    q = quarterly if quarterly is not None else load_quarterly_near(symbol)
    live = live_daily if live_daily is not None else load_live_perp_funding_daily(symbol)

    if q.empty or live.empty:
        return pd.DataFrame(columns=["date", "symbol", "near_contract", "near_close",
                                     "near_expiry", "near_dte", "funding_rate_mean",
                                     "perp_close", "basis_near_pct", "basis_near_ann",
                                     "funding_ann_pct", "disagreement"])

    panel = q.merge(live, on="date", how="inner").sort_values("date").reset_index(drop=True)
    panel["symbol"] = symbol

    panel["basis_near_pct"] = (panel["near_close"] / panel["perp_close"] - 1.0) * 100.0
    panel["basis_near_ann"] = panel["basis_near_pct"] * (365.0 / panel["near_dte"])
    # funding 8h -> annualisé, IDENTIQUE à build_panel.py (3 périodes/jour * 365)
    panel["funding_ann_pct"] = panel["funding_rate_mean"] * 3 * 365 * 100.0
    panel["disagreement"] = panel["funding_ann_pct"] - panel["basis_near_ann"]

    panel = panel[panel["near_dte"] >= MIN_DTE].copy()
    return panel.reset_index(drop=True)
