"""
src/institutional/data/atomic_parquet.py
─────────────────────────────────────────────────────────────────────────────
Écriture parquet ATOMIQUE + append concurrence-safe (Phase 30).

Règle absolue : aucun process ne doit écrire DIRECTEMENT dans
data/enriched/*.parquet. Toute écriture passe par ici :

    lock exclusif (flock)
    → lecture ancien fichier (quarantaine si corrompu, jamais réparer en silence)
    → concat / dedupe / sort
    → écriture fichier temporaire (même filesystem)
    → validation du temp (magic bytes + lecture)
    → os.replace atomique
    → fsync du dossier
    → validation finale

os.replace est atomique sur le même filesystem : un lecteur voit toujours soit
l'ancien fichier complet, soit le nouveau complet — jamais un fichier tronqué.

⚠ FUITE RÉELLE TROUVÉE le 2026-09-04 (audit infrastructure, item P0.4)
──────────────────────────────────────────────────────────────────────
`atomic_write_parquet` nettoyait son fichier temporaire dans un `finally`.
Un `finally` couvre les exceptions — il ne couvre PAS un SIGKILL, un OOM
kill, un `systemctl stop` qui expire en SIGKILL, ni une coupure machine.
Chaque interruption de ce type laissait donc un `.tmp` de la TAILLE DU
FICHIER CIBLE, définitivement.

Mesure au moment de la découverte : 41 Go de `.tmp` orphelins dans
data/enriched/, datés du 4 juillet au 4 septembre — plus du double de
l'espace libre restant (19 Go), sur un disque à 98 %. C'est la cause
racine de la pénurie disque, pas une conséquence.

Aggravant structurel : un enriched est un parquet de 4050 colonnes × ~73k
lignes, soit ~1,4-1,7 Go. Chaque append relit tout, concatène et réécrit
tout : le pic transitoire est de 2× la taille du fichier. Une interruption
pendant cette fenêtre laisse ~1,5 Go au sol.

Trois corrections ici :
  1. `_TMP_IN_FLIGHT` + handler SIGTERM/SIGINT + atexit : les interruptions
     PROPRES (dont `systemctl stop`, donc le disk watchdog) nettoient
     désormais leur temporaire. SIGKILL reste incatchable par nature.
  2. `sweep_orphan_tmp()` : recense les orphelins que le point 1 ne peut pas
     couvrir (SIGKILL, crash machine, fichiers historiques déjà au sol).
  3. DRY-RUN PAR DÉFAUT. Cette fonction ne supprime RIEN sans `delete=True`
     explicite, et ne considère jamais comme orphelin un `.tmp` récent
     (fenêtre de garde) qui pourrait appartenir à une écriture en cours.
"""
from __future__ import annotations

import atexit
import fcntl
import os
import signal
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import pandas as pd

# Temporaires en vol pour CE process. Nettoyés sur SIGTERM/SIGINT/atexit --
# le `finally` seul ne suffisait pas (voir en-tête).
_TMP_IN_FLIGHT: Set[Path] = set()
_SIGNAL_HANDLERS_INSTALLED = False

# Un `.tmp` plus jeune que ça peut appartenir à une écriture EN COURS dans un
# autre process : jamais considéré comme orphelin. Une écriture d'enriched
# (1,5 Go) prend quelques minutes ; 6 h est délibérément très large — se
# tromper ici coûterait un fichier valide, se tromper dans l'autre sens ne
# coûte qu'un passage de balayage supplémentaire.
ORPHAN_MIN_AGE_SECONDS = 6 * 3600


def _cleanup_in_flight(*_args) -> None:
    for tmp in list(_TMP_IN_FLIGHT):
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        _TMP_IN_FLIGHT.discard(tmp)


def _install_signal_handlers() -> None:
    """Idempotent, et ne vole JAMAIS un handler déjà posé par l'application
    (le collecteur microstructure gère lui-même SIGTERM pour flusher) : on
    chaîne, on ne remplace pas."""
    global _SIGNAL_HANDLERS_INSTALLED
    if _SIGNAL_HANDLERS_INSTALLED:
        return
    _SIGNAL_HANDLERS_INSTALLED = True
    atexit.register(_cleanup_in_flight)
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            previous = signal.getsignal(sig)
        except (ValueError, OSError):
            continue

        def _chained(signum, frame, _prev=previous):
            _cleanup_in_flight()
            if callable(_prev):
                _prev(signum, frame)
            elif _prev == signal.SIG_DFL:
                signal.signal(signum, signal.SIG_DFL)
                os.kill(os.getpid(), signum)

        try:
            signal.signal(sig, _chained)
        except (ValueError, OSError):
            # pas le thread principal (ou plateforme sans ce signal) :
            # atexit + le finally restent en place, on ne casse rien.
            pass


