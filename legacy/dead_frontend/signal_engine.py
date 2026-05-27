"""
frontend_pipeline/signal_engine.py  v3
========================================
Moteur de signal multi-symboles avec Level 7 connecté.

Corrections v3 :
  - Level 7 RiskConfig importé et utilisé (stop/TP/sizing)
  - Gates continues (-1..+1) au lieu de binaires
  - Seuil signal réduit : raw_score > 0.15 (au lieu de 3/5 gates binaires)
  - Multiplicateur confiance : 70 (au lieu de 130)
  - Intégration macro plus précise (funding z-score vs seuils fixes)
"""
from __future__ import annotations

import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
import requests
from pymongo import MongoClient, DESCENDING

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from ai.level_7.config import make_long_risk_config, make_short_risk_config
    _RISK_LONG  = make_long_risk_config()
    _RISK_SHORT = make_short_risk_config()
    _LEVEL7_OK  = True
except Exception as _e:
    _LEVEL7_OK  = False
    _RISK_LONG  = None
    _RISK_SHORT = None
    logging.getLogger(__name__).warning(f"Level 7 non disponible: {_e}")

logger = logging.getLogger(__name__)

MONGO_URI = "mongodb://localhost:27017"
DB_NAME   = "trader"

_session = requests.Session()
_session.headers["User-Agent"] = "futur-signal/3.0"

SYMBOL_CONFIGS = {
    "BTCUSDT": {"name": "Bitcoin",  "icon": "₿", "color": "#F7931A"},
    "ETHUSDT": {"name": "Ethereum", "icon": "Ξ", "color": "#627EEA"},
    "SOLUSDT": {"name": "Solana",   "icon": "◎", "color": "#9945FF"},
    "BNBUSDT": {"name": "BNB",      "icon": "B", "color": "#F3BA2F"},
}

_cache:    Dict[str, Dict] = {}
_cache_ts: Dict[str, datetime] = {}
CACHE_TTL = 60


# ─────────────────────────────────────────────────────────────────────────────
# Données Binance
# ─────────────────────────────────────────────────────────────────────────────

