#!/usr/bin/env python3
"""
depth_capacity_features.py -- la capacite executable autour de chaque lancement H2, derivee des archives
Vision deja sur disque (bookDepth pour le carnet, aggTrades pour le mid et le spread effectif).

Ce module ne calcule AUCUN retour de prix et ne dit jamais "tradable" au sens d'une strategie : il dit si
la capacite est MESUREE, et ce que le carnet supportait a chaque instant. Les archives brutes restent
ignorees par git ; seules les features et les rapports sont versionnes.

Ce que le format Vision permet, et rien de plus :
  - profondeur cumulee a +-20 bps et +-1..5 % (pas de meilleur bid/ask, pas de mid) ;
  - un mid PROXY et un spread effectif PROXY depuis les trades signes (is_buyer_maker) ;
  - sous 20 bps : None ; entre deux bandes : borne inferieure ; glissement : borne superieure + estimation lineaire.
"""
from __future__ import annotations

import csv
import io
import json
import math
import statistics
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from data_lake.indices import book_depth_parser as BD

ROOT = Path(__file__).resolve().parents[2]
VISION = ROOT / "data" / "vision_backfill" / "um"
MANIFESTS = ROOT / "data" / "vision_backfill" / "manifests"

WINDOWS_MIN = (0, 1, 5, 15, 30, 60)
NOTIONALS_USD = (100, 500, 1000)
BANDS_REQUESTED = (10, 25, 50)
SPREAD_TOO_WIDE_BPS = 50.0
DEPTH_TOO_THIN_USD = 1000.0            # un ordre de 1 000 USDT doit tenir dans +-20 bps de chaque cote
SNAPSHOT_TOLERANCE_MS = 120_000
TRADE_BUCKET_MS = 60_000
STATUSES = ("CAPACITY_OK", "SPREAD_TOO_WIDE", "DEPTH_TOO_THIN", "NO_DEPTH", "BAD_BOOK", "UNKNOWN")


# ----------------------------------------------------------------------------- trades (mid et spread proxy)
def load_trades(zip_path: Path) -> List[Tuple[int, float, float, bool]]:
    """(ts_ms, price, qty, is_buyer_maker) ; l'en-tete est saute. Aucun retour n'est calcule ici."""
    out = []
    try:
        with zipfile.ZipFile(zip_path) as z:
            for name in z.namelist():
                with z.open(name) as fh:
                    for row in csv.reader(io.TextIOWrapper(fh, encoding="utf-8", errors="replace")):
                        if len(row) < 7 or not row[5].strip().isdigit():
                            continue
                        ts = int(row[5]); ts = ts // 1000 if ts > 10 ** 14 else ts
                        try:
                            out.append((ts, float(row[1]), float(row[2]), row[6].strip().lower() == "true"))
                        except ValueError:
                            continue
    except (zipfile.BadZipFile, OSError, csv.Error):
        return []
    out.sort(key=lambda x: x[0]); return out


def mid_and_spread_proxy(trades: List[Tuple[int, float, float, bool]], t_ms: int, bucket_ms: int = TRADE_BUCKET_MS) -> Dict[str, Optional[float]]:
    """Dans [t, t+bucket) : mid proxy = mediane des prix ; spread effectif proxy = (prix moyen des trades a
    l'achat agressif - prix moyen des trades a la vente agressive) / mid, en bps. is_buyer_maker=True signifie
    que l'acheteur est passif, donc le trade a eu lieu au BID (vente agressive)."""
    w = [t for t in trades if t_ms <= t[0] < t_ms + bucket_ms]
    if not w:
        return {"mid_price": None, "effective_spread_bps": None, "n_trades": 0, "notional_traded_usd": None}
    prices = [p for _, p, _, _ in w]
    mid = statistics.median(prices)
    at_bid = [p for _, p, _, bm in w if bm]; at_ask = [p for _, p, _, bm in w if not bm]
    spread = None
    if at_bid and at_ask and mid > 0:
        s = (statistics.mean(at_ask) - statistics.mean(at_bid)) / mid * 1e4
        spread = round(max(s, 0.0), 3)
    return {"mid_price": mid, "effective_spread_bps": spread, "n_trades": len(w), "notional_traded_usd": round(sum(p * q for _, p, q, _ in w), 2)}