def sweep_orphan_tmp(directory: Path, delete: bool = False,
                     min_age_seconds: int = ORPHAN_MIN_AGE_SECONDS,
                     now: Optional[float] = None) -> Dict[str, object]:
    """Recense (et, seulement si `delete=True`, supprime) les `.tmp` orphelins
    laissés par une écriture atomique interrompue.

    DRY-RUN PAR DÉFAUT : sans `delete=True`, cette fonction ne modifie rien.
    C'est délibéré — la règle du projet interdit toute suppression non
    demandée, et un `.tmp` est certes un fragment d'écriture avortée, mais
    c'est à l'humain de valider la reprise d'espace.

    Ne considère comme orphelin QUE les fichiers qui portent le motif
    `.<nom cible>.<hex>.tmp` produit par `atomic_write_parquet` ET dont la
    dernière modification remonte à plus de `min_age_seconds`.
    """
    directory = Path(directory)
    now = time.time() if now is None else now
    found: List[dict] = []
    deleted: List[str] = []
    total = 0
    if not directory.exists():
        return {"directory": str(directory), "n_orphans": 0, "total_bytes": 0,
                "deleted": [], "dry_run": not delete, "orphans": []}

    for p in sorted(directory.glob(".*.tmp")):
        try:
            st = p.stat()
        except OSError:
            continue
        age = now - st.st_mtime
        if age < min_age_seconds:
            continue
        found.append({"path": str(p), "bytes": st.st_size, "age_seconds": int(age)})
        total += st.st_size
        if delete:
            try:
                p.unlink()
                deleted.append(str(p))
            except OSError:
                pass
    return {"directory": str(directory), "n_orphans": len(found), "total_bytes": total,
            "total_gb": round(total / (1024 ** 3), 3), "deleted": deleted,
            "dry_run": not delete, "orphans": found}


@contextmanager
def file_lock(lock_path: Path):
    """Verrou exclusif inter-process (flock). Un seul writer à la fois."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def fsync_dir(path: Path) -> None:
    dir_fd = os.open(str(path), os.O_DIRECTORY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def validate_parquet_readable(path: Path) -> None:
    """Magic bytes PAR1 + lecture non vide. Lève si invalide."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)
    if path.stat().st_size < 8:
        raise ValueError(f"Parquet trop petit: {path}")
    with open(path, "rb") as f:
        head = f.read(4)
        f.seek(-4, os.SEEK_END)
        tail = f.read(4)
    if head != b"PAR1" or tail != b"PAR1":
        raise ValueError(f"Magic bytes parquet invalides: {path}")
    df = pd.read_parquet(path)
    if df.empty:
        raise ValueError(f"Parquet vide après écriture: {path}")


def atomic_write_parquet(df: pd.DataFrame, target: Path) -> None:
    """Écrit df dans target de façon atomique (temp + os.replace + fsync)."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    _install_signal_handlers()
    tmp = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    _TMP_IN_FLIGHT.add(tmp)
    try:
        df.to_parquet(tmp, index=False)
        validate_parquet_readable(tmp)
        os.replace(tmp, target)       # atomique (même FS)
        fsync_dir(target.parent)
        validate_parquet_readable(target)
    finally:
        _TMP_IN_FLIGHT.discard(tmp)
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def append_enriched_atomic(
    target: Path,
    new_rows: pd.DataFrame,
    timestamp_col: str = "datetime",
    dedupe_cols: Tuple[str, ...] = ("datetime",),
) -> int:
    """
    Append concurrence-safe à un enriched parquet. Retourne le nb de lignes final.

    Si le fichier existant est corrompu : quarantaine + exception (jamais écraser,
    jamais réparer en silence — rebuild from origin requis).
    """
    target = Path(target)
    lock_path = target.with_suffix(target.suffix + ".lock")

    with file_lock(lock_path):
        if target.exists():
            try:
                old = pd.read_parquet(target)
            except Exception as exc:
                quarantine = target.with_suffix(
                    target.suffix + f".corrupt.{uuid.uuid4().hex}")
                os.replace(target, quarantine)
                fsync_dir(target.parent)
                raise RuntimeError(
                    f"Parquet existant corrompu. Mis en quarantaine → {quarantine}. "
                    "Append refusé. Rebuild from origin requis."
                ) from exc
            combined = pd.concat([old, new_rows], ignore_index=True)
        else:
            combined = new_rows.copy()

        combined = combined.drop_duplicates(list(dedupe_cols), keep="last")
        combined = combined.sort_values(timestamp_col).reset_index(drop=True)

        if combined[timestamp_col].duplicated().any():
            raise ValueError(f"Timestamps dupliqués après dedupe: {target}")
        if not combined[timestamp_col].is_monotonic_increasing:
            raise ValueError(f"Timestamps non monotones: {target}")

        atomic_write_parquet(combined, target)
        return len(combined)
