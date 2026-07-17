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
"""
from __future__ import annotations

import fcntl
import os
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Tuple

import pandas as pd


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
    tmp = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        df.to_parquet(tmp, index=False)
        validate_parquet_readable(tmp)
        os.replace(tmp, target)       # atomique (même FS)
        fsync_dir(target.parent)
        validate_parquet_readable(target)
    finally:
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
