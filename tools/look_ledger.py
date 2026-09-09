#!/usr/bin/env python3
"""
look_ledger.py -- le ledger des regards, ecrit AU MOMENT du regard.

Pourquoi il existe. L'audit du 2026-09-09 a cherche les regards passes sur la
fenetre scellee et n'a trouve AUCUN enregistrement : zero mention dans
`MULTIPLICITY_LEDGER.json` et `PREREGISTRATION_LEDGER.jsonl`. Le compte de 16
essais, et le N=138 qui en decoule, sont donc des RECONSTRUCTIONS depuis les
artefacts restes sur disque -- pas des enregistrements. Tout ce qui a tourne sans
laisser d'artefact n'est pas compte. Les seuils sont des BORNES INFERIEURES, et
personne ne sait de combien.

Le correctif ne peut pas etre un meilleur audit : il doit etre une precondition.
    - le ledger est ecrit AVANT que le regard ne calcule quoi que ce soit
    - un regard qui ne peut pas ecrire au ledger NE S'EXECUTE PAS (fail-closed)
    - la chaine est hachee : toute reecriture d'une entree anterieure casse la
      verification, donc un ledger tronque se voit

Il ne remplace pas l'honnetete, il la rend verifiable apres coup.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "reports" / "loop" / "LOOK_LEDGER.jsonl"
GENESIS = "0" * 64


class LedgerError(RuntimeError):
    """Le regard ne doit pas s'executer."""


def _git(*args, cwd=ROOT):
    try:
        out = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)
    except Exception as e:
        raise LedgerError(f"git indisponible : {e}")
    return out.returncode, out.stdout.strip(), out.stderr.strip()


