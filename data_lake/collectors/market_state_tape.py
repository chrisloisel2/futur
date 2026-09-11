#!/usr/bin/env python3
"""
market_state_tape.py -- le collecteur central de l'etat du marche (P4).

    python -m data_lake.collectors.market_state_tape --mode lightweight_watch
    python -m data_lake.collectors.market_state_tape --mode triggered_microstructure_capture --symbol SOMEUSDT --trigger-type new_perp_listing
    python -m data_lake.collectors.market_state_tape --mode readiness

MODE 1 lightweight_watch (24/7, leger) : exchangeInfo USDS-M (defaut 5 s) et spot (60 s) -> diffs ->
  evenements de cycle de vie ; premiumIndex (mark / index / funding, tous symboles, 1 appel) ;
  openInterest (perps, par passes) ; ticker 24 h ; flux !forceOrder@arr compte les rafales.
  Chaque enregistrement : ts local, ts exchange si dispo, source, raw_hash. Append-only.
MODE 2 triggered_microstructure_capture (lourd, borne) : voir microstructure_window.py.
Declencheurs : nouveau perp, changement de statut, onboardDate, delisting / suspension, pic d'OI,
funding extreme, rafale de liquidations, manuel. Dedup + refroidissement + concurrence bornee ;
chaque declenchement est journalise (triggers.jsonl) qu'il soit capture ou non.
Aucun signal. Aucun verdict. Aucun ordre. Aucune jointure alpha.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional
from urllib.request import Request, urlopen

from data_lake.collectors.market_state_schema import TAPE_ROOT, AppendOnlyJsonl, make_snapshot, now_local, now_ns, raw_hash, validate, TRIGGER_TYPES
from data_lake.collectors.exchange_info_diff import ExchangeInfoWatcher
from data_lake.collectors.symbol_lifecycle import SymbolLifecycle
from data_lake.collectors import microstructure_window as MW

ROOT = Path(__file__).resolve().parents[2]
FAPI = "https://fapi.binance.com/fapi/v1"
BINANCE_WS = "wss://fstream.binance.com/market/ws"
WATCH_STATE = TAPE_ROOT / "watch_state.json"
TRIGGERS_LOG = TAPE_ROOT / "triggers" / "triggers.jsonl"
DEFAULTS = {"exchange_info_interval": 5.0, "spot_interval": 60.0, "state_interval": 60.0, "oi_interval": 300.0, "ticker_interval": 300.0,
            "oi_spike_pct": 10.0, "funding_extreme_abs": 0.003, "liq_burst_per_symbol_10s": 5, "liq_burst_global_10s": 100,
            "cooldown_s": {"new_perp_listing": 6 * 3600, "status_change": 6 * 3600, "onboard_date_change": 6 * 3600, "delisting_detected": 6 * 3600,
                           "oi_spike": 6 * 3600, "funding_extreme": 8 * 3600, "liquidation_burst": 6 * 3600, "manual": 0},
            "max_concurrent_captures": 4, "post_window_s": 6 * 3600, "pre_window_s": 1800, "rest_budget_per_min": 600,
            "daily_caps": {"liquidation_burst": 6, "oi_spike": 6, "funding_extreme": 6, "status_change": 12, "new_perp_listing": None, "delisting_detected": None, "onboard_date_change": None, "manual": None},
            "always_on_symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT"], "heartbeat_s": 300}


def _get(url: str, timeout: int = 30):
    with urlopen(Request(url, headers={"User-Agent": "futur-market-state"}), timeout=timeout) as r:
        return json.loads(r.read())


class TriggerDispatcher:
    """Decide si un declencheur donne lieu a une capture : dedup par (venue, symbole, type), refroidissement,
    concurrence bornee, symboles perp seulement pour les captures WS. Tout est journalise."""

    def __init__(self, cfg: dict, state: dict, log_path: Path = TRIGGERS_LOG, spawn: bool = True):
        self.cfg = cfg; self.state = state; self.log_path = log_path; self.spawn = spawn
        self.state.setdefault("last_fired", {}); self.state.setdefault("active", {})

    def _reap(self):
        for tid, info in list(self.state["active"].items()):
            pid = info.get("pid")
            if pid and not _alive(pid) or (info.get("ends_at", 0) and time.time() > info["ends_at"] + 300):
                self.state["active"].pop(tid, None)

    def should_fire(self, trig: dict, now: Optional[float] = None) -> (bool, str):
        now = now or time.time(); self._reap()
        key = f"{trig['venue']}|{trig['symbol']}|{trig['trigger_type']}"
        if trig["trigger_type"] not in TRIGGER_TYPES:
            return False, "unknown_trigger_type"
        if trig.get("market_type") == "spot":
            return False, "spot_symbol_no_ws_capture (lifecycle recorded only)"
        cd = self.cfg["cooldown_s"].get(trig["trigger_type"], 0); last = self.state["last_fired"].get(key)
        if last and now - last < cd:
            return False, f"cooldown ({int(now - last)} s < {cd} s)"
        if len(self.state["active"]) >= self.cfg["max_concurrent_captures"]:
            return False, f"max_concurrent_captures ({self.cfg['max_concurrent_captures']}) reached"
        if any(a["symbol"] == trig["symbol"] for a in self.state["active"].values()):
            return False, "capture already active for this symbol"
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d"); n_today = self.state.get("daily_counts", {}).get(day, {}).get(trig["trigger_type"], 0)
        cap = self.cfg["daily_caps"].get(trig["trigger_type"])
        if cap is not None and n_today >= cap:
            return False, f"daily cap for {trig['trigger_type']} reached ({n_today}/{cap})"
        if trig["trigger_type"] in ("liquidation_burst", "oi_spike", "funding_extreme") and trig["symbol"] in self.cfg["always_on_symbols"]:
            return False, "symbol already covered 24/7 by microstructure_reduced (BBO + trades)"
        return True, "fire"

    def dispatch(self, trig: dict) -> dict:
        ok, why = self.should_fire(trig); rec = {**trig, "decided_at_local": now_local(), "fired": ok, "decision": why}
        if ok and not self.spawn:
            rec["pid"] = None; rec["decision"] = "would fire (no-capture mode: logged only, no cooldown set)"
        elif ok:
            tid = f"{trig['trigger_type']}_{trig['symbol']}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"
            rec["trigger_id"] = tid; key = f"{trig['venue']}|{trig['symbol']}|{trig['trigger_type']}"; self.state["last_fired"][key] = time.time()
            self.state.setdefault("daily_counts", {}); day = datetime.now(timezone.utc).strftime("%Y-%m-%d"); dc = self.state["daily_counts"].setdefault(day, {}); dc[trig["trigger_type"]] = dc.get(trig["trigger_type"], 0) + 1
            post = self.cfg["post_window_s"]; ends = time.time() + post
            if trig.get("t0"):
                try:
                    ends = datetime.fromisoformat(str(trig["t0"]).replace("Z", "+00:00")).timestamp() + post
                except ValueError:
                    pass
            if True:
                cmd = [sys.executable, "-m", "data_lake.collectors.market_state_tape", "--mode", "triggered_microstructure_capture", "--symbol", trig["symbol"],
                       "--trigger-type", trig["trigger_type"], "--reason", trig.get("reason", ""), "--trigger-id", tid, "--post-window", str(post), "--pre-window", str(self.cfg["pre_window_s"])]
                if trig.get("t0"):
                    cmd += ["--t0", str(trig["t0"])]
                logd = TAPE_ROOT / "windows" / tid; logd.mkdir(parents=True, exist_ok=True)
                p = subprocess.Popen(cmd, cwd=str(ROOT), stdout=open(logd / "capture.log", "ab"), stderr=subprocess.STDOUT, start_new_session=True)
                rec["pid"] = p.pid; self.state["active"][tid] = {"symbol": trig["symbol"], "pid": p.pid, "ends_at": ends, "started_at": now_local()}
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        return rec


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0); return True
    except OSError:
        return False


class LightweightWatch:
    def __init__(self, cfg: dict, spawn: bool = True, seconds: Optional[float] = None):
        self.cfg = cfg; self.seconds = seconds
        self.state = json.loads(WATCH_STATE.read_text()) if WATCH_STATE.exists() else {}
        self.um = ExchangeInfoWatcher("binance_um"); self.spot = ExchangeInfoWatcher("binance_spot"); self.life = SymbolLifecycle()
        self.sink = AppendOnlyJsonl(TAPE_ROOT, gz=False); self.gz = AppendOnlyJsonl(TAPE_ROOT, gz=True)
        self.disp = TriggerDispatcher(cfg, self.state, spawn=spawn); self.stop = asyncio.Event(); self.stats = defaultdict(int)
        self.last_oi: Dict[str, float] = self.state.get("last_oi", {}); self.liq: Deque = deque(); self.rest_spent: Deque = deque()

    async def pace(self, weight: int):
        while True:
            now = time.time()
            while self.rest_spent and now - self.rest_spent[0][0] > 60:
                self.rest_spent.popleft()
            if sum(w for _, w in self.rest_spent) + weight <= self.cfg["rest_budget_per_min"]:
                self.rest_spent.append((now, weight)); return
            await asyncio.sleep(0.5)

    def save_state(self):
        self.state["last_oi"] = self.last_oi; self.state["saved_at"] = now_local(); WATCH_STATE.parent.mkdir(parents=True, exist_ok=True)
        tmp = WATCH_STATE.with_suffix(".tmp"); tmp.write_text(json.dumps(self.state, default=str)); tmp.replace(WATCH_STATE)

    def handle_changes(self, venue: str, res: dict):
        if not res.get("changed"):
            return
        events, triggers = self.life.apply(venue, res["changes"], res.get("server_time"))
        for ev in events:
            self.sink.write("symbol_lifecycle_event", ev, venue=venue); self.stats["lifecycle_events"] += 1
        for t in triggers:
            t["market_type"] = "spot" if venue == "binance_spot" else "perp"; self.disp.dispatch(t); self.stats["triggers"] += 1
        self.life.save(); self.sink.flush()

    async def exchange_info_loop(self):
        while not self.stop.is_set():
            try:
                await self.pace(1); res = await asyncio.get_event_loop().run_in_executor(None, self.um.poll); self.stats["um_polls"] += 1; self.handle_changes("binance_um", res)
            except Exception as e:
                self.stats["um_errors"] += 1
            await asyncio.sleep(self.cfg["exchange_info_interval"])

    async def spot_loop(self):
        while not self.stop.is_set():
            try:
                await self.pace(20); res = await asyncio.get_event_loop().run_in_executor(None, self.spot.poll); self.stats["spot_polls"] += 1; self.handle_changes("binance_spot", res)
            except Exception:
                self.stats["spot_errors"] += 1
            await asyncio.sleep(self.cfg["spot_interval"])

    async def state_loop(self):
        """premiumIndex : mark / index / funding de tous les symboles en un appel -> snapshots legers + funding extreme."""
        while not self.stop.is_set():
            try:
                await self.pace(1); rn = now_ns(); d = await asyncio.get_event_loop().run_in_executor(None, _get, f"{FAPI}/premiumIndex"); self.stats["premium_polls"] += 1
                for x in d:
                    fr = float(x["lastFundingRate"]) if x.get("lastFundingRate") not in (None, "") else None
                    rec = make_snapshot("binance", x["symbol"], "watch_premium_index", ts_exchange=datetime.fromtimestamp(x["time"] / 1000, tz=timezone.utc).isoformat(timespec="milliseconds"),
                                        mark_price=float(x["markPrice"]), index_price=float(x["indexPrice"]) if x.get("indexPrice") not in (None, "") else None, funding_rate=fr,
                                        open_interest=self.last_oi.get(x["symbol"]), latency_ms=(rn / 1e6 - x["time"]) if x.get("time") else None, raw=x,
                                        extra={"next_funding_time": x.get("nextFundingTime"), "interest_rate": x.get("interestRate")})
                    self.gz.write("market_state_snapshot", rec, venue="binance", name="watch_premium_index"); self.stats["light_snapshots"] += 1
                    if fr is not None and abs(fr) >= self.cfg["funding_extreme_abs"]:
                        self.disp.dispatch({"trigger_type": "funding_extreme", "venue": "binance", "symbol": x["symbol"], "t0": None, "reason": f"lastFundingRate {fr:+.5f}", "market_type": "perp"})
                self.gz.flush()
            except Exception:
                self.stats["premium_errors"] += 1
            await asyncio.sleep(self.cfg["state_interval"])

    async def oi_loop(self):
        while not self.stop.is_set():
            try:
                syms = [s for s, v in (self.um.prev or {}).items() if v.get("status") == "TRADING" and v.get("market_type") == "perp"]
                for s in syms:
                    if self.stop.is_set():
                        break
                    await self.pace(1)
                    try:
                        d = await asyncio.get_event_loop().run_in_executor(None, _get, f"{FAPI}/openInterest?symbol={s}"); oi = float(d["openInterest"])
                    except Exception:
                        continue
                    prev = self.last_oi.get(s); self.last_oi[s] = oi; self.stats["oi_polls"] += 1
                    self.gz.write("market_state_snapshot", make_snapshot("binance", s, "watch_open_interest", ts_exchange=datetime.fromtimestamp(d["time"] / 1000, tz=timezone.utc).isoformat(timespec="milliseconds"),
                                                                        open_interest=oi, latency_ms=(now_ns() / 1e6 - d["time"]), raw=d), venue="binance", name="watch_open_interest")
                    if prev and prev > 0 and (oi - prev) / prev * 100 >= self.cfg["oi_spike_pct"]:
                        self.disp.dispatch({"trigger_type": "oi_spike", "venue": "binance", "symbol": s, "t0": None, "reason": f"OI {prev:.0f} -> {oi:.0f} (+{(oi - prev) / prev * 100:.1f} % since last pass)", "market_type": "perp"})
                self.gz.flush(); self.save_state()
            except Exception:
                self.stats["oi_errors"] += 1
            await asyncio.sleep(self.cfg["oi_interval"])

    async def ticker_loop(self):
        while not self.stop.is_set():
            try:
                await self.pace(40); d = await asyncio.get_event_loop().run_in_executor(None, _get, f"{FAPI}/ticker/24hr"); self.stats["ticker_polls"] += 1
                for x in d:
                    self.gz.write("ticker_24h", {"venue": "binance", "symbol": x["symbol"], "ts_local": now_local(), "ts_exchange": datetime.fromtimestamp(x["closeTime"] / 1000, tz=timezone.utc).isoformat(timespec="milliseconds") if x.get("closeTime") else None,
                                                 "source": "watch_ticker_24h", "quote_volume": float(x.get("quoteVolume", 0) or 0), "count": x.get("count"), "last_price": float(x.get("lastPrice", 0) or 0), "raw_hash": raw_hash(x)}, venue="binance", name="watch_ticker_24h")
                self.gz.flush()
            except Exception:
                self.stats["ticker_errors"] += 1
            await asyncio.sleep(self.cfg["ticker_interval"])

    async def liquidation_loop(self):
        import websockets
        while not self.stop.is_set():
            try:
                async with websockets.connect(BINANCE_WS, ping_interval=20, max_size=2**23) as ws:
                    await ws.send(json.dumps({"method": "SUBSCRIBE", "params": ["!forceOrder@arr"], "id": 1}))
                    while not self.stop.is_set():
                        try:
                            msg = await asyncio.wait_for(ws.recv(), timeout=60)
                        except asyncio.TimeoutError:
                            continue
                        d = json.loads(msg); d = d.get("data", d)
                        if d.get("e") != "forceOrder":
                            continue
                        o = d["o"]; t = time.time(); self.liq.append((t, o["s"])); self.stats["force_orders"] += 1
                        while self.liq and t - self.liq[0][0] > 10:
                            self.liq.popleft()
                        per = defaultdict(int)
                        for _, s in self.liq:
                            per[s] += 1
                        if per[o["s"]] >= self.cfg["liq_burst_per_symbol_10s"] or len(self.liq) >= self.cfg["liq_burst_global_10s"]:
                            top = sorted(per.items(), key=lambda kv: -kv[1])[:3] if len(self.liq) >= self.cfg["liq_burst_global_10s"] else [(o["s"], per[o["s"]])]
                            for s, n in top:
                                self.disp.dispatch({"trigger_type": "liquidation_burst", "venue": "binance", "symbol": s, "t0": None, "reason": f"{n} forceOrder messages for {s} in 10 s ({len(self.liq)} total)", "market_type": "perp"})
            except Exception:
                self.stats["liq_ws_errors"] += 1; await asyncio.sleep(3)

    def heartbeat(self):
        try:
            rss_mb = int(open("/proc/self/statm").read().split()[1]) * 4096 // (1 << 20)
        except Exception:
            rss_mb = None
        print(json.dumps({"ts": now_local(), "heartbeat": True, "rss_mb": rss_mb, **dict(self.stats), "active_captures": len(self.state.get("active", {})), "um_symbols": len(self.um.prev or {}), "spot_symbols": len(self.spot.prev or {})}, default=str), flush=True)

    async def run(self):
        started = time.time(); last_hb = time.time(); tasks = [asyncio.ensure_future(c()) for c in (self.exchange_info_loop, self.spot_loop, self.state_loop, self.oi_loop, self.ticker_loop, self.liquidation_loop)]
        while not self.stop.is_set():
            await asyncio.sleep(1)
            if time.time() - last_hb >= self.cfg["heartbeat_s"]:
                self.heartbeat(); last_hb = time.time()
            if self.seconds and time.time() - started >= self.seconds:
                self.stop.set()
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.sink.close(); self.gz.close(); self.save_state()
        return {**dict(self.stats), "lifecycle": self.life.summary(), "um_symbols": len(self.um.prev or {}), "spot_symbols": len(self.spot.prev or {}), "active_captures": len(self.state.get("active", {}))}


def readiness(write: bool = True) -> dict:
    """Ce que la tape contient reellement, sans rien joindre."""
    root = TAPE_ROOT; out = {"generated_at_utc": now_local(), "tape_root": str(root.relative_to(ROOT)), "exchange_info": {}, "lifecycle_events": 0, "light_snapshots_files": 0, "windows": [], "triggers": {"total": 0, "fired": 0, "by_type": {}}}
    for v in ("binance_um", "binance_spot"):
        d = root / "exchange_info" / f"venue={v}"; snaps = sorted(d.glob("snapshot_*.json")) if d.exists() else []
        ch = (d / "changes.jsonl"); n_ch = sum(1 for l in ch.read_text().splitlines() if l.strip()) if ch.exists() else 0
        last = json.loads(snaps[-1].read_text()) if snaps else {}
        out["exchange_info"][v] = {"snapshots": len(snaps), "changes_logged": n_ch, "symbols": last.get("n_symbols"), "last_snapshot_at": last.get("captured_at_local")}
    for p in root.glob("symbol_lifecycle_event/venue=*/date=*/*.jsonl"):
        out["lifecycle_events"] += sum(1 for l in p.read_text().splitlines() if l.strip())
    out["light_snapshots_files"] = len(list(root.glob("market_state_snapshot/venue=*/date=*/*.jsonl.gz")))
    for m in sorted(root.glob("windows/*/manifest.json")):
        d = json.loads(m.read_text())
        out["windows"].append({"trigger_id": d["trigger_id"], "trigger_type": d["trigger_type"], "symbol": d["symbol"], "start_ts": d["start_ts"], "end_ts": d.get("end_ts"), "final": d.get("final"),
                               "snapshots": d["row_counts"].get("snapshots"), "completeness_score": d["completeness_score"], "missing_fields": d["missing_fields"], "first_timestamps": d.get("first_timestamps"),
                               "pre_window_missing": d.get("pre_window_missing"), "latency_ms_median": d.get("latency_ms_median"), "message_counts": d.get("message_counts")})
    if TRIGGERS_LOG.exists():
        for l in TRIGGERS_LOG.read_text().splitlines():
            if l.strip():
                t = json.loads(l); out["triggers"]["total"] += 1; out["triggers"]["fired"] += int(bool(t.get("fired")) and bool(t.get("pid"))); out["triggers"]["by_type"][t["trigger_type"]] = out["triggers"]["by_type"].get(t["trigger_type"], 0) + 1
    out["no_alpha_test"] = True; out["no_orders"] = True
    if write:
        rp = ROOT / "reports" / "data_gap"; rp.mkdir(parents=True, exist_ok=True)
        (rp / "MARKET_STATE_TAPE_READINESS.json").write_text(json.dumps(out, indent=1, ensure_ascii=False, default=str) + "\n")
        w = out["windows"]; lines = [f"# MARKET_STATE_TAPE — readiness ({out['generated_at_utc'][:19]} UTC)", "", "Generated by `market_state_tape --mode readiness` from what is on disk. No price join, no signal, no order.", "",
                                     "| item | value |", "|---|---|"]
        for v, e in out["exchange_info"].items():
            lines.append(f"| exchangeInfo {v} | {e['symbols']} symbols, {e['snapshots']} snapshots archived, {e['changes_logged']} changes logged, last {e['last_snapshot_at']} |")
        lines += [f"| lifecycle events | {out['lifecycle_events']} |", f"| light snapshot files (premiumIndex / OI / ticker) | {out['light_snapshots_files']} |", f"| triggers | {out['triggers']['total']} logged, {out['triggers']['fired']} captures fired, by type {out['triggers']['by_type']} |", f"| triggered windows | {len(w)} |", ""]
        if w:
            lines += ["| window | symbol | type | start | final | snapshots | completeness | missing | first_orderbook | first_trade | first_mark | first_oi | latency ms | pre-window missing |", "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
            for x in w:
                ft = x.get("first_timestamps") or {}
                lines.append(f"| {x['trigger_id']} | {x['symbol']} | {x['trigger_type']} | {x['start_ts'][:19]} | {x['final']} | {x['snapshots']} | {x['completeness_score']} | {', '.join(x['missing_fields']) or '-'} | {(ft.get('first_orderbook_ts') or '-')[11:23]} | {(ft.get('first_trade_ts') or '-')[11:23]} | {(ft.get('first_mark_ts') or '-')[11:23]} | {(ft.get('first_oi_ts') or '-')[11:23]} | {x['latency_ms_median']} | {x['pre_window_missing']} |")
        (rp / "MARKET_STATE_TAPE_READINESS.md").write_text("\n".join(lines) + "\n")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", required=True, choices=["lightweight_watch", "triggered_microstructure_capture", "readiness"])
    ap.add_argument("--symbol"); ap.add_argument("--trigger-type", default="manual", choices=list(TRIGGER_TYPES)); ap.add_argument("--reason", default="manual trigger"); ap.add_argument("--trigger-id")
    ap.add_argument("--t0", help="ISO UTC de l'evenement (lancement) si connu a l'avance"); ap.add_argument("--pre-window", type=int, default=DEFAULTS["pre_window_s"]); ap.add_argument("--post-window", type=int, default=DEFAULTS["post_window_s"])
    ap.add_argument("--venue", default="binance"); ap.add_argument("--prereg-link")
    ap.add_argument("--exchange-info-interval", type=float, default=DEFAULTS["exchange_info_interval"]); ap.add_argument("--state-interval", type=float, default=DEFAULTS["state_interval"]); ap.add_argument("--oi-interval", type=float, default=DEFAULTS["oi_interval"])
    ap.add_argument("--dry-run", action="store_true"); ap.add_argument("--seconds", type=float); ap.add_argument("--no-capture", action="store_true", help="journaliser les declencheurs sans lancer de capture")
    a = ap.parse_args()
    if a.mode == "triggered_microstructure_capture":
        if not a.symbol:
            ap.error("--symbol requis")
        man = MW.capture(a.venue, a.symbol, a.trigger_type, a.reason, a.t0, a.pre_window, a.post_window, trigger_id=a.trigger_id, prereg_link=a.prereg_link)
        print(json.dumps({k: man.get(k) for k in ("trigger_id", "start_ts", "end_ts", "row_counts", "completeness_score", "missing_fields", "first_timestamps", "message_counts", "pre_window_missing")}, indent=1, default=str))
    elif a.mode == "lightweight_watch":
        cfg = dict(DEFAULTS); cfg.update({"exchange_info_interval": a.exchange_info_interval, "state_interval": a.state_interval, "oi_interval": a.oi_interval, "post_window_s": a.post_window, "pre_window_s": a.pre_window})
        w = LightweightWatch(cfg, spawn=not a.no_capture, seconds=a.seconds if (a.dry_run or a.seconds) else None)
        res = asyncio.get_event_loop().run_until_complete(w.run()); print(json.dumps(res, indent=1, default=str))
    else:
        r = readiness(write=True); print(json.dumps({k: r[k] for k in ("generated_at_utc", "exchange_info", "lifecycle_events", "light_snapshots_files", "triggers")}, default=str)); print(f"windows: {len(r['windows'])} -> reports/data_gap/MARKET_STATE_TAPE_READINESS.md")


if __name__ == "__main__":
    main()
