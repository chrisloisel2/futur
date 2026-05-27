#!/usr/bin/env python3
"""
scripts/supervisor.py
=====================
Superviseur des scrapers et du daemon de données.
Lance, surveille et redémarre automatiquement tous les collecteurs.

Usage :
  python scripts/supervisor.py          # Lance tout en mode daemon
  python scripts/supervisor.py --dry    # Affiche ce qui serait lancé
"""
from __future__ import annotations
import argparse, logging, os, signal, subprocess, sys, time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = Path("/tmp/futur_logs")
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  [supervisor] %(message)s",
    datefmt="%H:%M:%S")
log = logging.getLogger("supervisor")

PY3 = sys.executable

PROCESSES: Dict[str, Dict] = {
    "data_daemon": {
        "cmd": [PY3, str(ROOT / "scripts" / "data_daemon.py")],
        "cwd": str(ROOT),
        "restart_delay": 30,
        "max_restarts": 99,
        "log": str(LOG_DIR / "data_daemon.log"),
        "critical": True,   # redémarre immédiatement si crash
    },
    "fetch_news": {
        "cmd": [PY3, str(ROOT / "scripts" / "fetch_news.py"), "--update"],
        "cwd": str(ROOT),
        "restart_delay": 7200,   # toutes les 2h
        "max_restarts": 99,
        "log": str(LOG_DIR / "fetch_news.log"),
        "critical": False,
        "run_every": 7200,      # lance toutes les 2h (pas un daemon)
    },
    "fetch_1m_btc": {
        "cmd": [PY3, str(ROOT / "scripts" / "fetch_1m_history.py"), "--symbol", "BTCUSDT", "--update"],
        "cwd": str(ROOT),
        "restart_delay": 300,   # toutes les 5min pour les updates
        "max_restarts": 99,
        "log": str(LOG_DIR / "fetch_1m_btc.log"),
        "critical": False,
        "run_every": 300,
    },
    "fetch_whale": {
        "cmd": [PY3, str(ROOT / "scripts" / "fetch_whale_onchain.py"), "--days", "2", "--blocks", "5"],
        "cwd": str(ROOT),
        "restart_delay": 1800,
        "max_restarts": 99,
        "log": str(LOG_DIR / "fetch_whale.log"),
        "critical": False,
        "run_every": 1800,   # toutes les 30min
    },
    "alpha_ingest": {
        "cmd": [PY3, str(ROOT / "scripts" / "ingest_alpha_data.py"), "--update"],
        "cwd": str(ROOT),
        "restart_delay": 3600,
        "max_restarts": 99,
        "log": str(LOG_DIR / "alpha_ingest.log"),
        "critical": False,
        "run_every": 3600,   # toutes les heures
    },
}

# État runtime
_procs: Dict[str, subprocess.Popen] = {}
_restarts: Dict[str, int] = {k: 0 for k in PROCESSES}
_last_run: Dict[str, float] = {}
_running = True


def _start(name: str, dry: bool = False) -> Optional[subprocess.Popen]:
    cfg = PROCESSES[name]
    if dry:
        log.info(f"[DRY] {name}: {' '.join(cfg['cmd'])}")
        return None
    try:
        log.info(f"Démarrage: {name}")
        proc = subprocess.Popen(
            cfg["cmd"],
            cwd=cfg["cwd"],
            stdout=open(cfg["log"], "a"),
            stderr=subprocess.STDOUT,
            env={**os.environ, "PYTHONPATH": str(ROOT)},
        )
        _last_run[name] = time.time()
        log.info(f"  {name} PID={proc.pid}")
        return proc
    except Exception as e:
        log.error(f"  Échec démarrage {name}: {e}")
        return None


def _should_run(name: str) -> bool:
    """Pour les processus à lancement périodique (run_every), vérifie si c'est l'heure."""
    cfg = PROCESSES[name]
    every = cfg.get("run_every")
    if not every:
        return False   # daemon permanent → géré par restart
    last = _last_run.get(name, 0)
    return (time.time() - last) >= every


def _stop_all():
    global _running
    _running = False
    log.info("Arrêt du superviseur…")
    for name, proc in _procs.items():
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
    log.info("Tous les processus arrêtés.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry", action="store_true")
    args = parser.parse_args()

    signal.signal(signal.SIGTERM, lambda *_: _stop_all())
    signal.signal(signal.SIGINT,  lambda *_: _stop_all())

    log.info("=" * 60)
    log.info("FUTUR SUPERVISOR")
    log.info(f"ROOT: {ROOT}")
    log.info(f"Processus configurés: {list(PROCESSES.keys())}")
    log.info("=" * 60)

    # Lancer les daemons critiques immédiatement
    for name, cfg in PROCESSES.items():
        if not cfg.get("run_every"):   # daemon permanent
            proc = _start(name, args.dry)
            if proc:
                _procs[name] = proc

    # Boucle principale
    while _running:
        for name, cfg in PROCESSES.items():
            every = cfg.get("run_every")

            if every:
                # Processus périodique : lance si c'est l'heure et pas en cours
                if name in _procs and _procs[name].poll() is None:
                    continue   # encore en cours
                if _should_run(name):
                    proc = _start(name, args.dry)
                    if proc:
                        _procs[name] = proc
            else:
                # Daemon permanent : redémarre si crashé
                proc = _procs.get(name)
                if proc is None or proc.poll() is not None:
                    rc = proc.poll() if proc else None
                    if rc is not None:
                        log.warning(f"  {name} terminé (rc={rc}), redémarrage dans {cfg['restart_delay']}s")
                        time.sleep(cfg["restart_delay"])
                        _restarts[name] += 1
                    new_proc = _start(name, args.dry)
                    if new_proc:
                        _procs[name] = new_proc

        # Status tous les 60s
        if int(time.time()) % 60 == 0:
            for name, proc in _procs.items():
                status = "running" if proc.poll() is None else f"stopped (rc={proc.poll()})"
                log.info(f"  {name}: {status} | restarts: {_restarts[name]}")

        time.sleep(15)


if __name__ == "__main__":
    main()
