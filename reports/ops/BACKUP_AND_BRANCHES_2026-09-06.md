# Réconciliation des branches et sauvegarde — 2026-09-06

Items E1 et E3.

---

# E1 — l'état des branches

## Le constat de l'audit ne tient plus

L'audit décrivait `feat/free-derivatives-backfill` comme **185 commits devant `main` et 107
derrière**, avec un `main` local en retard sur l'origine et un push rejeté en
non-fast-forward. Mesuré ce jour, après `git fetch` :

| comparaison | devant | derrière |
|---|---|---|
| `HEAD` vs `origin/main` | **4** | **0** |
| `HEAD` vs `origin/feat/free-derivatives-backfill` | **4** | **0** |
| `HEAD` vs `main` (local) | 572 | 0 |

La divergence 185/107 a déjà été résolue à l'origine. Il ne restait qu'une **référence locale
périmée** : le `main` local pointait 572 commits en arrière d'`origin/main`, sans aucun commit
unique à lui (`git rev-list --count origin/main..main` = 0). C'est ce qui donnait l'impression
d'une divergence massive alors que l'origine était à jour.

**Corrigé** : `main` local avancé sur `origin/main` (`3415599`). Opération purement locale et
sans risque — aucun commit unique n'existait sur l'ancien `main`.

## Ce qui reste

- Les **4 commits du jour** (A1, B1, B3, C1+C2) ne sont pas poussés. `HEAD` est un
  fast-forward strict d'`origin/feat/free-derivatives-backfill` : le push est trivial, mais
  c'est une action sortante — décision explicite requise.
- Le worktree `/home/qbee/futur-alpha-foundry-v5` porte `alpha-foundry-v5-local`, **22 commits
  devant** `origin/research/alpha-foundry-v5`, toujours non poussés (l'audit en comptait 19).
  Hors du périmètre déclaré de cette session (« Pas `alpha_foundry_v5` »), donc signalé et non
  touché.

---

# E3 — la sauvegarde

## Le tri, qui est l'essentiel

`data/` pèse 79 Go. Sauvegarder les 79 Go n'est pas « plus prudent » : c'est trois fois plus
long, trois fois plus cher, et ça rend la restauration assez lourde pour qu'on ne la teste
jamais — la façon dont les sauvegardes échouent en pratique.

**Périmètre critique : 17,26 Go.** Ce qui ne se re-télécharge pas.

| chemin | Go | motif |
|---|---|---|
| `data/derivatives_raw` | 8,85 | collecteur REST 5 min ; `openInterestHist` ne retient que ~30 j — au-delà, **déjà irrécupérable côté exchange** |
| `data/microstructure_reduced` | 4,78 | bande BBO/trades websocket ; aucune API ne rejoue un carnet passé |
| `data/hyperliquid` | 3,28 | collecteur metaorders/l2Book |
| `data/execution_probe` | 0,20 | sondes d'exécution horodatées |
| `reports/live_alpha_lab` | 0,07 | **ledgers scellés** — append-only, donc non reconstructibles |
| `data/positioning`, `reports/edge_discovery`, `configs`, `state`, `data/spread_probe`, `data/derivatives_live_metrics` | 0,08 | petits et critiques (dont les fichiers gitignorés absents du dépôt) |

**Hors périmètre, délibérément :** `data/enriched` (48 Go — dérivé de klines publiques **et**
périmé depuis fin juin pour 40 des 50 symboles : 60 % du volume, 0 % du risque),
`data/derivatives_backfill` (5,7 Go — archives publiques Binance Vision),
`data/options_backfill`, `data/listings_backfill`, `data/session_*`.

## Le vrai obstacle n'est pas le volume

**3 285 247 fichiers.** Dont **3 171 884 dans `derivatives_raw`, à 2,9 Ko de moyenne** — 97 %
du compte de fichiers pour 51 % des octets.

Trois conséquences, toutes coûteuses :

1. Sur un système de fichiers à blocs de 4 Ko, un fichier de 2,9 Ko en occupe 4. 8,85 Go de
   contenu tiennent **17 Go de disque** — environ 8 Go de pure perte, sur un disque à 94 %.
2. Un `rsync` fichier par fichier est dominé par le coût **par fichier**, pas par le débit.
3. Un stockage objet facture **à la requête** : 3,17 M de PUT est le vrai poste de dépense,
   pas les 17 Go.

**Mesuré** sur un symbole (GRTUSDT, 66 partitions closes) : regroupement en archives par
(symbole, jour) → 66 archives, **5,0 Mo**, en 7,9 s, pour ~32 000 fichiers d'origine.
Extrapolé à 49 symboles : **~3 200 archives** au lieu de 3,17 M de fichiers (facteur ~900) et
quelques centaines de Mo au lieu de 8,85 Go.

`--pack` ne touche **que les partitions closes** (`date=` < aujourd'hui, UTC) et **ne supprime
jamais l'original**. La partition du jour est en cours d'écriture par le collecteur :
l'archiver donnerait une archive tronquée, et la relire pendant l'écriture produit exactement
le gzip incomplet rencontré dans le tape microstructure. Écriture en `.tmp` puis renommage
atomique — une archive n'apparaît sous son nom définitif que complète.

## La restauration est testée, pas seulement documentée

| test | résultat |
|---|---|
| `--test-restore` (copie + SHA-256 sur échantillon du périmètre réel) | **PASS — 39/39 identiques octet à octet** |
| aller-retour `--pack` puis dépaquetage, comparaison au contenu d'origine | **3 404/3 404 identiques octet à octet, 0 écart** |

`verify` distingue un fichier **absent** d'un fichier **altéré** : ce ne sont pas la même
panne et elles n'appellent pas la même réponse.

Manifeste : `reports/ops/BACKUP_MANIFEST.json` (mode `SAMPLE` pour l'instant — 16 426 fichiers
hachés sur 3 285 235 ; le mode `FULL` n'a de sens qu'après le regroupement, sinon il
consisterait à hacher 3,17 M de fichiers de 2,9 Ko).

## Chemin de restauration

```bash
# 1. inspecter ce qui est sauvegardé, et pourquoi
.venv/bin/python scripts/backup_critical_state.py --scope

# 2. regrouper les partitions closes (ne supprime rien)
.venv/bin/python scripts/backup_critical_state.py --pack /chemin/staging

# 3. manifeste SHA-256 + preuve de restauration
.venv/bin/python scripts/backup_critical_state.py --manifest --test-restore

# 4. copie hors machine
.venv/bin/python scripts/backup_critical_state.py --copy /media/destination

# 5. vérifier la destination (absents et altérés comptés séparément)
.venv/bin/python scripts/backup_critical_state.py --verify /media/destination

# restauration : rsync inverse, puis `tar xzf` de chaque archive depuis la racine
# du dépôt (les arcname sont relatifs à la racine, donc l'arborescence se
# reconstitue telle quelle).
```

## Ce qui reste ouvert

**La copie hors machine n'a pas de destination.** Tout le reste est en place et vérifié ; il
manque le « où ». Cette décision n'est pas technique — c'est un choix de coût, de
juridiction et d'accès qui n'appartient pas à ce script.

Second point, indépendant de la sauvegarde : regrouper `derivatives_raw` **récupérerait ~8 Go
de blocs perdus** sur un disque à 94 %. C'est la seule opération de ce document qui améliore
la situation même si aucune sauvegarde n'est jamais faite — mais elle suppose de supprimer
les originaux après vérification, ce que `--pack` ne fait délibérément pas tout seul.
