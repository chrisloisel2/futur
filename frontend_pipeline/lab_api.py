#!/usr/bin/env python3
"""
frontend_pipeline/lab_api.py
─────────────────────────────────────────────────────────────────────────────
API LECTURE SEULE du LIVE ALPHA LAB pour le command center (préfixe /api/lab).

Source de vérité : les fichiers écrits par scripts/run_live_alpha_lab_cycle.py
(timer systemd, toutes les 15 min) sous reports/live_alpha_lab/ :
  portfolios/SUMMARY.json · portfolios/<NAME>/state.json · CYCLE_STATE.json
Rien n'est calculé ni écrit ici : on relit, on met en forme, cache TTL 20 s.
Capital VIRTUEL (200 000 € par portefeuille shadow), aucun ordre réel.

Étiquette (label) dérivée UNIQUEMENT des registres configs/ — jamais d'une
liste d'alphas codée en dur (règle « honnête ») :
  VALIDATED_FORWARD    un candidat de validation_registry.yaml a
                       frozen_alpha_id == alpha_id ET validated_for_forward
  NO_CAPITAL           scientific_status ∈ {REJECTED, INVALIDATED,
                       INVALIDATED_PENDING_RESPEC}
  GATE / OVERLAY       rôle du runner dans live_alpha_runners.yaml
  EXPERIMENTAL_SHADOW  tout le reste
Vivant (cycle.live) ⇔ CYCLE_STATE.status == "OK" ET cycle_finished_at à
moins de 20 min de maintenant (UTC).

Compatibilité : Python 3.8 (venv hôte) ET 3.11 (conteneur) — typing.Dict/List,
pas de `X | None`, pas de match.
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import numpy as np
import yaml
from fastapi import APIRouter, HTTPException

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))   # scripts.* et src.* importables (comme _stack_equity)

# Constantes de chemin (monkeypatchées par les tests — les sous-chemins sont
# recalculés à chaque appel à partir de LAB_DIR, jamais figés à l'import).
LAB_DIR = ROOT / "reports" / "live_alpha_lab"
REGISTRY_PATH = ROOT / "configs" / "live_alpha_registry.yaml"
RUNNERS_PATH = ROOT / "configs" / "live_alpha_runners.yaml"
VALIDATION_PATH = ROOT / "configs" / "validation_registry.yaml"

# Ordre FIXE d'affichage (src/institutional/live_alpha_lab/portfolio_config.py)
PORTFOLIO_NAMES: List[str] = [
    "P1_EQUAL_RISK", "P1_CONTROL", "P1_VOL_OVERLAY", "P2_DIVERSIFIED", "P3_ALL_CANDIDATES",
]
TIMER_EVERY_MIN = 15
LIVE_MAX_AGE_MIN = 20.0
SHADOW_OPERATIONAL = {"SIGNAL_SHADOW", "EXECUTION_SHADOW"}
NO_CAPITAL_SCIENTIFIC = {"REJECTED", "INVALIDATED", "INVALIDATED_PENDING_RESPEC"}
PORTFOLIOS_TTL_S = 20.0
DETAIL_TTL_S = 5.0
FILLS_RECENT_MAX = 20

# Sous-ensemble SÛR de clés d'un fill exposé au frontend (contrat API). Un fill
# réel s'écrit symbol/fill_price/fee_usd : on les renomme vers les clés du
# contrat, sans jamais exposer le reste (intent_id, signal_id, mark_source…).
FILL_KEYS = ("timestamp", "instrument", "alpha_id", "portfolio_id", "side", "direction",
             "quantity", "price", "fee", "notional", "order_id")
FILL_ALIASES: Dict[str, tuple] = {
    "instrument": ("instrument", "symbol"),
    "price": ("price", "fill_price"),
    "fee": ("fee", "fee_usd", "fee_amount"),
    "notional": ("notional", "requested_notional"),
}

try:
    from src.institutional.live_alpha_lab.portfolio_config import CAPITAL_EUR as _CAP
    CAPITAL_EUR = float(_CAP)
except Exception:   # pragma: no cover — le repo est toujours bind-monté entier
    CAPITAL_EUR = 200_000.0

router = APIRouter(prefix="/api/lab")
_cache: Dict[str, tuple] = {}


# ── utilitaires ──────────────────────────────────────────────────────────────

def _now() -> datetime:
    """Horloge UTC (isolée pour être monkeypatchée dans les tests)."""
    return datetime.now(timezone.utc)


def _cached(key: str, ttl: float, fn: Callable[[], Any]) -> Any:
    now = time.time()
    hit = _cache.get(key)
    if hit is not None and now - hit[0] < ttl:
        return hit[1]
    v = fn()            # une exception (404…) n'est pas mise en cache
    _cache[key] = (now, v)
    return v


def _clean(o: Any) -> Any:
    """NaN/inf → None récursivement (JSON strict) ; numpy → python natif."""
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (float, np.floating)) and not np.isfinite(o):
        return None
    if isinstance(o, np.generic):
        return o.item()
    if isinstance(o, datetime):
        return o.isoformat()
    return o


def _read_json(p: Path) -> Optional[dict]:
    try:
        with open(p, "r", encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else None
    except Exception:
        return None


def _load_yaml(p: Path) -> dict:
    try:
        d = yaml.safe_load(Path(p).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _parse_ts(s: Any) -> Optional[datetime]:
    """ISO-8601 → datetime aware UTC (None si illisible)."""
    if s is None:
        return None
    if isinstance(s, datetime):
        dt = s
    else:
        txt = str(s).strip()
        if txt.endswith("Z"):
            txt = txt[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(txt)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _ts_str(s: Any) -> Optional[str]:
    if s is None:
        return None
    if isinstance(s, datetime):
        return s.isoformat()
    return str(s)


def _f(x: Any, default: Optional[float] = 0.0) -> Optional[float]:
    if x is None:
        return default
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _pick(sources: List[dict], *keys: str, default: Any = None) -> Any:
    """Première valeur non-None trouvée dans `sources` (ordre = priorité)."""
    for src in sources:
        if not isinstance(src, dict):
            continue
        for k in keys:
            v = src.get(k)
            if v is not None:
                return v
    return default


# ── chemins (dérivés de LAB_DIR à l'appel) ───────────────────────────────────

def _state_path(name: str) -> Path:
    return LAB_DIR / "portfolios" / name / "state.json"


def _load_state_or_404(name: str) -> dict:
    if name not in PORTFOLIO_NAMES:
        raise HTTPException(404, f"portefeuille inconnu : {name}")
    st = _read_json(_state_path(name))
    if st is None:
        raise HTTPException(404, f"state.json absent pour {name}")
    return st


# ── cycle (CYCLE_STATE.json) ─────────────────────────────────────────────────

def _cycle() -> dict:
    cs = _read_json(LAB_DIR / "CYCLE_STATE.json") or {}
    finished_at = _ts_str(cs.get("cycle_finished_at"))
    status = cs.get("status")
    status = str(status) if status is not None else None
    age_min: Optional[float] = None
    ts = _parse_ts(finished_at)
    if ts is not None:
        age_min = round((_now() - ts).total_seconds() / 60.0, 1)
    live = bool(status == "OK" and age_min is not None and abs(age_min) <= LIVE_MAX_AGE_MIN)
    failed_raw = cs.get("producers_failed") or []
    failed: List[str] = []
    for x in failed_raw if isinstance(failed_raw, list) else []:
        if isinstance(x, dict):
            failed.append(str(x.get("name") or x.get("alpha_id") or json.dumps(x, sort_keys=True)))
        else:
            failed.append(str(x))
    return {
        "finished_at": finished_at,
        "status": status,
        "producers_ok": int(_f(cs.get("producers_ok"), 0) or 0),
        "producers_run": int(_f(cs.get("producers_run"), 0) or 0),
        "producers_failed": failed,
        "age_min": age_min,
        "live": live,
        "timer_every_min": TIMER_EVERY_MIN,
    }


# ── portefeuilles shadow ─────────────────────────────────────────────────────

def _portfolio_row(name: str, summary_p: Optional[dict]) -> Optional[dict]:
    """Ligne résumé d'un portefeuille. None si son state.json manque (omis).
    Priorité des sources : dernier point de l'equity_curve (état exact au
    dernier pas), puis SUMMARY.json, puis champs cumulés du state."""
    st = _read_json(_state_path(name))
    if st is None:
        return None
    curve = st.get("equity_curve") or []
    curve = [pt for pt in curve if isinstance(pt, dict)]
    last = curve[-1] if curve else {}
    sp = summary_p if isinstance(summary_p, dict) else {}
    srcs = [last, sp]
    positions = st.get("positions")
    if isinstance(positions, dict):
        n_positions = len(positions)
    elif isinstance(positions, list):
        n_positions = len(positions)
    else:
        n_positions = int(_f(_pick(srcs, "n_positions"), 0) or 0)

    equity = _f(_pick(srcs, "equity"), None)
    if equity is None:
        equity = _f(st.get("cash"), CAPITAL_EUR)
    pnl_eur = equity - CAPITAL_EUR
    pnl_pct = pnl_eur / CAPITAL_EUR if CAPITAL_EUR else None

    pnl_by_alpha = _pick([last, sp, st], "pnl_by_alpha", "cumulative_pnl_by_alpha", default={})
    cost_by_alpha = _pick([sp, st], "cost_by_alpha", "cumulative_cost_by_alpha", default={})
    status = _pick(srcs, "status")
    return {
        "name": name,
        "equity": equity,
        "pnl_eur": pnl_eur,
        "pnl_pct": pnl_pct,
        "gross_exposure": _f(_pick(srcs, "gross_exposure"), 0.0),
        "net_exposure": _f(_pick(srcs, "net_exposure"), 0.0),
        "n_positions": n_positions,
        "realized_pnl": _f(_pick([last, sp, st], "realized_pnl", "cumulative_realized_pnl"), 0.0),
        "unrealized_pnl": _f(_pick(srcs, "unrealized_pnl"), 0.0),
        "fees": _f(_pick([last, sp, st], "fees", "cumulative_fees_usd"), 0.0),
        "funding": _f(_pick([last, sp, st], "funding", "cumulative_funding_usd"), 0.0),
        "drawdown": _f(_pick(srcs, "drawdown"), 0.0),
        "status": str(status) if status is not None else None,
        "last_step_ts": _ts_str(st.get("last_step_ts") or last.get("ts")),
        "since": _ts_str(curve[0].get("ts")) if curve else None,
        "n_equity_points": len(curve),
        "pnl_by_alpha": {str(k): _f(v, 0.0) for k, v in (pnl_by_alpha or {}).items()},
        "cost_by_alpha": {str(k): _f(v, 0.0) for k, v in (cost_by_alpha or {}).items()},
    }


# ── roster (registres + décisions) ───────────────────────────────────────────

def _scoreboard_row(entry: dict) -> Optional[dict]:
    """Métriques replay/forward via scripts.compute_live_alpha_lab_scoreboard.
    None si l'import (ou le calcul) échoue → fallback parquet."""
    try:
        from scripts.compute_live_alpha_lab_scoreboard import row_for
    except Exception:
        return None
    try:
        r = row_for(entry)
    except Exception:
        return None
    return {
        "replay_decisions": int(_f(r.get("replay_decisions"), 0) or 0),
        "forward_decisions": int(_f(r.get("forward_decisions"), 0) or 0),
        "independent_episodes": r.get("forward_independent_episodes"),
        "confidence": r.get("confidence_level"),
        "last_trigger_h_ago": r.get("time_since_last_trigger_hours"),
    }


