# Le dossier de vente en un clic

[← Retour à l'accueil](../README.md)

Quand un copropriétaire vend son lot, l'acquéreur doit recevoir une série de documents, annexés à la promesse de vente (article L. 721-2 du code de la construction et de l'habitation). Tant qu'il ne les a pas, son délai de rétractation ne court pas. Chez un syndic professionnel, ces documents sont facturés, souvent plusieurs centaines d'euros. Dans une copropriété gérée avec Mitoyen, presque tout est déjà dans la fiche : un appel suffit.

## Ce que contient le dossier

| Fichier | Contenu | D'où viennent les données |
|---|---|---|
| `00_bordereau.pdf` | La liste des documents exigés par la loi, avec pour chacun : fourni, joint, à joindre, à compléter | Calculé |
| `01_fiche_synthetique.pdf` | Les six rubriques de la fiche synthétique (décret n° 2016-1822) : identification, syndic, organisation, caractéristiques techniques, équipements, finances | Fiche de la copro, section `comptes_approuves` |
| `02_pre_etat_date.pdf` | Pour les lots vendus : charges du budget et échéancier, charges payées par le vendeur, part du fonds de travaux, état global des impayés et des dettes, travaux votés, ce que le plan de travaux représentera année par année | Fiche, dossiers de travaux, AG, plan ; comptes du vendeur fournis par le syndic |
| `03_plan_travaux.pdf` | La projection du plan pluriannuel de travaux | [Plan de travaux](plan-de-travaux.md) |
| `documents/…` | Ce que le syndic a déposé dans `data/<copro>/documents/` : règlement, procès-verbaux signés, carnet d'entretien, DTG, notice d'information | Reconnus par leur nom : `reglement…`, `pv_…`, `carnet…`, `dtg…`, `notice…` |

La dernière rubrique du pré-état daté est la plus utile à l'acquéreur : il sait, avant de signer, combien le plan de travaux lui coûtera chaque année pour ce lot.

## Ce que Mitoyen ne devine pas

- **Les comptes du vendeur.** Les charges qu'il a réellement payées sur les deux derniers exercices et ses éventuels impayés viennent de la comptabilité du syndic. Sans eux, le dossier est produit quand même et ces rubriques sont marquées « à compléter ».
- **Les documents signés.** Les procès-verbaux d'AG, le règlement de copropriété : Mitoyen les joint s'ils sont déposés, sinon le bordereau les signale à joindre.
- **Une donnée absente de la fiche.** La fiche synthétique liste ce qui manque au lieu de l'inventer.

Si l'acquéreur est déjà copropriétaire, les documents d'organisation de l'immeuble ne sont pas à lui transmettre : le bordereau les marque « déjà détenu ».

## Exemple

```bash
curl -X POST localhost:8000/copros/mimosas/ventes -H "X-API-Key: $MOTEUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"lots": [2, 21], "impayes_vendeur": "0",
       "charges_payees": [{"exercice": 2024, "budget": "1210.40"}, {"exercice": 2025, "budget": "1262.80"}]}'
```

La réponse donne le bordereau, ce qui reste à compléter et le lien de l'archive ZIP. Un dossier par vendeur : plusieurs lots d'un même propriétaire (un appartement et son parking) vont dans le même dossier.

## La fiche synthétique, à part

La fiche synthétique doit aussi être tenue à jour chaque année, dans les deux mois qui suivent l'AG qui approuve les comptes, et mise à disposition de chaque copropriétaire. `GET /copros/{copro}/fiche-synthetique.pdf` la produit à tout moment ; il suffit de mettre à jour la section `comptes_approuves` de la fiche après l'AG.

> Aide à la préparation. L'état daté établi au moment de la vente reste à la charge du syndic, et le notaire vérifie le dossier. Mitoyen ne donne pas de conseil juridique : en cas de doute, l'ADIL de votre département répond gratuitement.
