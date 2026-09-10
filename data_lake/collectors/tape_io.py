"""Lecture tolerante des partitions gzip d'une tape EN COURS d'ecriture.

Le collecteur ecrit avec un flush synchrone sans fermer le membre gzip : une
partition vivante n'a pas encore son marqueur de fin, et `gzip.open` leve
EOFError sur sa queue. On rend toutes les lignes completes et on s'arrete la :
la derniere ligne, peut-etre coupee, n'est jamais rendue.
Aucune dependance : importable par les tests sans websockets."""
from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Iterator


def iter_lines(path: Path) -> Iterator[str]:
    with open(path, "rb") as raw:
        g = gzip.GzipFile(fileobj=raw, mode="rb")
        buf = b""
        try:
            while True:
                chunk = g.read(1 << 16)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    yield line.decode("utf-8")
        except EOFError:
            # queue non terminee : ce qui precede est complet, le reste attendra
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                yield line.decode("utf-8")


def iter_records(path: Path) -> Iterator[dict]:
    for line in iter_lines(path):
        if line.strip():
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def read_tape(root: Path, pattern: str = "events-*.jsonl.gz", limit: int = 0) -> list:
    out = []
    for p in sorted(Path(root).rglob(pattern)):
        for r in iter_records(p):
            out.append(r)
            if limit and len(out) >= limit:
                return out
    return out