def _fallback_metrics(alpha_id: str) -> dict:
    """Compte la seule colonne `provenance` de <alpha_id>/decisions.parquet.
    Sans colonne provenance : tout est compté replay (fail closed, comme le
    scoreboard). Épisodes/confiance/dernier trigger : inconnus → null."""
    p = LAB_DIR / str(alpha_id) / "decisions.parquet"
    replay = forward = 0
    if p.exists():
        try:
            import pandas as pd
            vc = pd.read_parquet(p, columns=["provenance"])["provenance"].value_counts()
            replay = int(vc.get("REPLAY", 0))
            forward = int(vc.get("FORWARD_LIVE", 0))
        except Exception:
            try:
                import pyarrow.parquet as pq
                replay = int(pq.ParquetFile(str(p)).metadata.num_rows)
            except Exception:
                replay = 0
    return {
        "replay_decisions": replay,
        "forward_decisions": forward,
        "independent_episodes": None,
        "confidence": None,
        "last_trigger_h_ago": None,
    }


def _label(alpha_id: str, scientific_status: Optional[str], role: Optional[str],
           validated_forward: set) -> str:
    if alpha_id in validated_forward:
        return "VALIDATED_FORWARD"
    if scientific_status in NO_CAPITAL_SCIENTIFIC:
        return "NO_CAPITAL"
    if role == "gate":
        return "GATE"
    if role == "overlay":
        return "OVERLAY"
    return "EXPERIMENTAL_SHADOW"


