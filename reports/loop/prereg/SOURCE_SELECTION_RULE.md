# Règle de sélection de la source — écrite AVANT tout inventaire

*2026-09-09. Scellée sur branche orpheline avant que la profondeur, l'accès ou la
granularité de l'une ou l'autre source n'aient été vérifiés.*

## La règle

**Parmi les deux sources nommées avant tout inventaire — CME (contrats à terme
Bitcoin) et Bitfinex (marché du prêt en marge USD) —, je retiens celle dont
l'historique quotidien librement accessible est le plus long.**

Elle doit ensuite satisfaire, dans cet ordre, trois conditions :

1. **accès libre et granularité quotidienne** au moins ;
2. **profondeur suffisante** : `T ≥ ((threshold_t(n) + 0,84) / 1,66)²` années, où
   `n` est le nombre de sources examinées jusqu'ici inclus, `threshold_t` est
   unilatéral (direction imposée), 0,84 est le quantile de 80 % de puissance, et
   1,66 le Sharpe arithmétique hors échantillon du livre, pris comme **proxy de
   planification** faute de mieux — soit 2,24 ans à n=1, 2,85 ans à n=2 ;
3. **une hypothèse dont aucun script commité du dépôt n'a mesuré le mécanisme**,
   vérifié par lignée de code, hypothèse écrite avant tout chargement.

Si elle échoue à l'une des trois, la seconde est examinée. **Chaque source
examinée compte un essai** ; `n` vaut donc 1 ou 2, jamais plus. Si les deux
échouent, on s'arrête — aucune exception ne sera fabriquée.

## Priors déclarés, non vérifiés

Je crois, sans l'avoir vérifié, que le marché de prêt Bitfinex remonte à
2013-2014 et les contrats CME à 2017-12. Si c'est vrai, la règle désigne
**Bitfinex en premier** — dont le payeur est celui que mon interlocuteur juge le
plus proche du funding des perpétuels, déjà mesuré en long et en large. Je
déclare ce prior pour qu'un auditeur voie que la règle n'a pas été écrite pour
désigner CME.

## Borne déclarée

*Aucun code commité ne montre de regard sur ces sources ; ni l'une ni l'autre
n'apparaît, même comme chaîne, dans le dépôt. Le code non commité du hunt round 1
est indétectable.* C'est une borne, pas un fait.