def _klines(symbol: str, interval: str, limit: int = 250) -> pd.DataFrame:
    url = "https://api.binance.com/api/v3/klines"
    for attempt in range(3):
        try:
            r = _session.get(url, params={"symbol": symbol, "interval": interval, "limit": limit}, timeout=10)
            if r.ok:
                break
        except Exception:
            time.sleep(1 << attempt)
    else:
        return pd.DataFrame()

    rows = r.json()
    cols = ["ot","o","h","l","c","v","ct","qv","n","tb","tq","_x"]
    df = pd.DataFrame(rows, columns=cols)
    df["ts"] = pd.to_datetime(df["ot"], unit="ms", utc=True)
    for col in ("o","h","l","c","v","tb","tq","qv"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["n"]  = pd.to_numeric(df["n"], errors="coerce").fillna(0).astype(int)
    df["tbr"] = df["tb"] / (df["v"] + 1e-12)
    return df.set_index("ts").sort_index()


def _mongo_derivs(symbol: str) -> Dict[str, Any]:
    try:
        db = MongoClient(MONGO_URI, serverSelectionTimeoutMS=2000)[DB_NAME]
        result: Dict[str, Any] = {}

        fund = db["derivatives_funding"].find_one({"symbol": symbol}, sort=[("timestamp", DESCENDING)])
        oi   = db["derivatives_oi"].find_one({"symbol": symbol},       sort=[("timestamp", DESCENDING)])
        oi_p = db["derivatives_oi"].find_one(
            {"symbol": symbol, "timestamp": {"$lt": oi["timestamp"]} if oi else {}},
            sort=[("timestamp", DESCENDING)]
        )
        ls   = db["derivatives_ls"].find_one({"symbol": symbol},  sort=[("timestamp", DESCENDING)])
        fng  = db["sentiment_fng"].find_one(sort=[("timestamp", DESCENDING)])
        opt  = db["options_btc"].find_one(sort=[("timestamp", DESCENDING)]) if "BTC" in symbol else None

        if fund:
            result["funding_rate"] = float(fund.get("funding_rate", 0) or 0)
            result["funding_pct"]  = result["funding_rate"] * 100
        if oi:
            result["oi"] = float(oi.get("oi", 0) or 0)
            if oi_p and oi_p.get("oi"):
                result["oi_change_pct"] = (result["oi"] - float(oi_p["oi"])) / float(oi_p["oi"]) * 100
        if ls:
            result["ls_ratio"]  = float(ls.get("ls_ratio_global", 1) or 1)
            result["long_pct"]  = float(ls.get("ls_long_pct", 50)   or 50)
            result["short_pct"] = float(ls.get("ls_short_pct", 50)  or 50)
        if fng:
            result["fng_value"] = int(fng.get("fng_value", 50) or 50)
            result["fng_class"] = fng.get("fng_class", "Neutral")
        if opt:
            result["atm_iv"]   = opt.get("atm_iv")
            result["pc_ratio"] = opt.get("put_call_vol_ratio")
            result["skew_25d"] = opt.get("skew_25d_approx")
        return result
    except Exception as e:
        logger.debug(f"MongoDB derivs error: {e}")
        return {}


# ─────────────────────────────────────────────────────────────────────────────
# Feature engineering (continu)
# ─────────────────────────────────────────────────────────────────────────────

def _tanh_score(x: float, scale: float = 1.0) -> float:
    """Mappe x → (-1, +1) de façon smooth. Évite les seuils binaires."""
    return float(np.tanh(x * scale))


def _features(df: pd.DataFrame) -> Dict[str, float]:
    if df.empty:
        return {}
    c, h, l, v, tb = df["c"], df["h"], df["l"], df["v"], df["tbr"]

    # EMAs
    e9   = c.ewm(span=9,   adjust=False).mean()
    e21  = c.ewm(span=21,  adjust=False).mean()
    e50  = c.ewm(span=50,  adjust=False).mean()
    e200 = c.ewm(span=200, adjust=False).mean()

    # RSI
    delta = c.diff()
    gain  = delta.clip(lower=0).ewm(span=14, adjust=False).mean()
    loss  = (-delta.clip(upper=0)).ewm(span=14, adjust=False).mean()
    rsi   = 100 - 100 / (1 + gain / (loss + 1e-9))

    # MACD
    macd_line   = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    macd_signal = macd_line.ewm(span=9, adjust=False).mean()
    macd_hist   = macd_line - macd_signal

    # ATR
    hl  = h - l
    hcp = (h - c.shift()).abs()
    lcp = (l - c.shift()).abs()
    atr = pd.concat([hl, hcp, lcp], axis=1).max(axis=1).ewm(span=14, adjust=False).mean()

    # Bollinger
    sma20 = c.rolling(20, min_periods=1).mean()
    std20 = c.rolling(20, min_periods=1).std().fillna(0)
    boll  = (c - sma20) / (std20 + 1e-9)  # z-score position

    # VWAP
    vwap      = (c * v).rolling(60, min_periods=1).sum() / (v.rolling(60, min_periods=1).sum() + 1e-9)
    dist_vwap = (c - vwap) / (vwap + 1e-9)

    # Volume
    vol_z = (v - v.rolling(20, min_periods=1).mean()) / (v.rolling(20, min_periods=1).std() + 1e-9)

    # Taker
    tb_ma5  = tb.rolling(5,  min_periods=1).mean()
    tb_ma20 = tb.rolling(20, min_periods=1).mean()
    tb_z    = (tb - tb_ma20) / (tb.rolling(20, min_periods=1).std() + 1e-9)

    last = -1
    pr   = float(c.iloc[last])
    return {
        "close":       pr,
        "high":        float(h.iloc[last]),
        "low":         float(l.iloc[last]),
        "volume":      float(v.iloc[last]),
        "ema9":        float(e9.iloc[last]),
        "ema21":       float(e21.iloc[last]),
        "ema50":       float(e50.iloc[last]),
        "ema200":      float(e200.iloc[last]),
        # Continuous scores (-1..+1)
        "ema9_vs_21":  (float(e9.iloc[last]) - float(e21.iloc[last])) / (pr + 1e-9) * 100,
        "ema21_vs_50": (float(e21.iloc[last]) - float(e50.iloc[last])) / (pr + 1e-9) * 100,
        "ema50_vs_200":(float(e50.iloc[last]) - float(e200.iloc[last])) / (pr + 1e-9) * 100,
        "dist_ema200": (pr - float(e200.iloc[last])) / (pr + 1e-9) * 100,
        "rsi":         float(rsi.iloc[last]),
        "rsi_norm":    (float(rsi.iloc[last]) - 50) / 50,   # -1..+1 centered
        "macd_hist":   float(macd_hist.iloc[last]),
        "macd_slope":  float(macd_hist.iloc[last] - macd_hist.iloc[-2]) if len(macd_hist) >= 2 else 0,
        "atr":         float(atr.iloc[last]),
        "atr_pct":     float(atr.iloc[last]) / (pr + 1e-9) * 100,
        "boll_pos":    float(boll.iloc[last]),
        "vwap":        float(vwap.iloc[last]),
        "dist_vwap":   float(dist_vwap.iloc[last]) * 100,
        "vol_z":       float(vol_z.iloc[last]),
        "taker_buy":   float(tb.iloc[last]),
        "taker_z":     float(tb_z.iloc[last]),
        "taker_delta": float(tb_ma5.iloc[last] - tb_ma20.iloc[last]),
        "ret1":        float(c.pct_change(1).iloc[last] * 100),
        "ret5":        float(c.pct_change(5).iloc[last] * 100) if len(c) >= 5   else 0,
        "ret24":       float(c.pct_change(24).iloc[last] * 100) if len(c) >= 24 else 0,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Signal Cascade v3 — scores continus
# ─────────────────────────────────────────────────────────────────────────────

def _cascade(
    f1h: Dict, f15m: Dict, f5m: Dict, f1m: Dict, deriv: Dict
) -> Dict[str, Any]:
    """
    5 dimensions → score pondéré continu → action + confiance.
    Chaque dimension retourne un score ∈ (-1, +1).
    """
    dims: List[Dict] = []
    detail: Dict = {}

    # ══════════════════════════════════════════════════════════════════
    # D1 — TENDANCE 1H  (poids 30%)
    # Score = alignement EMA : ema9>ema21>ema50>ema200
    # ══════════════════════════════════════════════════════════════════
    if f1h:
        e9_21  = f1h.get("ema9_vs_21", 0)     # % spread
        e21_50 = f1h.get("ema21_vs_50", 0)
        e50_200 = f1h.get("ema50_vs_200", 0)
        dist200 = f1h.get("dist_ema200", 0)    # dist from ema200

        # Score = moyenne tanh des alignements
        s = (_tanh_score(e9_21, 30)
           + _tanh_score(e21_50, 20)
           + _tanh_score(e50_200, 10)
           + _tanh_score(dist200, 8)) / 4

        dims.append({"name": "trend_1h", "score": s, "weight": 0.30})
        detail["trend_1h"] = round(s, 3)
        detail["ema9_vs_21"]   = round(e9_21, 4)
        detail["ema21_vs_50"]  = round(e21_50, 4)

    # ══════════════════════════════════════════════════════════════════
    # D2 — MOMENTUM 15M  (poids 25%)
    # Score = RSI centré + MACD direction
    # ══════════════════════════════════════════════════════════════════
    if f15m:
        rsi_n   = f15m.get("rsi_norm", 0)       # -1..+1 (0=neutral, +1=RSI100)
        macd_s  = _tanh_score(f15m.get("macd_hist", 0), 5_000)
        macd_sl = _tanh_score(f15m.get("macd_slope", 0), 10_000)

        # RSI extreme = contrarian (invert)
        rsi     = f15m.get("rsi", 50)
        rsi_adj = rsi_n if 25 < rsi < 75 else -rsi_n * 0.5

        s = 0.4 * rsi_adj + 0.4 * macd_s + 0.2 * macd_sl
        dims.append({"name": "momentum_15m", "score": s, "weight": 0.25})
        detail["momentum_15m"] = round(s, 3)
        detail["rsi_15m"]      = round(rsi, 1)

    # ══════════════════════════════════════════════════════════════════
    # D3 — PRESSION VOLUME 5M  (poids 20%)
    # Score = taker delta + dist VWAP + vol spike
    # ══════════════════════════════════════════════════════════════════
    if f5m:
        tb_z     = f5m.get("taker_z", 0)
        tb_delta = f5m.get("taker_delta", 0)
        dist_vwap = f5m.get("dist_vwap", 0)     # % from VWAP
        vol_z    = f5m.get("vol_z", 0)

        s = (_tanh_score(tb_delta, 20)
           + _tanh_score(tb_z, 0.8)
           + _tanh_score(dist_vwap, 15)) / 3

        # Vol spike amplifies (but doesn't create) the signal
        amp = 1.0 + 0.3 * min(abs(vol_z) / 3, 1.0)
        s   = float(np.clip(s * amp, -1, 1))

        dims.append({"name": "pressure_5m", "score": s, "weight": 0.20})
        detail["pressure_5m"]    = round(s, 3)
        detail["taker_buy_5m"]   = round(f5m.get("taker_buy", 0.5), 3)
        detail["dist_vwap_5m"]   = round(dist_vwap, 3)

    # ══════════════════════════════════════════════════════════════════
    # D4 — CONTEXTE MACRO  (poids 20%)
    # Funding rate + L/S ratio + Fear & Greed (signaux contrarian)
    # ══════════════════════════════════════════════════════════════════
    macro_scores = []

    if "funding_rate" in deriv:
        fr = deriv["funding_rate"]
        # Funding positif = longs surchargés → fade → bearish pour le long
        # Funding négatif = shorts surchargés → squeeze → bullish pour le long
        macro_scores.append(-_tanh_score(fr * 10_000, 1.5))

    if "ls_ratio" in deriv:
        ls = deriv["ls_ratio"]
        # L/S < 1 = majority short = squeeze potentiel → bullish
        macro_scores.append(-_tanh_score((ls - 1.0) * 3, 1.0))

    if "fng_value" in deriv:
        fng = deriv["fng_value"]
        # Extreme fear (< 20) → contrarian long
        # Extreme greed (> 80) → contrarian short
        # Middle zone → neutral
        fng_n = (fng - 50) / 50  # -1..+1
        if abs(fng_n) > 0.4:     # Only extreme values count
            macro_scores.append(-fng_n * 0.7)  # contrarian → invert
        else:
            macro_scores.append(fng_n * 0.2)   # mild follow trend

    if macro_scores:
        s = float(np.mean(macro_scores))
        dims.append({"name": "macro", "score": s, "weight": 0.20})
        detail["macro"] = round(s, 3)
        detail["funding_pct"] = round(deriv.get("funding_pct", 0), 5)
        detail["ls_ratio"]    = round(deriv.get("ls_ratio", 1), 3)
        detail["fng"]         = deriv.get("fng_value", 50)

    # ══════════════════════════════════════════════════════════════════
    # D5 — MICROSTRUCTURE 1M  (poids 5%)
    # Position vs VWAP + taker instantané
    # ══════════════════════════════════════════════════════════════════
    if f1m:
        dv = f1m.get("dist_vwap", 0)
        tb = f1m.get("taker_delta", 0)
        s  = (_tanh_score(dv, 20) + _tanh_score(tb, 30)) / 2
        dims.append({"name": "microstructure_1m", "score": s, "weight": 0.05})
        detail["microstructure_1m"] = round(s, 3)

    # ══════════════════════════════════════════════════════════════════
    # SYNTHÈSE
    # ══════════════════════════════════════════════════════════════════
    if not dims:
        return {"action": "WAIT", "confidence": 0, "details": detail}

    total_w  = sum(d["weight"] for d in dims)
    raw      = sum(d["score"] * d["weight"] for d in dims) / (total_w or 1)

    # Action : seuil plus bas (0.15 au lieu de 0.20, pas besoin de 3/5 gates)
    if   raw >  0.15: action = "LONG"
    elif raw < -0.15: action = "SHORT"
    else:             action = "WAIT"

    # Confiance : multiplicateur 70 au lieu de 130
    conf = min(100.0, abs(raw) * 70)

    detail["dims"]     = dims
    detail["raw_score"] = round(raw, 4)

    return {"action": action, "confidence": round(conf, 1), "raw_score": round(raw, 4), "details": detail}


# ─────────────────────────────────────────────────────────────────────────────
# Risk — Level 7 connecté
# ─────────────────────────────────────────────────────────────────────────────

def _risk_level7(price: float, action: str, atr: float, conf: float) -> Dict[str, Any]:
    """
    Utilise la config Level 7 (make_long/short_risk_config).
    Fallback sur ATR heuristique si Level 7 non disponible.
    """
    if action == "WAIT":
        return {}

    cfg = _RISK_LONG if action == "LONG" else _RISK_SHORT

    # Stop et TP depuis Level 7
    if _LEVEL7_OK and cfg is not None:
        sl_pct = cfg.stop_loss_pct
        tp_pct = cfg.take_profit_pct
        rr     = cfg.risk_reward_ratio
        # ATR override si atr_pct < sl_pct (stop minimal)
        atr_pct = atr / (price + 1e-9)
        if cfg.use_atr_stop and atr_pct > sl_pct:
            sl_pct = min(atr_pct * 2.0, cfg.stop_loss_pct * 2)
            tp_pct = sl_pct * rr

        # Kelly sizing depuis Level 7
        win_est   = 0.50 + (conf / 100) * 0.10   # 50-60% win rate estimé
        kelly_raw = win_est - (1 - win_est) / rr
        kelly_f   = kelly_raw * cfg.kelly_fraction
        size_pct  = min(cfg.max_position_pct * 100, max(0.5, kelly_f * 100))

        cooldown    = cfg.cooldown_bars
        max_dd_day  = cfg.max_daily_drawdown_pct * 100
        max_consec  = cfg.max_consecutive_losses

    else:
        # Fallback heuristique
        atr_pct  = atr / (price + 1e-9)
        sl_pct   = max(0.015, atr_pct * 2.0)
        tp_pct   = sl_pct * 1.67
        rr       = 1.67
        size_pct = 2.0
        cooldown, max_dd_day, max_consec = 3, 3.0, 4

    if action == "LONG":
        stop = price * (1 - sl_pct)
        tp1  = price * (1 + tp_pct)
        tp2  = price * (1 + tp_pct * 2)
    else:
        stop = price * (1 + sl_pct)
        tp1  = price * (1 - tp_pct)
        tp2  = price * (1 - tp_pct * 2)

    return {
        "entry":            round(price, 4),
        "stop":             round(stop, 4),
        "tp1":              round(tp1, 4),
        "tp2":              round(tp2, 4),
        "sl_pct":           round(sl_pct * 100, 3),
        "tp_pct":           round(tp_pct * 100, 3),
        "rr_ratio":         round(rr, 2),
        "size_pct_capital": round(size_pct, 2),
        "cooldown_bars":    cooldown,
        "max_dd_daily_pct": max_dd_day,
        "max_consec_losses": max_consec,
        "level7_active":    _LEVEL7_OK,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Indicateurs visuels
# ─────────────────────────────────────────────────────────────────────────────

def _indicators(f1h: Dict, f15m: Dict, f5m: Dict, deriv: Dict) -> List[Dict]:
    inds: List[Dict] = []

    def add(name: str, value: str, label: str, color: str, icon: str, note: str = ""):
        inds.append({"name": name, "value": value, "label": label,
                     "color": color, "icon": icon, "interpretation": note})

    if f15m:
        rsi = f15m.get("rsi", 50)
        c   = "red" if rsi > 70 else ("green" if rsi < 30 else "blue")
        note = "Overbought ⚠" if rsi > 70 else ("Oversold ✓" if rsi < 30 else "Neutre")
        add("RSI 14", f"{rsi:.0f}", "RSI", c, "◉", note)

        mh = f15m.get("macd_hist", 0)
        add("MACD Hist", f"{mh:.4f}", "MACD", "green" if mh > 0 else "red",
            "▲" if mh > 0 else "▼", "Haussier" if mh > 0 else "Baissier")

    if f5m:
        tb = f5m.get("taker_buy", 0.5)
        c  = "green" if tb > 0.55 else ("red" if tb < 0.45 else "amber")
        add("Taker Buy %", f"{tb*100:.1f}%", "Taker", c,
            "⬆" if tb > 0.5 else "⬇",
            "Buy pressure" if tb > 0.55 else ("Sell pressure" if tb < 0.45 else "Neutre"))

        dv = f5m.get("dist_vwap", 0)
        add("Dist VWAP", f"{dv:+.2f}%", "VWAP", "green" if dv > 0 else "red",
            "◈", "Au-dessus VWAP" if dv > 0 else "En-dessous VWAP")

    if "funding_pct" in deriv:
        fp = deriv["funding_pct"]
        c  = "red" if fp > 0.05 else ("green" if fp < -0.02 else "blue")
        add("Funding Rate", f"{fp:+.5f}%", "Funding", c, "💹",
            "Longs surchargés" if fp > 0.05 else ("Shorts surchargés" if fp < -0.02 else "Neutre"))

    if "ls_ratio" in deriv:
        ls = deriv["ls_ratio"]
        c  = "green" if ls < 0.75 else ("red" if ls > 1.25 else "blue")
        add("L/S Ratio", f"{ls:.3f}", "L/S", c, "⚖",
            f"Longs {deriv.get('long_pct',50):.0f}% · Shorts {deriv.get('short_pct',50):.0f}%")

    if "fng_value" in deriv:
        fng = deriv["fng_value"]
        c   = "green" if fng <= 25 else ("red" if fng >= 75 else "amber")
        add("Fear & Greed", f"{fng}", "F&G", c, "😱", deriv.get("fng_class","Neutral"))

    if f1h:
        e9, e21, e50 = f1h.get("ema9",0), f1h.get("ema21",0), f1h.get("ema50",0)
        cl = f1h.get("close", 1)
        bull = cl > e9 > e21 > e50
        bear = cl < e9 < e21 < e50
        c    = "green" if bull else ("red" if bear else "amber")
        add("Tendance 1h", "▲ UP" if bull else ("▼ DOWN" if bear else "→ FLAT"),
            "Trend 1h", c, "▲" if bull else "▼",
            "Uptrend confirmé" if bull else ("Downtrend" if bear else "Consolidation"))

    if "oi_change_pct" in deriv:
        oic = deriv["oi_change_pct"]
        add("OI Change 1h", f"{oic:+.2f}%", "OI",
            "green" if oic > 1 else ("red" if oic < -1 else "blue"),
            "📈" if oic > 0 else "📉",
            "Expansion" if oic > 1 else ("Liquidation" if oic < -1 else "Stable"))

    if "atm_iv" in deriv and deriv["atm_iv"]:
        iv = deriv["atm_iv"]
        c  = "red" if iv > 80 else ("green" if iv < 30 else "blue")
        add("IV ATM (Deribit)", f"{iv:.0f}%", "IV", c, "🎯",
            "High vol" if iv > 80 else ("Low vol" if iv < 30 else "Normal"))

    return inds


# ─────────────────────────────────────────────────────────────────────────────
# API principale
# ─────────────────────────────────────────────────────────────────────────────

def get_signal(symbol: str = "BTCUSDT") -> Dict[str, Any]:
    symbol = symbol.upper()
    if not symbol.endswith("USDT"):
        symbol += "USDT"

    now = datetime.now(timezone.utc)
    if symbol in _cache_ts and (now - _cache_ts[symbol]).total_seconds() < CACHE_TTL:
        return _cache[symbol]

    cfg    = SYMBOL_CONFIGS.get(symbol, {})
    result: Dict[str, Any] = {
        "symbol":    symbol,
        "name":      cfg.get("name", symbol),
        "icon":      cfg.get("icon", ""),
        "color":     cfg.get("color", "#fff"),
        "timestamp": now.isoformat(),
    }

    try:
        # Données multi-TF
        df1h  = _klines(symbol, "1h",  250)
        df15m = _klines(symbol, "15m", 100)
        df5m  = _klines(symbol, "5m",   60)
        df1m  = _klines(symbol, "1m",   30)

        f1h  = _features(df1h)
        f15m = _features(df15m)
        f5m  = _features(df5m)
        f1m  = _features(df1m)
        deriv = _mongo_derivs(symbol)

        # Prix
        price = float(f1m.get("close", f5m.get("close", f1h.get("close", 0))))
        atr   = float(f1h.get("atr", price * 0.005))

        result["current_price"] = price
        result["atr"]           = round(atr, 4)
        result["atr_pct"]       = round(atr / price * 100, 3) if price else 0

        if len(df1h) >= 24:
            p24 = float(df1h["c"].iloc[-24])
            result["change_24h_pct"] = round((price - p24) / p24 * 100, 2)
        if len(df1h) >= 168:
            p7d = float(df1h["c"].iloc[-168])
            result["change_7d_pct"] = round((price - p7d) / p7d * 100, 2)

        # Cascade signal
        sig = _cascade(f1h, f15m, f5m, f1m, deriv)
        result.update(sig)

        # Level 7 Risk
        result["risk"]       = _risk_level7(price, sig["action"], atr, sig["confidence"])
        result["level7_active"] = _LEVEL7_OK

        # Indicateurs
        result["indicators"] = _indicators(f1h, f15m, f5m, deriv)

        # Chart 5m (120 dernières bougies)
        if not df5m.empty:
            result["chart_5m"] = [
                {"time": int(idx.timestamp()), "open": round(float(r["o"]),4),
                 "high": round(float(r["h"]),4), "low": round(float(r["l"]),4),
                 "close": round(float(r["c"]),4), "volume": round(float(r["v"]),2),
                 "taker": round(float(r.get("tbr",0.5)),3)}
                for idx, r in df5m.tail(120).iterrows()
            ]
        result["derivatives"] = deriv

    except Exception as e:
        logger.error(f"Signal error [{symbol}]: {e}", exc_info=True)
        result.update({"action": "WAIT", "confidence": 0, "error": str(e)})

    _cache[symbol]    = result
    _cache_ts[symbol] = now
    return result


def get_all_signals() -> Dict[str, Any]:
    from concurrent.futures import ThreadPoolExecutor
    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    with ThreadPoolExecutor(max_workers=3) as ex:
        results = list(ex.map(get_signal, symbols))
    return {
        "signals":   {s["symbol"]: s for s in results},
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "level7_active": _LEVEL7_OK,
    }