def _roster() -> List[dict]:
    alphas = _load_yaml(REGISTRY_PATH).get("alphas") or []
    runners_raw = _load_yaml(RUNNERS_PATH).get("runners") or []
    candidates = _load_yaml(VALIDATION_PATH).get("candidates") or []

    roles: Dict[str, Optional[str]] = {}
    for r in runners_raw:
        if isinstance(r, dict) and r.get("alpha_id"):
            role = r.get("role")
            roles[str(r["alpha_id"])] = str(role) if role is not None else None

    validated_forward = set()
    for c in candidates:
        if isinstance(c, dict) and c.get("frozen_alpha_id") and c.get("validated_for_forward") is True:
            validated_forward.add(str(c["frozen_alpha_id"]))

    out: List[dict] = []
    for e in alphas:
        if not isinstance(e, dict) or not e.get("alpha_id"):
            continue
        op = e.get("operational_status")
        if op not in SHADOW_OPERATIONAL:
            continue
        alpha_id = str(e["alpha_id"])
        sci = e.get("scientific_status")
        sci = str(sci) if sci is not None else None
        role = roles.get(alpha_id)
        metrics = _scoreboard_row(e)
        if metrics is None:
            metrics = _fallback_metrics(alpha_id)
        out.append({
            "alpha_id": alpha_id,
            "family": e.get("family"),
            "risk_bucket": e.get("risk_bucket"),
            "correlation_family": e.get("correlation_family"),
            "scientific_status": sci,
            "operational_status": str(op),
            "role": role,
            "label": _label(alpha_id, sci, role, validated_forward),
            "freeze_timestamp": _ts_str(e.get("freeze_timestamp")),
            "replay_decisions": metrics["replay_decisions"],
            "forward_decisions": metrics["forward_decisions"],
            "independent_episodes": metrics["independent_episodes"],
            "confidence": metrics["confidence"],
            "last_trigger_h_ago": metrics["last_trigger_h_ago"],
        })
    return out


