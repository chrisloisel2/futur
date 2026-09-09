# CME_SEGMENTATION_V1 — préenregistrement, source examinée n° 2

*Écrit le 2026-09-09, avant tout chargement de données CME. Règle de sélection : `SOURCE_SELECTION_RULE`
(branche `prereg/source-selection-rule`, commit `e765081`), écrite avant l'inventaire. Bitfinex a été
examinée en premier et refusée à la condition 3 (`EXAMEN_BITFINEX_2026-09-09.md`, ledger seq 4) :
**n = 2**.*

## 1. Le payeur, en une phrase falsifiable

**L'institution régulée — fonds, teneur de marché d'ETF, trésorerie — qui ne peut détenir ni
perpétuels ni spot sur une plateforme offshore et achète son exposition BTC par les contrats CME
paie, pour cet accès conforme, une prime supérieure au prix du levier retail ; elle continue parce
que la conformité n'est pas facultative pour elle ; elle cesserait si un canal conforme moins cher
se généralisait.** Les ETF spot américains (2024-01-11) sont exactement cela : **la prime relative
doit se comprimer après cette date.** C'est une prédiction secondaire, écrite avant.

Falsifiable : si les extrêmes de la prime institutionnelle relative ne sont pas suivis d'un
rendement BTC de signe opposé, l'hypothèse est fausse.

## 2. Le mécanisme

Deux marchés cloisonnés pour le même levier : CME (institutionnel, conforme) et les perpétuels
offshore (retail, global). L'écart
`d = basis CME annualisé − funding perp annualisé`
mesure l'**encombrement institutionnel relativement au retail**. Le retail encombré a déjà été
mesuré cent fois ici (funding, positionnement) ; le terme *institutionnel* de cet écart n'a jamais
été observé — CME n'a jamais été sur ce disque (lignée : 0 fichier). Aux extrêmes, le payeur est
surexposé et perd : la position se dénoue.

## 3. La direction, imposée

**Vendre BTC quand `z ≥ +1`, acheter quand `z ≤ −1`, rester à plat entre les deux.** Contrarien à
l'encombrement institutionnel. Le harnais ne choisit aucun côté.

## 4. La configuration, dérivée du mécanisme

| paramètre | valeur | pourquoi, depuis le mécanisme |
|---|---|---|
| actif | BTC seul | le produit institutionnel de CME ; ETH n'y est coté que depuis 2021 et n'est pas inclus |
| jambe CME | Yahoo `BTC=F`, front-month continu, clôture quotidienne | la seule série libre et complète (2 190 barres) ; ses barres sont bornées à **minuit New York** |
| jambe spot | Binance spot BTCUSDT, la bougie 1 h dont la clôture tombe sur **l'horodatage exact de la barre Yahoo du jour** (04:00Z en été, 05:00Z en hiver) | aligner les deux jambes à la même heure : un basis sur deux clôtures décalées est du bruit intrajournalier, pas une prime |
| jambe retail | funding Binance BTCUSDT du jour **D−1**, ×365 | le prix du levier retail ; le jour D somme des règlements encore à venir à 04:00Z |
| annualisation CME | `(F/S − 1) × 365 / DTE`, DTE au dernier vendredi du mois (règle de calendrier CME) | rendre le basis comparable au funding ; aucune donnée n'entre dans la règle |
| standardisation | z-score glissant **90 j**, min 60 | un trimestre = un cycle de contrat, le cycle de positionnement institutionnel |
| seuil d'action | `\|z\| ≥ 1` | « les extrêmes » : hors de l'écart-type, comme les déciles du test transversal |
| exécution | signal sur la barre de `t`, position **établie à la barre de `t+1`**, tenue un jour | la première exécution praticable après le signal ; jamais sur la barre du signal |
| instrument tradé | perp Binance BTCUSDT, même alignement horaire | c'est ce qu'on peut réellement tenir long ou short |
| coût | **10 bps** par unité de position tournée | hypothèse déclarée pour BTC perp, non mesurée |
| fenêtre | du **2020-01-01** (première bougie perp 1 h sur Vision) à la dernière date disponible | tout l'historique CME est un seul regard : jamais chargé |

## 5. Seuil, puissance, arrêt

- **n = 2** (Bitfinex, puis CME). Seuil **unilatéral** `threshold_t(2) = 1,9600` sur le `t` de
  Newey-West (lag 5) du **net** quotidien, tous jours confondus (à plat = 0).
- Puissance : `T ≥ ((1,96 + 0,84)/1,66)² = 2,85` ans au proxy 1,66 ; la fenêtre en a ≈ 6,7.
- **Un seul test.** Si l'acquisition révèle que la donnée ne permet pas cette configuration
  (clôture Yahoo non alignée, mois manquants, roll non conforme), **le préenregistrement est perdu
  et compte quand même** — n = 2 consommé. Rien ne sera réécrit pour coller à la donnée.
- Budget : l'exécution débite 1 des 3 tests du `LOOP_STATE`.

## 6. La contrainte de queue, fixée avant

Validité = `t_net ≥ 1,96`. **Déployabilité**, en plus : perte maximale à 1× ≤ **25 %** ;
asymétrie quotidienne ≥ **−1,0** ; et cohérence avec la prédiction secondaire — un net
**plus faible après 2024-01-11** qu'avant. Une hypothèse valide mais incohérente avec sa propre
prédiction secondaire est signalée, pas déployée.

## 7. Fuites déclarées, et ce que ce test n'est pas

- **La feature est neuve, la cible ne l'est pas** : le rendement quotidien de BTC a été regardé
  par d'autres familles (tendance, cascades, rounds 3-4). La configuration ci-dessus ne reprend
  aucun paramètre d'un balayage.
- **Inventaire** : des réponses Yahoo et Bitfinex ont été téléchargées pour en lire les
  *horodatages et codes HTTP* ; **aucune valeur n'a été lue ni imprimée**, et les fichiers ont été
  supprimés avant l'écriture de ce document.
- **Jambe funding** : donnée déjà sur disque et déjà regardée, utilisée ici comme *entrée* du
  différentiel, pas comme hypothèse.
- **Borne déclarée** : aucun code commité ne mentionne CME ; le code non commité du hunt
  round 1 est indétectable.
- **Interdit après scellement** : re-regarder, ajuster un paramètre, seconde implémentation.

## 8. Ordre d'exécution et code pinné

1. sceller ce fichier (branche orpheline `prereg/cme-segmentation-v1`, fichier unique, poussée seule)
2. vérifier `pushed: true`, inscrire le scellement au ledger
3. **alors seulement** `python3 scripts/fetch_cme_basis_inputs.py` (écrit `MANIFEST.json`)
4. `python3 tools/test_cme_segmentation_v1.py` — inscrit le regard **avant** de calculer, exige
   le témoin, refuse si une pin diffère

```json pins
{
  "tools/test_cme_segmentation_v1.py": "21fbabc5fb264aea19af6619af713b2f89cd1b89397baff9755620822b30decb",
  "scripts/fetch_cme_basis_inputs.py": "2256aeeb5fc05e7fdfb1521cff1837d6b29c0acc2dfe2239a4c18a8128b01dbf",
  "tools/look_ledger.py": "c9bb473326a812f27fb53f4b261c565a1fa4df1ad6085c165d87d01a5d058b14",
  "src/institutional/live_alpha_lab/preregistration.py": "bd307deeba684c28662bd6579a65beadd4aec2234d967c530f2b211ab3f0cc39"
}
```
