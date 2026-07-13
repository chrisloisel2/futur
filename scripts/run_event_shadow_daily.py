#!/usr/bin/env python3
"""
scripts/run_event_shadow_daily.py
─────────────────────────────────────────────────────────────────────────────
SHADOW FORWARD quotidien des moteurs événementiels (le déverrouilleur de
promotion : ≥30 j de décisions GELÉES avant tout paper/live).

Chaque run (systemd timer quotidien) :
  1. top-up Vision metrics (J-2) sur l'univers 50 ;
  2. rebuild des datasets events (cascade/crowding/premium) ;
  3. modèles de PRODUCTION : chargés du registre (sha256 vérifié) s'ils ont
     <10 j, sinon ré-entraînés sur tout le passé et persistés ;
  4. events NOUVEAUX (event_time > dernier traité) : scorés, sélection au
     seuil top-20% val du modèle, wave-unitisation (gap 30 min, dédup
     symbole, top-3 par percentile inter-moteurs), décisions APPEND-ONLY
     dans le ledger ;
  5. labels des décisions passées remplis dès que la donnée existe ;
  6. rapport d'état : jours de shadow accumulés, PnL labellisé, verdict à 30 j.

⚠ Shadow sur données Vision J-2 : valide l'EDGE DU MODÈLE hors tape (ce qui
compte pour la promotion). Le déclenchement temps réel (feed liquidations
live) est la couche d'EXÉCUTION, branchée après.

Ledger : reports/liq_cascade/shadow/decisions.parquet (append-only)
État   : reports/liq_cascade/shadow/state.json
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.institutional.engines.event_production import (
    load_artifact, save_artifact, score, score_percentile,
    train_production_model)
from src.institutional.engines.liq_cascade.dataset import (
    FEATURES_V2, build_event_dataset)
from src.institutional.engines.liq_cascade.detector import METRICS_DIR, CascadeConfig

SHADOW_DIR = ROOT / "reports" / "liq_cascade" / "shadow"
LEDGER = SHADOW_DIR / "decisions.parquet"
STATE = SHADOW_DIR / "state.json"
COST_RT = 0.0014
TOP_FRAC = 0.20
WAVE_GAP = pd.Timedelta(minutes=30)
TOP_K = 3

SPECS = {
    "cascade": {"name": "LIQ_CASCADE", "horizon": "fwd_4h",
                "features": FEATURES_V2},
    "crowding": {"name": "CROWDING_REVERSAL", "horizon": "fwd_24h",
                 "features": FEATURES_V2},
    "premium": {"name": "PREMIUM_DISLOCATION", "horizon": "fwd_4h",
                "features": FEATURES_V2 + ["prem_at", "prem_z_at"]},
}


_MH_REG = ROOT / "artifacts" / "event_engines" / "multihorizon_registry.json"


def _load_mh(engine_name):
    """Charge l'artefact multi-horizon (MH_{engine}.pkl) si présent + sha256 OK."""
    import hashlib, pickle
    if not _MH_REG.exists():
        return None
    reg = json.loads(_MH_REG.read_text())
    if engine_name not in reg:
        return None
    p = ROOT / reg[engine_name]["path"]
    if not p.exists() or hashlib.sha256(p.read_bytes()).hexdigest() != reg[engine_name]["sha256"]:
        return None
    with open(p, "rb") as f:
        return pickle.load(f)


def _mh_consensus(art, ev_new):
    """Sélection CONSENSUS : les 3 horizons ≥ leur seuil. Retourne (mask, score_moyen)."""
    import numpy as np
    X = ev_new[art["features"]].values
    probs, mask = {}, np.ones(len(ev_new), dtype=bool)
    for h in art["horizons"]:
        p = np.mean([m.predict_proba(X)[:, 1] for m in art["models"][h]], axis=0)
        probs[h] = p
        mask &= (p >= art["thresholds"][h])
    mean_score = np.mean([probs[h] for h in art["horizons"]], axis=0)
    return mask, mean_score


def _detector(engine):
    if engine == "cascade":
        return None
    if engine == "crowding":
        from src.institutional.engines.crowding_reversal.detector import detect_washouts
        return detect_washouts
    from src.institutional.engines.premium_dislocation.detector import (
        detect_premium_dislocations)
    return detect_premium_dislocations


def topup_data():
    import yaml
    syms = ",".join(yaml.safe_load(
        (ROOT / "configs/portfolio_v1_1_parallel_50.yaml").read_text())["universe"])
    subprocess.run([sys.executable, str(ROOT / "scripts/backfill_binance_metrics_vision.py"),
                    "--symbols", syms, "--workers", "8"],
                   check=False, capture_output=True, timeout=1800)
    subprocess.run([sys.executable, str(ROOT / "scripts/backfill_binance_premium_vision.py")],
                   check=False, capture_output=True, timeout=1800)


