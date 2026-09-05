#!/usr/bin/env python3
"""W9 — export COMMITTABLE des tables d'audit.

`.gitignore:22` ignore `*.csv` sur tout le depot : les livrables CSV de ce worker ne seraient
jamais versionnes. Ce script en produit une copie JSON, versionnable, a contenu identique.
Usage: .venv/bin/python evidence/export_audit_json.py
"""
import csv, json, os
HERE = os.path.dirname(os.path.abspath(__file__))
def rd(n):
    p = os.path.join(HERE, n)
    return list(csv.DictReader(open(p))) if os.path.exists(p) else None
out = {
  "_note": ("Copie versionnable des tables d'audit de W9 (`*.csv` est ignore par .gitignore:22). "
            "Contenu identique aux CSV du meme dossier."),
  "column_verdicts": rd("COLUMN_VERDICTS.csv"),
  "never_use": rd("NEVER_USE.csv"),
  "audit_summary_by_family": rd("AUDIT_SUMMARY_BY_FAMILY.csv"),
  "source_attribution": rd("SOURCE_ATTRIBUTION.csv"),
  "seams": rd("SEAMS.csv"),
  "crosscheck_v2": rd("CROSSCHECK_V2.csv"),
  "volume_concordance": rd("VOLUME_CONCORDANCE.csv"),
}
p = os.path.join(HERE, "COLUMN_AUDIT.json")
json.dump(out, open(p, "w"), ensure_ascii=False, indent=0)
print("ecrit", p, os.path.getsize(p)//1024, "Ko")
for k, v in out.items():
    if isinstance(v, list): print(f"  {k:26s} {len(v):5d} lignes")