def _sha(obj) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def _entries():
    if not LEDGER.exists():
        return []
    out = []
    for i, line in enumerate(LEDGER.read_text().splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError as e:
            raise LedgerError(f"ledger corrompu ligne {i} : {e}")
    return out


def verify() -> dict:
    """Parcourt la chaine. Leve si une entree a ete reecrite ou retiree."""
    prev, n = GENESIS, 0
    for e in _entries():
        got = dict(e)
        h = got.pop("hash")
        if got.get("prev_hash") != prev:
            raise LedgerError(f"chaine rompue a seq={got.get('seq')} : "
                              f"prev_hash={got.get('prev_hash')} attendu={prev}")
        if _sha(got) != h:
            raise LedgerError(f"entree reecrite a seq={got.get('seq')}")
        prev, n = h, n + 1
    return {"n_entries": n, "head": prev}


def code_state() -> dict:
    rc, sha, _ = _git("rev-parse", "HEAD")
    _, dirty, _ = _git("status", "--porcelain")
    return {"commit": sha if rc == 0 else None,
            "working_tree_dirty": bool(dirty)}


def prereg_witness(path) -> dict:
    """Le pre-enregistrement est-il COMMITE et POUSSE sur le remote ?

    Un fichier cree dix secondes avant le test, dans le meme pipeline, n'est pas
    un engagement -- c'est un artefact. L'horodatage d'un remote est un temoin
    EXTERIEUR qu'on ne peut pas antidater. C'est la seule difference entre un
    pre-enregistrement et une note posterieure."""
    p = Path(path).resolve()
    try:
        rel = str(p.relative_to(ROOT))
    except ValueError:
        return {"path": str(p), "committed": False, "pushed": False,
                "raison": "hors du depot"}
    rc, work_blob, _ = _git("hash-object", rel)
    if rc != 0:
        return {"path": rel, "committed": False, "pushed": False, "raison": "illisible"}
    rc, head_blob, _ = _git("rev-parse", f"HEAD:{rel}")
    if rc != 0:
        return {"path": rel, "blob": work_blob, "committed": False, "pushed": False,
                "raison": "absent de HEAD (jamais commite)"}
    if head_blob != work_blob:
        return {"path": rel, "blob": work_blob, "committed": False, "pushed": False,
                "raison": "modifie depuis le dernier commit"}
    rc, commit, _ = _git("log", "-1", "--format=%H", "--", rel)
    rc_u, upstream, _ = _git("rev-parse", "--abbrev-ref", "@{u}")
    if rc_u != 0:
        return {"path": rel, "blob": work_blob, "commit": commit, "committed": True,
                "pushed": False, "raison": "pas de branche amont"}
    rc_a, _, _ = _git("merge-base", "--is-ancestor", commit, upstream)
    return {"path": rel, "blob": work_blob, "commit": commit, "upstream": upstream,
            "committed": True, "pushed": rc_a == 0,
            "raison": "" if rc_a == 0 else f"commit {commit[:12]} absent de {upstream}"}


def record(kind, window, configs, prereg=None, require_witness=False, note="") -> dict:
    """Inscrit un regard. Leve LedgerError si le regard ne doit pas s'executer.

    kind    : "search" | "placebo" | "confirm"
    window  : (start, end)
    configs : liste de dicts decrivant ce qui est regarde
    prereg  : chemin du pre-enregistrement, si le regard en invoque un
    require_witness : exige que le prereg soit COMMITE ET POUSSE
    """
    verify()                                   # refuse d'ecrire sur une chaine cassee
    w = prereg_witness(prereg) if prereg else None
    if require_witness:
        if w is None:
            raise LedgerError("regard scelle sans pre-enregistrement : refuse")
        if not w.get("pushed"):
            raise LedgerError(
                "le pre-enregistrement n'est pas pousse sur le remote : "
                f"{w.get('raison') or 'inconnu'}. Un fichier local n'est pas un "
                "engagement. Commiter et pousser AVANT de regarder.")
    prev = _entries()
    entry = {
        "seq": len(prev) + 1,
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "prev_hash": prev[-1]["hash"] if prev else GENESIS,
        "kind": kind,
        "window": list(window),
        "n_configs": len(configs),
        "configs": configs,
        "prereg": w,
        "code": code_state(),
        "note": note,
    }
    entry["hash"] = _sha(entry)
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(LEDGER, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
    except OSError as e:
        raise LedgerError(f"ecriture du ledger impossible : {e}. Le regard n'a pas lieu.")
    return entry


def seal(path) -> dict:
    """Commite ET pousse le pre-enregistrement : le rendre opposable.

    Deliberement une commande a part, jamais automatique. Pousser publie, et
    c'est precisement ce qui donne sa valeur au temoin : l'horodatage du remote
    ne peut pas etre antidate. Un pre-enregistrement qu'on peut encore modifier
    n'en est pas un."""
    p = Path(path).resolve()
    rel = str(p.relative_to(ROOT))
    rc, _, err = _git("add", "--", rel)
    if rc != 0:
        raise LedgerError(f"git add a echoue : {err}")
    rc, out, err = _git("commit", "-m", f"prereg: sceller {rel}", "--", rel)
    if rc != 0 and "nothing to commit" not in (out + err):
        raise LedgerError(f"git commit a echoue : {err or out}")
    rc_u, upstream, _ = _git("rev-parse", "--abbrev-ref", "@{u}")
    if rc_u != 0:
        raise LedgerError("pas de branche amont : impossible de produire un temoin")
    remote, branch = upstream.split("/", 1)
    rc, out, err = _git("push", remote, f"HEAD:{branch}")
    if rc != 0:
        raise LedgerError(f"git push a echoue : {err or out}")
    w = prereg_witness(p)
    if not w.get("pushed"):
        raise LedgerError(f"pousse mais non verifiable : {w.get('raison')}")
    return w


def main():
    import argparse
    ap = argparse.ArgumentParser(description="verifier, lister ou sceller")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--witness", metavar="PATH", help="etat de temoin d'un pre-enregistrement")
    ap.add_argument("--seal", metavar="PATH",
                    help="commiter ET pousser un pre-enregistrement (publie)")
    a = ap.parse_args()
    if a.witness:
        print(json.dumps(prereg_witness(a.witness), indent=2, ensure_ascii=False))
        return 0
    if a.seal:
        try:
            w = seal(a.seal)
        except LedgerError as e:
            print(f"scellement REFUSE : {e}")
            return 1
        print(f"scelle : {w['path']}\n  blob   {w['blob']}\n  commit {w['commit']}\n"
              f"  pousse sur {w['upstream']}")
        return 0
    if a.verify or not a.list:
        try:
            r = verify()
            print(f"chaine VALIDE : {r['n_entries']} entrees, tete {r['head'][:16]}")
        except LedgerError as e:
            print(f"chaine INVALIDE : {e}")
            return 1
    if a.list:
        for e in _entries():
            wit = e.get("prereg") or {}
            print(f"  seq={e['seq']:3d} {e['ts']}  {e['kind']:8s} "
                  f"{e['window'][0]}->{e['window'][1]}  n={e['n_configs']:3d}  "
                  f"prereg={'pousse' if wit.get('pushed') else ('commite' if wit.get('committed') else '-')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