def main():
    SHADOW_DIR.mkdir(parents=True, exist_ok=True)
    state = json.loads(STATE.read_text()) if STATE.exists() else {
        "last_processed": "2026-07-10T00:00:00+00:00",
        "shadow_started": datetime.now(timezone.utc).isoformat()}
    last = pd.Timestamp(state["last_processed"])
    print(f"shadow: dernier traité {last}", flush=True)

    print("1) top-up Vision…", flush=True)
    topup_data()

    symbols = sorted(p.stem.replace("_metrics_5m", "")
                     for p in METRICS_DIR.glob("*_metrics_5m.parquet"))
    new_rows, datasets = [], {}
    for eng, spec in SPECS.items():
        print(f"2) dataset {spec['name']}…", flush=True)
        ev = build_event_dataset(symbols, CascadeConfig(),
                                 detector_fn=_detector(eng))
        if ev.empty:
            continue
        datasets[eng] = ev
        new = ev[ev["event_time"] > last].copy()
        if new.empty:
            continue
        # PRIORITÉ au multi-horizon (consensus) si l'artefact MH existe
        mh = _load_mh(spec["name"])
        if mh is not None:
            import numpy as np
            new_lab = new[np.isfinite(new[mh["trade_h"]].values)] if mh["trade_h"] in new else new
            mask, mscore = _mh_consensus(mh, new)
            new = new[mask].copy()
            if new.empty:
                continue
            new["score"] = mscore[mask]
            new["rank_pct"] = new["score"]
            new["horizon"] = f"MH_consensus({','.join(mh['horizons'])})"
            print(f"   {spec['name']} MULTI-HORIZON consensus : {len(new)} décisions", flush=True)
        else:
            art = load_artifact(spec["name"], max_age_days=10)
            if art is None:
                art = train_production_model(ev[ev["event_time"] <= last],
                                             spec["features"], spec["horizon"],
                                             COST_RT, spec["name"])
                save_artifact(art)
            p = score(art, new)
            sel = p >= art["thresholds"][TOP_FRAC]
            new = new[sel].copy()
            if new.empty:
                continue
            new["score"] = p[sel]
            new["rank_pct"] = score_percentile(art, new["score"].values)
            new["horizon"] = spec["horizon"]
        new["engine"] = spec["name"]
        new_rows.append(new[["event_time", "symbol", "engine", "horizon",
                             "score", "rank_pct", "kind"]])

    # 4) wave-unitisation des nouveaux signaux + append ledger
    n_decisions = 0
    if new_rows:
        cand = pd.concat(new_rows, ignore_index=True).sort_values("event_time")
        times = cand["event_time"].values
        wid, w = 0, np.zeros(len(cand), dtype=int)
        for i in range(1, len(cand)):
            if (times[i] - times[i - 1]) > WAVE_GAP.to_timedelta64():
                wid += 1
            w[i] = wid
        cand["wave"] = w
        cand = (cand.sort_values(["wave", "rank_pct"], ascending=[True, False])
                    .drop_duplicates(subset=["wave", "symbol"], keep="first"))
        cand["k"] = cand.groupby("wave").cumcount()
        dec = cand[cand["k"] < TOP_K].drop(columns=["k"]).copy()
        dec["decided_at"] = datetime.now(timezone.utc).isoformat()
        dec["net_labeled"] = np.nan
        if LEDGER.exists():
            old = pd.read_parquet(LEDGER)
            dec = pd.concat([old, dec], ignore_index=True)
        dec.to_parquet(LEDGER, index=False)
        n_decisions = int((dec["decided_at"] == dec["decided_at"].iloc[-1]).sum())

    # 5) labels a posteriori (depuis les datasets frais)
    if LEDGER.exists():
        led = pd.read_parquet(LEDGER)
        led["event_time"] = pd.to_datetime(led["event_time"], utc=True)
        for eng, spec in SPECS.items():
            if eng not in datasets:
                continue
            ev = datasets[eng]
            m = led["engine"] == spec["name"]
            sub = led[m & led["net_labeled"].isna()]
            if sub.empty:
                continue
            j = sub.merge(
                ev[["event_time", "symbol", spec["horizon"]]],
                on=["event_time", "symbol"], how="left")
            led.loc[sub.index, "net_labeled"] = (
                j[spec["horizon"]].values - COST_RT)
        led.to_parquet(LEDGER, index=False)

        # 6) rapport d'état
        lab = led[np.isfinite(led["net_labeled"])]
        days = (pd.Timestamp.now(tz="UTC")
                - pd.Timestamp(state["shadow_started"])).days
        if len(lab):
            net = lab["net_labeled"].values
            pf = net[net > 0].sum() / max(abs(net[net < 0].sum()), 1e-9)
            print(f"SHADOW: jour {days} | décisions {len(led)} "
                  f"(labellisées {len(lab)}) | PF {pf:.2f} "
                  f"mean {net.mean()*1e4:+.1f}bps | verdict à J30 : "
                  f"{'ATTEINT — évaluer promotion' if days >= 30 else f'{30-days} j restants'}",
                  flush=True)
        else:
            print(f"SHADOW: jour {days} | {len(led)} décisions, aucune "
                  f"labellisée encore", flush=True)

    if new_rows:
        state["last_processed"] = str(
            pd.concat(new_rows)["event_time"].max())
    STATE.write_text(json.dumps(state, indent=2))
    print(f"nouvelles décisions ce run : {n_decisions} → {LEDGER}", flush=True)


if __name__ == "__main__":
    main()