# ----------------------------------------------------------------------------- features d'un instantane
def snapshot_features(snap: Optional[Dict[str, Any]], mid: Optional[float]) -> Dict[str, Any]:
    f: Dict[str, Any] = {"depth_resolution_bps": BD.NATIVE_BANDS_BPS[0], "book_ok": bool(snap and snap.get("ok")), "book_error": (snap or {}).get("error")}
    if not snap or not snap.get("ok"):
        for side in ("bid", "ask"):
            for b in BANDS_REQUESTED:
                f["%s_depth_%dbps_usd" % (side, b)] = None; f["%s_depth_%dbps_is_lower_bound" % (side, b)] = None
            for n in NOTIONALS_USD:
                f["%s_slippage_%d_usd_bps" % ("sell" if side == "bid" else "buy", n)] = None
                f["%s_slippage_%d_usd_ub_bps" % ("sell" if side == "bid" else "buy", n)] = None
        f["book_imbalance_10bps"] = None; f["book_imbalance_25bps"] = None; f["book_imbalance_20bps_native"] = None
        return f
    f["depth_resolution_bps"] = snap.get("resolution_bps")
    for side in ("bid", "ask"):
        for b in BANDS_REQUESTED:
            v, used, lb = BD.depth_within(snap[side], b)
            f["%s_depth_%dbps_usd" % (side, b)] = None if v is None else round(v, 2)
            f["%s_depth_%dbps_is_lower_bound" % (side, b)] = lb if v is not None else None
        f["%s_depth_20bps_native_usd" % side] = round(snap[side][20], 2) if 20 in snap[side] else None
        f["%s_depth_100bps_native_usd" % side] = round(snap[side][100], 2) if 100 in snap[side] else None
        act = "sell" if side == "bid" else "buy"
        for n in NOTIONALS_USD:
            s = BD.slippage_for_notional(snap[side], float(n))
            f["%s_slippage_%d_usd_bps" % (act, n)] = s["bps"]; f["%s_slippage_%d_usd_ub_bps" % (act, n)] = s["ub_bps"]
    f["book_imbalance_10bps"] = BD.imbalance(snap, 10)            # None : sous la resolution
    f["book_imbalance_25bps"] = BD.imbalance(snap, 25)            # = bande 20 bps (borne)
    f["book_imbalance_20bps_native"] = BD.imbalance(snap, 20)
    f["best_bid"] = None; f["best_ask"] = None; f["spread_bps"] = None     # absents du format Vision : jamais inventes
    f["mid_price"] = mid; f["mid_price_source"] = "aggTrades median (proxy)" if mid is not None else None
    return f


def capacity_status(f: Dict[str, Any]) -> Tuple[str, float, List[str]]:
    """Statut et score (0-100). Le score n'est pas une qualite d'alpha : c'est 'combien le carnet supporte'."""
    reasons: List[str] = []
    if f.get("book_error") and not f.get("book_ok"):
        return "BAD_BOOK", 0.0, [f["book_error"]]
    if not f.get("book_ok"):
        return "NO_DEPTH", 0.0, ["no book snapshot in the window"]
    res = f.get("depth_resolution_bps") or 100
    key = "20bps" if res == 20 else "100bps"
    bd, ad = f.get("bid_depth_%s_native_usd" % key), f.get("ask_depth_%s_native_usd" % key)
    thin = min(bd or 0.0, ad or 0.0)
    spread = f.get("effective_spread_bps")
    depth_pts = 50.0 * max(0.0, min(1.0, (math.log10(max(thin, 1.0)) - 2.0) / 2.0))     # 100 USDT -> 0, 10 000 USDT -> 50
    if res != 20:
        depth_pts *= 0.5                                                              # mesure a 1 % seulement : vaut moitie
    spread_pts = 50.0 * max(0.0, 1.0 - (spread / SPREAD_TOO_WIDE_BPS)) if spread is not None else 0.0
    score = round(depth_pts + spread_pts, 1)
    if thin < DEPTH_TOO_THIN_USD:
        reasons.append("thinner side holds %.0f USDT within %d bps (< %.0f)" % (thin, res, DEPTH_TOO_THIN_USD))
        return "DEPTH_TOO_THIN", score, reasons
    if spread is not None and spread > SPREAD_TOO_WIDE_BPS:
        reasons.append("effective spread proxy %.1f bps (> %.0f)" % (spread, SPREAD_TOO_WIDE_BPS))
        return "SPREAD_TOO_WIDE", score, reasons
    if spread is None:
        return "UNKNOWN", score, ["depth holds %.0f USDT within %d bps but no trades in the bucket to estimate the spread" % (thin, res)]
    if res != 20:
        return "UNKNOWN", score, ["fills within 100 bps and spread proxy %.1f bps, but this archive generation has no 20 bps level: sub-1%% capacity is not observable" % spread]
    return "CAPACITY_OK", score, ["both sides >= %.0f USDT within 20 bps, spread proxy %.1f bps" % (DEPTH_TOO_THIN_USD, spread)]


