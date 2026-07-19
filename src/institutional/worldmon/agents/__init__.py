"""Agents autonomes du World Monitor (pipeline scientifique reproductible).

Chaque agent : contrat run()->dict, s'isole (try/except), écrit son health,
ne casse jamais la chaîne. Aucune donnée fabriquée. Ordre canonique :
  Ingestor → Enricher → Quality → Correlator → Supervisor
"""