# ── endpoints ────────────────────────────────────────────────────────────────

def _build_portfolios() -> dict:
    summary = _read_json(LAB_DIR / "portfolios" / "SUMMARY.json") or {}
    per = summary.get("portfolios") if isinstance(summary.get("portfolios"), dict) else {}
    rows: List[dict] = []
    for name in PORTFOLIO_NAMES:
        row = _portfolio_row(name, per.get(name))
        if row is not None:
            rows.append(row)
    screened = summary.get("screened_symbols") or []
    return _clean({
        "generated_at": _ts_str(summary.get("generated_at")),
        "capital_eur": CAPITAL_EUR,
        "cycle": _cycle(),
        "vol_overlay_multiplier": _f(summary.get("vol_overlay_multiplier"), None),
        "screened_symbols": [str(s) for s in screened] if isinstance(screened, list) else [],
        "portfolios": rows,
        "roster": _roster(),
    })


@router.get("/portfolios")
def api_lab_portfolios():
    """Vue d'ensemble : cycle, 5 portefeuilles shadow (ordre fixe), roster."""
    return _cached("lab:portfolios", PORTFOLIOS_TTL_S, _build_portfolios)


@router.get("/portfolio/{name}/history")
def api_lab_portfolio_history(name: str):
    def load():
        st = _load_state_or_404(name)
        hist = []
        for pt in st.get("equity_curve") or []:
            if not isinstance(pt, dict):
                continue
            hist.append({
                "t": _ts_str(pt.get("ts")),
                "v": _f(pt.get("equity"), None),
                "gross": _f(pt.get("gross_exposure"), 0.0),
                "n_positions": int(_f(pt.get("n_positions"), 0) or 0),
                "status": str(pt.get("status")) if pt.get("status") is not None else None,
            })
        return _clean({"name": name, "capital_eur": CAPITAL_EUR, "history": hist})
    return _cached(f"lab:history:{name}", DETAIL_TTL_S, load)


