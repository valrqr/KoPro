# Les impayés : du relevé bancaire à la mise en demeure

[← Retour à l'accueil](../README.md)

Relancer un voisin est la tâche la plus ingrate du syndic bénévole. Il faut d'abord savoir qui a payé, en épluchant le relevé, puis trouver le bon ton et respecter une procédure précise si l'impayé dure. Mitoyen fait le pointage et prépare les courriers. Le syndic garde la main sur chaque envoi.

## En quatre temps

| Étape | Ce que fait Mitoyen | Ce que fait le syndic |
|---|---|---|
| **1. Import** | Lit le relevé CSV de la banque, quel que soit son format, sans créer de doublon si deux relevés se chevauchent | Télécharge le relevé depuis sa banque et l'envoie |
| **2. Rapprochement** | Rattache chaque crédit à un copropriétaire : automatiquement quand c'est sûr, sous forme de proposition quand c'est probable | Confirme ou corrige les propositions |
| **3. Comptes** | Calcule le solde de chacun, échéance par échéance (les paiements soldent les échéances les plus anciennes d'abord) | Ajoute ce qui n'est pas dans Mitoyen : solde antérieur, appel de travaux, avoir |
| **4. Relances** | Propose la relance du bon niveau, au bon moment, et prépare la lettre | Relit, signe, envoie, puis indique la date d'envoi |

## Le rapprochement

| Ce que Mitoyen reconnaît | Exemple de libellé | Résultat |
|---|---|---|
| La référence de l'avis d'appel, même collée ou tronquée | `VIR SCI LES PINS MIMOSAS2027T1CP06` | Automatique |
| Une remise de prélèvements SEPA dont le total correspond exactement | `REMISE PRLV SEPA MIMOSAS ECH 01/01/2027` | Automatique, réparti entre les copropriétaires prélevés |
| Un rejet de prélèvement portant la référence du mandat | `REJET PRLV SEPA … MIMOSAS-CP05` | Automatique : la somme redevient due |
| Le nom du copropriétaire | `VIR M ROUX MANON CHARGES COPRO` | **Proposé**, à confirmer |
| Un montant que doit un seul copropriétaire | `REMISE CHEQUE N 4417202` | **Proposé**, à confirmer |

**Garde-fou :** une proposition n'est jamais comptée tant que le syndic ne l'a pas confirmée, et le copropriétaire concerné ne peut pas être relancé en attendant. On ne relance pas quelqu'un qui a peut-être payé.

## Les relances, dans l'ordre

| Niveau | Proposée | Ton et contenu | Envoi |
|---|---|---|---|
| **1. Rappel amiable** | 15 jours après l'échéance | « Sans doute un oubli », proposition d'échéancier en cas de difficulté | Canal habituel (mail ou courrier) |
| **2. Relance** | 30 jours après l'échéance, au moins 15 jours après le rappel | Plus ferme : explique ce qu'entraînerait une mise en demeure | Canal habituel |
| **3. Mise en demeure** | 45 jours après l'échéance, au moins 15 jours après la relance | Détail de chaque provision, délai de 30 jours, conséquences légales | **Recommandé** obligatoire (Mitoyen refuse de la marquer envoyée par mail) |

Mitoyen ne saute jamais une étape, même pour un impayé très ancien : le premier courrier reste un rappel amiable. Les délais se règlent dans la section `recouvrement` de la fiche.

Ce que contient la mise en demeure, et pourquoi :

- **La nature et le montant de chaque provision réclamée**, rubrique par rubrique. Une mise en demeure trop vague rend la procédure irrecevable (Cour de cassation, avis du 12 décembre 2024).
- **Le délai de 30 jours** à compter du lendemain de la première présentation, et ce qui se passe ensuite : les autres provisions de l'exercice deviennent exigibles (loi du 10 juillet 1965, art. 19-2). Mitoyen chiffre ces provisions à échoir.
- **Les intérêts au taux légal** à partir de la mise en demeure (décret du 17 mars 1967, art. 36), sauf clause contraire du règlement. Mitoyen les calcule semestre par semestre au taux « autres cas » et signale quand le taux d'un semestre n'est pas encore à jour.
- **Les frais** : seuls ceux engagés après la mise en demeure peuvent être mis à la charge du débiteur (art. 10-1). Les rappels amiables restent gratuits.

Trente jours après la présentation du recommandé, si rien n'est payé, Mitoyen le signale dans l'échéancier : la suite relève du tribunal judiciaire. Mitoyen ne la lance pas, et recommande de se faire conseiller avant.

## L'état des impayés

`GET /copros/{copro}/impayes.pdf` produit une page pour le syndic et le conseil syndical : total des impayés, débiteurs et prochaine étape pour chacun, opérations du relevé à vérifier. Il fournit aussi le nombre de copropriétaires débiteurs de plus de 300 €, que demande la fiche synthétique. Ce document contient des données personnelles : il ne s'affiche pas dans le hall.

## Essayer

Un relevé fictif est fourni dans [exemples/banque/releve_mimosas_2027.csv](../exemples/banque/releve_mimosas_2027.csv). Il couvre les cas difficiles : référence collée, prélèvement rejeté, chèque, virement sans référence, paiement partiel, impayés.

```bash
# Les appels doivent être validés : c'est ce qui est dû
curl -X POST "localhost:8000/copros/mimosas/appels/2027/T1?final=true" -H "X-API-Key: $MOTEUR_TOKEN"
curl -X POST "localhost:8000/copros/mimosas/appels/2027/T2?final=true" -H "X-API-Key: $MOTEUR_TOKEN"
# Le relevé
curl -X POST localhost:8000/copros/mimosas/banque/releves -H "X-API-Key: $MOTEUR_TOKEN" \
  -F "fichier=@exemples/banque/releve_mimosas_2027.csv"
# Où en est-on au 20 mai 2027 ?
curl "localhost:8000/copros/mimosas/impayes?au=2027-05-20" -H "X-API-Key: $MOTEUR_TOKEN"
```

> Aide à la gestion, pas un conseil juridique. Les taux de l'intérêt légal changent chaque semestre : ils sont à mettre à jour dans `core/impayes.py`. En cas de doute, l'ADIL de votre département renseigne gratuitement.
