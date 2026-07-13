# TRM v5 — verdict de reproduction (2026-07-05) : LE +5,88 %/MOIS EST MORT

## Contexte

La revendication de mai 2026 (« +5,88 %/mois médian, 4/4 folds 2022-2025, AUC 0,81-0,85,
aucun mois négatif sur 48 ») était la SEULE trace d'un moteur ≥5 %/mois dans ce projet.
Le red-team de juin l'avait déclassée (MISSING_ARTIFACT : aucun .pkl chargeable).
Reproduction complète exécutée ce jour avec le script d'origine (`walk_forward_v5.py`,
inchangé, mêmes flags `--no-extra`) sur le store enriched actuel (post-recovery, 9/9 PASS).

## Résultats — reproduction 2022-2025 + fold 2026 inédit

| Fold | AUC méta | PF | WR | ROI/mois | MaxDD | n | verdict |
|---|---:|---:|---:|---:|---:|---:|---|
| 2022 | 0,663 | 0,72 | 45,0 % | **−3,55 %** | 35,4 % | 738 | ✗ |
| 2023 | 0,634 | 3,76 | 67,9 % | +0,84 % | 1,2 % | 56 | ✗ (<5 %) |
| 2024 | 0,623 | 0,89 | 54,1 % | **−0,41 %** | 11,5 % | 220 | ✗ |
| 2025 | 0,644 | 0,73 | 45,7 % | **−1,39 %** | 16,9 % | 368 | ✗ |
| **2026 (jamais vu)** | 0,660 | 0,84 | 33,4 % | **−1,03 %** | 8,2 % | 314 | ✗ |

**Médiane 2022-2025 : −0,90 %/mois, PF 0,81, AUC 0,64. Folds OK : 0/5.**

Revendiqué en mai : AUC 0,81-0,85, PF 3-10, +5,88 %/mois. Mesuré aujourd'hui :
AUC ~0,64, PF ~0,81, −0,9 %/mois. **L'écart n'est pas une dégradation, c'est une
inversion de signe.**

## Lecture

- Le chiffre de mai a été produit sur le store enriched d'AVANT le data-recovery
  (4 fichiers corrompus, gaps, pipeline détruit puis reconstruit depuis l'origine en
  juin). Il n'a jamais été recalculé après la réparation des données, et ses artefacts
  n'ont jamais été persistés. Cause exacte (donnée corrompue vs leakage résiduel vs
  état de code perdu) : indéterminable — et sans objet, puisque non reproductible.
- L'audit statique du pipeline actuel est propre (splits stricts, target = rendement
  8h réalisé, seuil calibré sur val, pas de shift négatif dans les features) : la
  version actuelle du code est honnête, et elle dit NÉGATIF.
- La règle du red-team (« aucun chiffre cité sans artefact chargeable ») est
  définitivement validée par l'expérience.

## Conséquences

1. **Aucun moteur ≥5 %/mois n'a jamais existé de façon reproductible dans ce projet.**
2. La référence de rendement validée reste V1.2 candidate : +8,6 %/an, DD 2,3 %,
   maturité 95/100 (`PARALLEL_50_V12_VALIDATION.md`).
3. La seule piste offensive ouverte = moteur événementiel liquidations sur le feed
   réel bi-source (Bybit WS + OKX REST) déployé le 2026-07-04 : ~1 160 events/jour,
   diagnostic ≥30j (~début août), entraînement ≥60j (~début septembre).
4. `bin/validate` pointe encore sur walk_forward_v5.py : à re-cibler sur la suite de
   maturité institutionnelle.

Verdict : `V5_REPRODUCTION: FAILED — claim buried. 0/5 folds, médiane −0,9 %/mois.`