def _safe_fill(f: dict) -> dict:
    out: Dict[str, Any] = {}
    for k in FILL_KEYS:
        for src in FILL_ALIASES.get(k, (k,)):
            if src in f and f[src] is not None:
                out[k] = f[src]
                break
    if "side" not in out:
        q = out.get("quantity")
        if isinstance(q, (int, float)) and q != 0:
            out["side"] = "BUY" if q > 0 else "SELL"
    return out


def _fill_sort_key(f: dict) -> float:
    ts = _parse_ts(f.get("timestamp")) if isinstance(f, dict) else None
    return ts.timestamp() if ts is not None else float("-inf")


@router.get("/portfolio/{name}/positions")
def api_lab_portfolio_positions(name: str):
    def load():
        st = _load_state_or_404(name)
        raw = st.get("positions") or {}
        items = list(raw.items()) if isinstance(raw, dict) else \
            [(None, p) for p in raw if isinstance(p, dict)]
        rows: List[dict] = []
        for key, p in items:
            if not isinstance(p, dict):
                continue
            qty = _f(p.get("quantity"), 0.0) or 0.0
            ep = _f(p.get("entry_price"), 0.0) or 0.0
            rows.append({
                "instrument": p.get("instrument") or key,
                "owner_alpha": p.get("owner_alpha"),
                "quantity": qty,
                "entry_price": ep,
                "notional_entry": qty * ep,
                "realized_pnl": _f(p.get("realized_pnl"), 0.0),
                "fees_paid": _f(p.get("fees_paid"), 0.0),
                "funding_paid": _f(p.get("funding_paid"), 0.0),
            })
        rows.sort(key=lambda r: -abs(r["notional_entry"]))
        fills = [f for f in (st.get("fills") or []) if isinstance(f, dict)]
        fills.sort(key=_fill_sort_key)
        recent = [_safe_fill(f) for f in reversed(fills[-FILLS_RECENT_MAX:])]
        return _clean({
            "name": name,
            "as_of": _ts_str(st.get("last_step_ts")),
            "positions": rows,
            "fills_recent": recent,
        })
    return _cached(f"lab:positions:{name}", DETAIL_TTL_S, load)


# ── /cycles : journal des cycles (cycle_log.jsonl) ───────────────────────────

CYCLES_MAX = 30
CYCLES_TTL_S = 20.0
CYCLES_TAIL_BYTES = 4 * 1024 * 1024   # une ligne ≈ 5-10 Ko (stdout_tail inclus)


def _tail_lines(p: Path, max_bytes: int) -> List[str]:
    """Dernières lignes d'un fichier sans le lire entièrement (seek en fin)."""
    try:
        size = p.stat().st_size
        with open(p, "rb") as fh:
            if size > max_bytes:
                fh.seek(size - max_bytes)
                fh.readline()          # ligne coupée → ignorée
            data = fh.read()
    except OSError:
        return []
    return [ln for ln in data.decode("utf-8", "replace").splitlines() if ln.strip()]


def _cycle_row(d: dict) -> dict:
    failed_raw = d.get("producers_failed") or []
    failed: List[str] = []
    for x in failed_raw if isinstance(failed_raw, list) else []:
        if isinstance(x, dict):
            failed.append(str(x.get("name") or x.get("alpha_id") or json.dumps(x, sort_keys=True)))
        else:
            failed.append(str(x))
    status = d.get("status")
    return {
        "started_at": _ts_str(d.get("cycle_started_at") or d.get("started_at")),
        "finished_at": _ts_str(d.get("cycle_finished_at") or d.get("finished_at")),
        "duration_sec": _f(d.get("duration_sec"), None),
        "status": str(status) if status is not None else None,
        "producers_ok": int(_f(d.get("producers_ok"), 0) or 0),
        "producers_run": int(_f(d.get("producers_run"), 0) or 0),
        "producers_failed": failed,
    }


