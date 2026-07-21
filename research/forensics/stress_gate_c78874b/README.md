status: FORENSIC_CANDIDATE
trusted_as_canonical_source: false
executed_after_recovery: false
remote_host: qbee@100.127.59.114
remote_path: UNKNOWN — retrieval blocked before reaching this step
remote_head: UNKNOWN — retrieval blocked before reaching this step
retrieved_at_utc: NOT_RETRIEVED
retrieval_commit: N/A

# Statut réel : récupération NON effectuée — bloquée au niveau réseau

Ce dossier documente une tentative de récupération forensique, pas une
récupération réussie. Aucun fichier source n'a été rapatrié. Ne rien
traiter ici comme une preuve reconstituée.

## Ce qui a été fait (local, read-only, exhaustif)

Recherche Git exhaustive pour `src/institutional/data/derivatives/features/
cross_exchange_features.py` et l'ancien module
`src/institutional/data/derivatives/cross_exchange.py` :

- `git log --all --full-history -- <chemin>` → vide (les deux fichiers)
- `git rev-list --objects --all | grep '<nom>$'` → vide (les deux fichiers)
- `git log --all --name-status -- '*/cross_exchange_features.py'` → vide
- `git reflog --all --date=iso` (60 entrées, toutes examinées) → aucune
  référence orpheline vers ces chemins
- `git remote -v` → un seul remote, `origin` =
  `https://github.com/chrisloisel2/futur.git` (miroir, pas la machine de
  recherche)
- `git ls-remote --heads --tags origin` → une branche non fusionnée en plus
  de main : `feat/free-derivatives-backfill` (cc328d5). Fetchée et
  inspectée (`git ls-tree -r cc328d5`) : contient
  `scripts/report_cross_exchange_funding_edge.py`,
  `scripts/validate_cross_exchange_signals.py`,
  `tests/test_cross_exchange.py`, `tests/test_cross_exchange_funding_edge.py`
  — **mais toujours pas le module source lui-même.**

**Conclusion (précise, comme demandé) : aucun objet Git atteignable par une
branche ou un tag, locale ou distante (GitHub), ne référence
`cross_exchange_features.py` ni `cross_exchange.py`, à aucun moment de
l'historique.**

## Ce qui n'a PAS pu être fait : récupération sur qbee@100.127.59.114

Tentative de connexion en lecture seule :

```
$ ssh -o BatchMode=yes -o ConnectTimeout=8 qbee@100.127.59.114 "hostname && date -u && whoami"
ssh: connect to host 100.127.59.114 port 22: Operation timed out
$ ping -c 2 100.127.59.114
Request timeout for icmp_seq 0
$ nc -zv -w 5 100.127.59.114 22
(aucune réponse)
```

**Diagnostic : pas de route réseau vers cet hôte depuis l'environnement
d'exécution de cet agent** (100.127.59.114 est une IP de mesh Tailscale ;
cet environnement sandboxé n'a apparemment pas accès à l'interface
Tailscale de la machine hôte, même si la clé SSH et la connectivité
existent potentiellement depuis un terminal utilisateur direct sur le
Mac). Ce n'est PAS un résultat de recherche ("rien trouvé sur qbee") —
c'est une impossibilité d'accès à ce stade. Ne pas confondre les deux.

## Reste à faire (bloqué en attente de décision utilisateur)

Deux voies possibles, à trancher par l'utilisateur :

1. L'utilisateur exécute lui-même le protocole de collecte en lecture
   seule (hostname/date/pwd/git rev-parse/git status/find/pip show/
   sys.path, puis si trouvé : sha256sum + git log --follow du fichier)
   depuis un terminal ayant réellement accès à qbee, et transmet les
   résultats (ou dépose les fichiers retrouvés dans ce dossier sous
   `source/`, `tests/`, `configs/`, `manifests/`) ;
2. L'utilisateur autorise à passer directement à la reconstruction neuve
   (`stress_gate_dispersion_v2_reproduction`, Cas C du protocole validé),
   sans attendre un accès réseau qui n'est pas garanti.

Rien n'a été committé dans `src/`. Le statut `UNVERIFIED_PROVENANCE` reste
affiché dans `research/edge_factory/basis_dispersion/README.md` quelle que
soit la voie choisie ensuite.
