#!/usr/bin/env python3
"""
frontend_pipeline/command_center.py
─────────────────────────────────────────────────────────────────────────────
FUTUR // COMMAND CENTER — poste de contrôle UNIQUE : portefeuille (3 jambes
depuis 2021), cryptos en temps réel + prévisions de l'utilisateur, panneau LIVE,
World Monitor, LABORATOIRE de croisement de données.

  .venv/bin/uvicorn frontend_pipeline.command_center:app --port 8899

Lecture seule SAUF les prévisions utilisateur (ses propres annotations, stockées
en Mongo — aucun ordre, rien d'envoyé à l'extérieur). Caches TTL.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[1]
LC = ROOT / "reports" / "liq_cascade"
STATIC = Path(__file__).parent / "static"

app = FastAPI(title="FUTUR Command Center")
_cache: Dict[str, tuple] = {}

# localhost par défaut (run natif) ; surchargé en Docker (mongodb://mongodb:27017)
MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")


def _universe() -> List[str]:
    return yaml.safe_load(
        (ROOT / "configs/portfolio_v1_1_parallel_50.yaml").read_text())["universe"]


def _forecast_db():
    """Collection Mongo des prévisions utilisateur (None si Mongo indispo)."""
    try:
        import pymongo
        c = pymongo.MongoClient(MONGO_URL, serverSelectionTimeoutMS=1500)
        c.admin.command("ping")
        return c["futur_ui"].forecasts
    except Exception:
        return None


def _clean(o):
    """NaN/inf → None récursivement (JSON strict)."""
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (float, np.floating)) and not np.isfinite(o):
        return None
    if isinstance(o, np.generic):
        return o.item()
    return o


def cached(key: str, ttl: float, fn):
    now = time.time()
    if key in _cache and now - _cache[key][0] < ttl:
        return _cache[key][1]
    v = fn()
    _cache[key] = (now, v)
    return v


# ── jambes / équités ─────────────────────────────────────────────────────────

def _stack_equity() -> Optional[pd.Series]:
    import sys
    sys.path.insert(0, str(ROOT))
    try:
        from scripts.measure_v12_plus_stack_overlay import stack_equity_daily
        return stack_equity_daily()
    except Exception:
        return None


def _legs() -> Dict[str, pd.Series]:
    def load():
        legs = {}
        p = LC / "v12_equity_daily.parquet"
        if p.exists():
            df = pd.read_parquet(p)
            legs["V1.2"] = pd.Series(df["equity"].values,
                                     index=pd.to_datetime(df["date"], utc=True))
        p = LC / "basis_term_equity_daily.parquet"
        if p.exists():
            df = pd.read_parquet(p)
            legs["BASIS"] = pd.Series(df["equity"].values,
                                      index=pd.to_datetime(df["date"], utc=True))
        s = _stack_equity()
        if s is not None and len(s):
            legs["STACK"] = s
        return {k: v.sort_index().resample("D").last().ffill()
                for k, v in legs.items()}
    return cached("legs", 300, load)


def _stats(eq: pd.Series) -> dict:
    r = eq.pct_change().dropna()
    dd = float(((eq - eq.cummax()) / eq.cummax()).min())
    yrs = max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-6)
    return {"roi_total": round(float(eq.iloc[-1] / eq.iloc[0] - 1), 4),
            "roi_ann": round(float((eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1), 4),
            "maxdd": round(dd, 4),
            "sharpe": round(float(r.mean() / max(r.std(), 1e-12) * np.sqrt(365)), 2)}


@app.get("/api/equity")
def api_equity():
    legs = _legs()
    if not legs:
        return {"dates": [], "series": {}}
    start = min(s.index[0] for s in legs.values())
    end = max(s.index[-1] for s in legs.values())
    idx = pd.date_range(start, end, freq="D", tz="UTC")
    out = {}
    for k, s in legs.items():
        x = s.reindex(idx).ffill()
        first = x.first_valid_index()
        x = x / x[first]
        out[k] = [None if pd.isna(v) else round(float(v), 5) for v in x]
    common = [s.index[0] for s in legs.values()]
    cstart = max(common)
    combo = None
    for s in legs.values():
        x = s.reindex(idx).ffill()
        x = x / x[x.index >= cstart].iloc[0] if (x.index >= cstart).any() else x
        x[x.index < cstart] = np.nan
        combo = x if combo is None else combo * x
    out["COMBINÉ"] = [None if pd.isna(v) else round(float(v), 5) for v in combo]
    return {"dates": [str(d.date()) for d in idx], "series": out}


@app.get("/api/summary")
def api_summary():
    def load():
        legs = _legs()
        rows = {k: _stats(v) for k, v in legs.items()}
        if legs:
            cstart = max(s.index[0] for s in legs.values())
            cend = min(s.index[-1] for s in legs.values())
            idx = pd.date_range(cstart, cend, freq="D", tz="UTC")
            combo, rets = None, {}
            for k, s in legs.items():
                x = s.reindex(idx).ffill()
                x = x / x.iloc[0]
                rets[k] = x.pct_change()
                combo = x if combo is None else combo * x
            rows["COMBINÉ"] = _stats(combo)
            corr = pd.DataFrame(rets).dropna().corr().round(3)
            corr_d = {a: {b: float(corr.loc[a, b]) for b in corr.columns}
                      for a in corr.index}
        else:
            corr_d = {}
        # verdicts moteurs
        verdicts = {}
        for f in LC.glob("*_WALKFORWARD*.json"):
            try:
                d = json.loads(f.read_text())
                if "verdict" in d and "engine" in d:
                    verdicts[d["engine"]] = d["verdict"]
            except Exception:
                pass
        # shadow countdown
        shadow = {}
        st = LC / "shadow" / "state.json"
        if st.exists():
            s = json.loads(st.read_text())
            started = pd.Timestamp(s.get("shadow_started"))
            days = (pd.Timestamp.now(tz="UTC") - started).days
            shadow = {"day": days, "target": 30,
                      "verdict_date": str((started + pd.Timedelta(days=30)).date())}
        return {"legs": rows, "corr": corr_d, "verdicts": verdicts,
                "shadow": shadow}
    return cached("summary", 300, load)


# ── LIVE ─────────────────────────────────────────────────────────────────────

@app.get("/api/live")
def api_live():
    def load():
        out = {"ts": pd.Timestamp.now(tz="UTC").isoformat()}
        # paper fleet TRM legacy
        p = ROOT / "reports" / "paper_trading" / "fleet_summary.json"
        if p.exists():
            try:
                out["paper_fleet"] = json.loads(p.read_text())
            except Exception:
                pass
        # collecteur : lignes du jour par exchange (manifests, pas les parquets)
        today = pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d")
        coll = {}
        raw = ROOT / "data" / "derivatives_raw"
        for ex_dir in raw.glob("exchange=*"):
            n = 0
            for mf in ex_dir.glob(f"market=*/stream=force_order/symbol=*/date={today}/*.manifest.json"):
                try:
                    n += json.loads(mf.read_text()).get("rows", 0)
                except Exception:
                    pass
            coll[ex_dir.name.split("=")[1]] = n
        out["liquidations_today"] = coll
        # shadow ledger
        led = LC / "shadow" / "decisions.parquet"
        if led.exists():
            df = pd.read_parquet(led)
            t = df.get("tier")
            df = df[(pd.Series("book", index=df.index) if t is None
                     else t.fillna("book")) == "book"]   # probes hors P&L
            lab = df[np.isfinite(df.get("net_labeled", np.nan))]
            sh = {"decisions": int(len(df)), "labeled": int(len(lab))}
            if len(lab):
                net = lab["net_labeled"].values
                sh["pf"] = round(float(net[net > 0].sum()
                                       / max(abs(net[net < 0].sum()), 1e-9)), 2)
                sh["mean_bps"] = round(float(net.mean() * 1e4), 1)
            tail = df.sort_values("event_time").tail(8)
            sh["latest"] = [
                {"t": str(r["event_time"])[:16], "symbol": r["symbol"],
                 "engine": r["engine"], "score": round(float(r["score"]), 3),
                 "net_bps": (None if pd.isna(r["net_labeled"])
                             else round(float(r["net_labeled"]) * 1e4, 1))}
                for _, r in tail.iterrows()]
            out["shadow"] = sh
        # services
        units = ["futur-api", "futur-derivatives", "futur-paper-v1",
                 "futur-event-shadow.timer", "futur-event-retrain.timer"]
        svc = {}
        for u in units:
            try:
                r = subprocess.run(["systemctl", "--user", "is-active", u],
                                   capture_output=True, text=True, timeout=5)
                svc[u] = r.stdout.strip()
            except Exception:
                svc[u] = "unknown"
        out["services"] = svc
        return _clean(out)
    return cached("live", 10, load)


# ── EDGE LAB : croisement de données ────────────────────────────────────────

SOURCES = ["price", "open_interest", "funding", "premium", "taker_ratio",
           "toptrader_ratio", "liq_usd", "basis_ann",
           "fear_greed", "news_sent", "news_vol",
           "gdelt_tone", "gdelt_vol", "btc_dominance", "total_mcap_usd",
           "quake_energy"]

_WORLD_FEATURES = {"gdelt_tone", "gdelt_vol", "btc_dominance",
                   "total_mcap_usd", "quake_energy"}


def _daily_source(source: str, symbol: str) -> Optional[pd.Series]:
    if source == "price":
        import sys; sys.path.insert(0, str(ROOT))
        from src.institutional.engines.legacy_bridge import load_enriched
        df = load_enriched(symbol, required_cols=["close"])
        if df is None:
            return None
        s = df.set_index(pd.to_datetime(df["datetime"], utc=True))["close"]
        return s.resample("D").last()
    if source in ("open_interest", "taker_ratio", "toptrader_ratio"):
        p = (ROOT / "data" / "derivatives_backfill" / "binance_vision_metrics"
             / f"{symbol}_metrics_5m.parquet")
        if not p.exists():
            return None
        col = {"open_interest": "sum_open_interest",
               "taker_ratio": "sum_taker_long_short_vol_ratio",
               "toptrader_ratio": "sum_toptrader_long_short_ratio"}[source]
        df = pd.read_parquet(p, columns=["create_time", col])
        s = df.set_index(pd.to_datetime(df["create_time"], utc=True))[col]
        return s.resample("D").last()
    if source == "funding":
        p = ROOT / "data" / "derivatives_backfill" / "binance" / "funding" / f"{symbol}.parquet"
        if not p.exists():
            return None
        df = pd.read_parquet(p)
        s = df.set_index(pd.to_datetime(df["timestamp"], utc=True))["funding_rate"]
        return s.resample("D").mean()
    if source == "premium":
        p = (ROOT / "data" / "derivatives_backfill" / "binance_vision_premium"
             / f"{symbol}_premium_5m.parquet")
        if not p.exists():
            return None
        df = pd.read_parquet(p, columns=["ts", "premium"])
        s = df.set_index(pd.to_datetime(df["ts"], utc=True))["premium"]
        return s.resample("D").mean()
    if source == "liq_usd":
        rows = []
        raw = ROOT / "data" / "derivatives_raw"
        for p in raw.glob(f"exchange=*/market=*/stream=force_order/symbol={symbol}/date=*/part-*.parquet"):
            try:
                rows.append(pd.read_parquet(p, columns=["timestamp", "usd"]))
            except Exception:
                pass
        if not rows:
            return None
        df = pd.concat(rows)
        s = df.set_index(pd.to_datetime(df["timestamp"], unit="ms", utc=True))["usd"]
        return s.resample("D").sum()
    if source == "fear_greed":
        p = ROOT / "data" / "news_backfill" / "fear_greed.parquet"
        if not p.exists():
            return None
        df = pd.read_parquet(p)
        s = pd.Series(df["fear_greed"].values,
                      index=pd.to_datetime(df["date"], utc=True))
        return s.resample("D").last()
    if source in ("news_sent", "news_vol"):
        fn = "news_daily_sent.parquet" if source == "news_sent" else "news_daily_vol.parquet"
        p = ROOT / "data" / "news_backfill" / fn
        if not p.exists() or symbol not in pd.read_parquet(p).columns:
            return None
        df = pd.read_parquet(p)
        s = pd.Series(df[symbol].values, index=pd.to_datetime(df.index, utc=True))
        return s.resample("D").mean() if source == "news_sent" else s.resample("D").sum()
    if source in _WORLD_FEATURES:
        p = ROOT / "data" / "worldmon_features" / "world_daily_features.parquet"
        if not p.exists():
            return None
        df = pd.read_parquet(p)
        if source not in df.columns:
            return None
        return pd.Series(df[source].values,
                         index=pd.to_datetime(df.index, utc=True)).resample("D").mean()
    if source == "basis_ann":
        qdir = ROOT / "data" / "derivatives_backfill" / "binance_vision_quarterly"
        reg_p = qdir / "contracts.json"
        if not reg_p.exists():
            return None
        reg = json.loads(reg_p.read_text())
        spot = _daily_source("price", symbol)
        if spot is None:
            return None
        frames = []
        for c, meta in reg.items():
            if meta["symbol"] != symbol:
                continue
            pq = qdir / f"{c}_1d.parquet"
            if not pq.exists():
                continue
            df = pd.read_parquet(pq)
            df["date"] = pd.to_datetime(df["date"], utc=True).dt.floor("D")
            expiry = pd.Timestamp(meta["expiry"], tz="UTC")
            F = df.set_index("date")["close"]
            idx = F.index.intersection(spot.index)
            dl = np.maximum((expiry - idx).days, 1)
            ann = (F[idx] / spot[idx] - 1) * 365 / dl
            keep = (dl >= 10) & (dl <= 120)
            frames.append(ann[keep])
        if not frames:
            return None
        return pd.concat(frames).groupby(level=0).mean().sort_index()
    return None


@app.get("/api/cross")
def api_cross(x: str, y: str, symbol: str = "BTCUSDT",
              symbol_y: str = "", lag_days: int = 0):
    if x not in SOURCES or y not in SOURCES:
        raise HTTPException(400, f"sources valides : {SOURCES}")
    sy = symbol_y or symbol
    key = f"cross:{x}:{y}:{symbol}:{sy}:{lag_days}"

    def load():
        a = _daily_source(x, symbol)
        b = _daily_source(y, sy)
        if a is None or b is None:
            raise HTTPException(404, "source indisponible pour ce symbole")
        if lag_days:
            b = b.shift(lag_days)   # y retardé : x_t vs y_{t-lag} (prédictivité)
        df = pd.DataFrame({"x": a, "y": b}).dropna()
        if len(df) < 30:
            raise HTTPException(404, "moins de 30 points communs")
        pearson = float(df["x"].corr(df["y"]))
        # corrélation des VARIATIONS (souvent la seule qui compte)
        dchg = df.pct_change().replace([np.inf, -np.inf], np.nan).dropna()
        pearson_chg = float(dchg["x"].corr(dchg["y"])) if len(dchg) > 30 else None
        # indexation base 100 pour affichage UN SEUL axe
        xi = df["x"] / df["x"].iloc[0] * 100
        yi = df["y"] / df["y"].iloc[0] * 100 if (df["y"].iloc[0] or 0) != 0 else df["y"]
        return {"dates": [str(d.date()) for d in df.index],
                "x_indexed": [round(float(v), 3) for v in xi],
                "y_indexed": [round(float(v), 3) for v in yi],
                "x_label": f"{x}({symbol})", "y_label": f"{y}({sy})" +
                           (f" lag{lag_days}j" if lag_days else ""),
                "pearson_niveaux": round(pearson, 3),
                "pearson_variations": (None if pearson_chg is None
                                       else round(pearson_chg, 3)),
                "n": int(len(df))}
    return cached(key, 300, load)


@app.get("/api/worldmon")
def api_worldmon():
    def load():
        try:
            from src.institutional.worldmon.bigdata_store import BigDataStore
            store = BigDataStore()
            health = store.latest_doc("agent_health") or {}
            cand = store.latest_doc("signal_candidates") or {}
            qual = store.latest_doc("quality_reports") or {}
            for d in (health, cand, qual):
                d.pop("_id", None)
            return _clean({
                "backend": store.backend,
                "total_events": store.count(),
                "pipeline_healthy": health.get("pipeline_healthy"),
                "heartbeat": health.get("heartbeat"),
                "agents": health.get("agents", {}),
                "quality": {k: qual.get(k) for k in
                            ("data_trustworthy", "source_coverage",
                             "dedup_ratio", "volume_z_today", "n_events_90d")},
                "candidates": cand.get("candidates", []),
                "candidates_warning": cand.get("WARNING"),
                "n_tests": cand.get("n_tests"),
            })
        except Exception as e:
            return {"backend": "unavailable", "error": str(e)}
    return cached("worldmon", 30, load)


@app.get("/api/sources")
def api_sources():
    syms = sorted(p.stem.replace("_metrics_5m", "") for p in
                  (ROOT / "data" / "derivatives_backfill"
                   / "binance_vision_metrics").glob("*_metrics_5m.parquet"))
    return {"sources": SOURCES, "symbols": syms}


# ── CRYPTOS : prix live + historique + prévisions utilisateur ────────────────

def _live_tickers() -> Dict[str, dict]:
    """Batch Binance spot 24h (public) → {symbol: {price, chg24}}, caché 15 s."""
    def load():
        try:
            req = urllib.request.Request(
                "https://api.binance.com/api/v3/ticker/24hr",
                headers={"User-Agent": "futur-cc/1.0"})
            data = json.loads(urllib.request.urlopen(req, timeout=15).read())
            uni = set(_universe())
            out = {}
            for t in data:
                s = t.get("symbol")
                if s in uni:
                    out[s] = {"price": float(t["lastPrice"]),
                              "chg24": float(t["priceChangePercent"]),
                              "vol": float(t.get("quoteVolume", 0))}
            return out
        except Exception:
            return {}
    return cached("tickers", 15, load)


def _daily_close(symbol: str, tail: int = 180) -> Optional[pd.Series]:
    def load():
        try:
            from src.institutional.engines.legacy_bridge import load_enriched
            df = load_enriched(symbol, required_cols=["close"])
            s = df.set_index(pd.to_datetime(df["datetime"], utc=True))["close"]
            return s.resample("D").last().dropna()
        except Exception:
            return None
    s = cached(f"close:{symbol}", 3600, load)
    return s.tail(tail) if s is not None else None


@app.get("/api/universe")
def api_universe():
    tk = _live_tickers()
    fdb = _forecast_db()
    fc = {d["_id"]: d for d in fdb.find()} if fdb is not None else {}
    rows = []
    for sym in _universe():
        s = _daily_close(sym, tail=60)
        spark = [round(float(v), 6) for v in s.values] if s is not None else []
        last_close = float(s.iloc[-1]) if s is not None and len(s) else None
        live = tk.get(sym, {})
        f = fc.get(sym)
        rows.append({
            "symbol": sym, "price": live.get("price") or last_close,
            "chg24": live.get("chg24"), "vol": live.get("vol"),
            "spark": spark,
            "forecast": None if not f else {
                "direction": f.get("direction"), "target": f.get("target"),
                "horizon_days": f.get("horizon_days"),
                "conviction": f.get("conviction"), "note": f.get("note")},
        })
    return _clean({"assets": rows, "live": bool(tk),
                   "ts": datetime.now(timezone.utc).isoformat()})


@app.get("/api/crypto/{symbol}")
def api_crypto(symbol: str):
    symbol = symbol.upper()
    if symbol not in _universe():
        raise HTTPException(404, "hors univers")
    s = _daily_close(symbol, tail=720)
    if s is None or not len(s):
        raise HTTPException(404, "pas d'historique")
    live = _live_tickers().get(symbol, {})
    fdb = _forecast_db()
    f = fdb.find_one({"_id": symbol}) if fdb is not None else None
    if f:
        f.pop("_id", None)
    return _clean({
        "symbol": symbol,
        "dates": [str(d.date()) for d in s.index],
        "close": [round(float(v), 6) for v in s.values],
        "price": live.get("price") or float(s.iloc[-1]),
        "chg24": live.get("chg24"),
        "forecast": f,
    })


class Forecast(BaseModel):
    symbol: str
    direction: str = "neutral"      # up | down | neutral
    target: Optional[float] = None
    horizon_days: Optional[int] = None
    conviction: int = 3             # 1..5
    note: str = ""


@app.get("/api/forecasts")
def api_forecasts():
    fdb = _forecast_db()
    if fdb is None:
        return {"forecasts": [], "backend": "unavailable"}
    out = []
    for d in fdb.find():
        d["symbol"] = d.pop("_id")
        out.append(d)
    return _clean({"forecasts": out, "backend": "mongodb"})


@app.post("/api/forecast")
def api_set_forecast(f: Forecast):
    sym = f.symbol.upper()
    if sym not in _universe():
        raise HTTPException(400, "symbole hors univers")
    if f.direction not in ("up", "down", "neutral"):
        raise HTTPException(400, "direction : up|down|neutral")
    fdb = _forecast_db()
    if fdb is None:
        raise HTTPException(503, "MongoDB indisponible")
    doc = {"_id": sym, "direction": f.direction, "target": f.target,
           "horizon_days": f.horizon_days, "conviction": max(1, min(5, f.conviction)),
           "note": f.note[:500], "updated_at": datetime.now(timezone.utc).isoformat()}
    fdb.replace_one({"_id": sym}, doc, upsert=True)
    return {"ok": True, "symbol": sym}


@app.delete("/api/forecast/{symbol}")
def api_del_forecast(symbol: str):
    fdb = _forecast_db()
    if fdb is None:
        raise HTTPException(503, "MongoDB indisponible")
    fdb.delete_one({"_id": symbol.upper()})
    return {"ok": True}


# ── PORTEFEUILLE PAPER LIVE 200 000 € (forward réel, marqué au prix live) ────

def _paper():
    from src.institutional.live.paper_portfolio import PaperPortfolio
    try:
        import pymongo
        c = pymongo.MongoClient(MONGO_URL, serverSelectionTimeoutMS=1500)
        c.admin.command("ping")
        return PaperPortfolio(c["futur_ui"])
    except Exception:
        return PaperPortfolio(None)


class InitReq(BaseModel):
    policy: str = "core"        # core | forecasts


@app.get("/api/portfolio/live")
def api_portfolio_live():
    pp = _paper()
    if not pp.exists():
        return {"exists": False,
                "hint": "portefeuille non initialisé — POST /api/portfolio/init"}
    return _clean(pp.mark_to_market())


@app.post("/api/portfolio/init")
def api_portfolio_init(req: InitReq):
    pp = _paper()
    if pp.col is None:
        raise HTTPException(503, "MongoDB indisponible")
    if req.policy in ("strategy", "aggressive", "max", "adaptive"):
        preset = {"strategy": "calm", "aggressive": "aggressive",
                  "max": "max", "adaptive": "adaptive"}[req.policy]
        return _clean(pp.initialize_strategy(preset=preset))
    forecasts = None
    if req.policy == "forecasts":
        fdb = _forecast_db()
        if fdb is not None:
            forecasts = [{**d, "symbol": d["_id"]} for d in fdb.find()]
    return _clean(pp.initialize(policy=req.policy, forecasts=forecasts))


@app.get("/api/portfolio/history")
def api_portfolio_history():
    return _clean({"history": _paper().history()})


@app.get("/api/portfolio/events")
def api_portfolio_events(limit: int = 30):
    """Événements de notification (gros gains/pertes, flips, rolls, rebalances)
    émis par le moteur paper — consommés par le PWA (toast + Notification)."""
    pp = _paper()
    if pp.events is None:
        return {"events": [], "backend": "unavailable"}
    out = []
    for d in pp.events.find().sort("ts", -1).limit(max(1, min(limit, 100))):
        d.pop("_id", None)
        out.append(d)
    return _clean({"events": out})


# ── EVENT STRATEGY — paper trading LIVE (multi-horizon consensus, shadow) ────

@app.get("/api/event_book")
def api_event_book():
    def load():
        led = LC / "shadow" / "decisions.parquet"
        st = LC / "shadow" / "state.json"
        if not led.exists():
            return {"exists": False, "hint": "shadow pas encore exécuté"}
        df = pd.read_parquet(led)
        df["event_time"] = pd.to_datetime(df["event_time"], utc=True)
        df = df.sort_values("event_time")
        SIZE = 0.05                       # 5% par décision (choix user, paper only —
                                          # ~Kelly/4 ; +18.9%/an mais maxDD -8.4% en backtest)
        # tier 'probe' (0.50-0.70) = accumulation de labels UNIQUEMENT ; le
        # book officiel (P&L, courbe, verdict) ne compte que le tier 'book'.
        tiers = df.get("tier")
        tiers = (pd.Series("book", index=df.index) if tiers is None
                 else tiers.fillna("book"))
        probes = df[tiers == "probe"]
        df = df[tiers == "book"]
        labeled = df[np.isfinite(df.get("net_labeled", np.nan))]
        pending = df[~np.isfinite(df.get("net_labeled", np.nan))]
        # courbe d'équité paper (base 100) sur les trades clôturés
        eq, curve = 100.0, []
        for _, r in labeled.iterrows():
            eq *= (1 + float(r["net_labeled"]) * SIZE)
            curve.append({"t": str(r["event_time"])[:16], "v": round(eq, 3)})
        stats = {}
        if len(labeled):
            net = labeled["net_labeled"].values
            stats = {"n_closed": int(len(net)),
                     "pf": round(float(net[net > 0].sum() / max(abs(net[net < 0].sum()), 1e-9)), 3),
                     "wr": round(float((net > 0).mean()), 3),
                     "mean_bps": round(float(net.mean()) * 1e4, 1),
                     "equity": round(eq, 3), "roi_pct": round(eq - 100, 3)}
        # décisions récentes (statut)
        recent = df.sort_values("event_time").tail(15).iloc[::-1]
        rows = [{"t": str(r["event_time"])[:16], "symbol": r["symbol"],
                 "engine": r["engine"], "score": round(float(r["score"]), 3),
                 "status": ("clos" if np.isfinite(r.get("net_labeled", np.nan)) else "ouvert"),
                 "net_bps": (round(float(r["net_labeled"]) * 1e4, 0)
                             if np.isfinite(r.get("net_labeled", np.nan)) else None)}
                for _, r in recent.iterrows()]
        by_eng = {}
        for eng, g in labeled.groupby("engine"):
            n = g["net_labeled"].values
            by_eng[eng] = {"n": int(len(n)),
                           "pf": round(float(n[n > 0].sum() / max(abs(n[n < 0].sum()), 1e-9)), 2)}
        started = None
        if st.exists():
            started = json.loads(st.read_text()).get("shadow_started")
        fwd_days = 0
        if started:
            fwd_days = max(0, (pd.Timestamp.now(tz="UTC") - pd.Timestamp(started)).days)
        n_probe_lab = int(np.isfinite(probes.get(
            "net_labeled", pd.Series(dtype=float))).sum()) if len(probes) else 0
        return _clean({
            "exists": True, "sizing_pct": SIZE * 100,
            "n_total": int(len(df)), "n_closed": int(len(labeled)),
            "n_pending": int(len(pending)),
            "n_probe": int(len(probes)), "n_probe_labeled": n_probe_lab,
            "stats": stats, "curve": curve, "recent": rows, "by_engine": by_eng,
            "forward_days": fwd_days, "gate_days": 30,
            "note": "Paper live — consensus multi-horizon, sizing 5%, gating par CONFIANCE (score≥0.70, pas de cap de nombre : la capacité s'étend dans les grosses vagues). Tier PROBE (0.50-0.70) enregistré pour les labels seulement — n'entre ni dans le P&L ni dans le verdict. Aucun argent réel.",
        })
    return cached("event_book", 20, load)


@app.get("/", response_class=HTMLResponse)
def index():
    return (STATIC / "command_center.html").read_text()


# ── PWA : manifest, service worker, icônes ───────────────────────────────────

@app.get("/manifest.webmanifest", include_in_schema=False)
def manifest():
    return FileResponse(STATIC / "manifest.webmanifest",
                        media_type="application/manifest+json")


@app.get("/sw.js", include_in_schema=False)
def service_worker():
    # no-cache : le navigateur revalide le SW à chaque visite (déploiements)
    return FileResponse(STATIC / "sw.js", media_type="application/javascript",
                        headers={"Cache-Control": "no-cache"})


@app.get("/icons/{name}", include_in_schema=False)
def icon(name: str):
    icons_dir = (STATIC / "icons").resolve()
    p = (icons_dir / name).resolve()
    if p.parent != icons_dir or not p.is_file():
        raise HTTPException(404)
    return FileResponse(p, media_type="image/png",
                        headers={"Cache-Control": "public, max-age=86400"})