def _build_cycles() -> dict:
    rows: List[dict] = []
    for ln in reversed(_tail_lines(LAB_DIR / "cycle_log.jsonl", CYCLES_TAIL_BYTES)):
        try:
            d = json.loads(ln)
        except ValueError:
            continue              # ligne malformée → ignorée
        if not isinstance(d, dict):
            continue
        rows.append(_cycle_row(d))
        if len(rows) >= CYCLES_MAX:
            break
    return _clean({"cycles": rows})


@router.get("/cycles")
def api_lab_cycles():
    """Les 30 derniers cycles (plus récent d'abord), lignes malformées ignorées."""
    return _cached("lab:cycles", CYCLES_TTL_S, _build_cycles)


# ── /marks : dernier mark par instrument détenu ──────────────────────────────
#
# Même SOURCE que src/institutional/live_alpha_lab/marks.get_mark (perp →
# derivatives_raw open_interest.mark_price ; *_QUARTERLY → clôture quotidienne
# du trimestriel), mais lecture du SEUL fichier le plus récent de la dernière
# partition : get_mark() relit ~2 jours de fichiers par instrument (~0,85 s)
# et fait un appel REST Binance hors frozen-50 — inacceptable pour un endpoint
# rafraîchi toutes les 20 s. Aucun appel réseau ici ; un instrument sans
# fichier local est simplement omis (jamais un prix inventé).

MARKS_TTL_S = 20.0


def _mark_for(instrument: str) -> Optional[dict]:
    """{"price", "ts"} ou None. Lecture seule, aucune exception ne remonte."""
    try:
        import pandas as pd
        from src.institutional.live_alpha_lab import marks as _marks
        if instrument.endswith("_QUARTERLY"):
            q = _marks._from_quarterly_close(instrument, _marks._now())
            if q is None:
                return None
            return {"price": float(q.price), "ts": pd.Timestamp(q.mark_timestamp).isoformat()}
        symbol = instrument.replace("_PERP", "")
        base = _marks._oi_base(symbol)
        date_dirs = sorted(d for d in base.glob("date=*") if d.is_dir())
        for d in reversed(date_dirs[-2:]):
            files = sorted(d.glob("part-*.parquet"))
            if not files:
                continue
            df = pd.read_parquet(files[-1], columns=["timestamp", "mark_price"])
            df = df.dropna(subset=["mark_price"])
            if df.empty:
                continue
            last = df.sort_values("timestamp").iloc[-1]
            ts = pd.to_datetime(int(last["timestamp"]), unit="ms", utc=True)
            return {"price": float(last["mark_price"]), "ts": ts.isoformat()}
        return None
    except Exception:
        return None


def _held_instruments() -> List[str]:
    seen: Dict[str, bool] = {}
    for name in PORTFOLIO_NAMES:
        st = _read_json(_state_path(name)) or {}
        pos = st.get("positions") or {}
        items = list(pos.items()) if isinstance(pos, dict) else \
            [(None, p) for p in pos if isinstance(p, dict)]
        for key, p in items:
            instr = p.get("instrument") if isinstance(p, dict) else None
            instr = instr or key
            if instr:
                seen[str(instr)] = True
    return sorted(seen)


def _build_marks() -> dict:
    marks: Dict[str, dict] = {}
    try:
        for instr in _held_instruments():
            m = _mark_for(instr)
            if m is not None:
                marks[instr] = m
    except Exception:
        marks = {}
    return _clean({"as_of": _now().isoformat(), "marks": marks})


@router.get("/marks")
def api_lab_marks():
    """Marks courants des instruments détenus par les 5 portefeuilles shadow.
    {} si aucune source locale (jamais d'erreur, jamais de prix inventé)."""
    try:
        return _cached("lab:marks", MARKS_TTL_S, _build_marks)
    except Exception:
        return {"as_of": _now().isoformat(), "marks": {}}