# ----------------------------------------------------------------------------- un evenement
def event_files(symbol: str, t0_ms: int) -> Dict[str, List[Path]]:
    """Archives locales (P6) couvrant [t0, t0 + 1 h] : le jour de t0 et, si la fenetre le traverse, le suivant."""
    days = sorted({datetime.fromtimestamp((t0_ms + off) / 1000, tz=timezone.utc).strftime("%Y-%m-%d") for off in (0, 61 * 60_000)})
    out = {"bookDepth": [], "aggTrades": []}
    for ds in out:
        for d in days:
            p = VISION / ds / symbol / ("%s-%s-%s.zip" % (symbol, ds, d))
            if p.exists():
                out[ds].append(p)
    return out


def compute_event(symbol: str, event_id: str, t0_iso: str) -> Dict[str, Any]:
    t0_ms = BD.parse_ts(t0_iso)
    files = event_files(symbol, t0_ms)
    snaps: List[Dict[str, Any]] = []; book_err = None
    for p in files["bookDepth"]:
        try:
            snaps += BD.load(p)
        except BD.BookError as e:
            book_err = str(e)
    snaps.sort(key=lambda s: s["ts_ms"])
    trades: List[Tuple[int, float, float, bool]] = []
    for p in files["aggTrades"]:
        trades += load_trades(p)
    windows = []
    for m in WINDOWS_MIN:
        t = t0_ms + m * 60_000
        snap = BD.nearest_at_or_after(snaps, t, SNAPSHOT_TOLERANCE_MS) if snaps else None
        tp = mid_and_spread_proxy(trades, t) if trades else {"mid_price": None, "effective_spread_bps": None, "n_trades": 0, "notional_traded_usd": None}
        f = snapshot_features(snap, tp["mid_price"]); f.update({"effective_spread_bps": tp["effective_spread_bps"], "n_trades_1m": tp["n_trades"], "notional_traded_1m_usd": tp["notional_traded_usd"]})
        if snap is None and not snaps and book_err:
            f["book_error"] = book_err
        f["book_update_count"] = BD.count_between(snaps, t, t + 60_000) if snaps else 0
        f["snapshot_ts"] = datetime.fromtimestamp(snap["ts_ms"] / 1000, tz=timezone.utc).isoformat(timespec="seconds") if snap else None
        f["snapshot_lag_s"] = round((snap["ts_ms"] - t) / 1000, 1) if snap else None
        st, score, why = capacity_status(f); f.update({"window_min": m, "capacity_status": st, "capacity_score": score, "capacity_reasons": why})
        windows.append(f)
    overall = windows[0]["capacity_status"] if windows else "NO_DEPTH"
    return {"event_id": event_id, "symbol": symbol, "t0": t0_iso, "files": {k: [str(p.relative_to(ROOT)) for p in v] for k, v in files.items()},
            "n_book_snapshots_day": len(snaps), "n_trades_day": len(trades), "book_error": book_err,
            "windows": windows, "capacity_status_t0": overall, "capacity_score_t0": windows[0]["capacity_score"] if windows else 0.0,
            "capacity_measured": overall not in ("NO_DEPTH", "BAD_BOOK"),
            "depth_resolution_bps": windows[0].get("depth_resolution_bps") if windows else None,
            "supports_usd": {str(n): (windows[0].get("sell_slippage_%d_usd_bps" % n) is not None and windows[0].get("buy_slippage_%d_usd_bps" % n) is not None) for n in NOTIONALS_USD} if windows else {},
            "no_return_computed": True}
