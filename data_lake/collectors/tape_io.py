"""Lecture tolerante des partitions gzip d'une tape EN COURS d'ecriture.

Le collecteur ecrit avec un flush synchrone sans fermer le membre gzip : une
partition vivante n'a pas de marqueur de fin. `gzip.GzipFile.read()` leve alors
EOFError -- et, si le fichier tient dans une lecture, AVANT d'avoir rendu la
moindre ligne (constate le 2026-09-10 : 0 ligne lue, tests passes a vide). On
decompresse donc avec zlib.decompressobj (wbits gzip) : la sortie partielle est
rendue, les membres concatenes (rotation) sont enchaines via unused_data, et la
derniere ligne, peut-etre coupee, n'est jamais rendue.
Aucune dependance : importable par les tests sans websockets."""
from __future__ import annotations

import json
import zlib
from pathlib import Path
from typing import Iterator

_WBITS_GZIP = 16 + zlib.MAX_WBITS


def decompress_partial(raw: bytes) -> bytes:
    """Tout ce qui est decompressable, membres concatenes compris, trailer ou pas."""
    out, data = bytearray(), raw
    while data:
        d = zlib.decompressobj(_WBITS_GZIP)
        try:
            out += d.decompress(data)
        except zlib.error:
            break                       # membre corrompu ou en-tete incomplet : on s'arrete la
        if d.eof and d.unused_data:
            data = d.unused_data        # membre suivant (rotation / reprise)
        else:
            break
    return bytes(out)


def iter_lines(path: Path) -> Iterator[str]:
    payload = decompress_partial(Path(path).read_bytes())
    lines = payload.split(b"\n")
    for line in lines[:-1]:             # lines[-1] est vide (fin sur \\n) ou coupee : jamais rendue
        yield line.decode("utf-8", "replace")


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
