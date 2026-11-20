# Le pack nouvel arrivant

[← Retour à l'accueil](../README.md)

Un nouvel arrivant dans l'immeuble ne sait pas qui gère, ce qu'il paiera, ni comment les choses fonctionnent. Mitoyen prépare un pack de bienvenue, en deux versions, parce que la copropriété n'a pas le même lien avec un propriétaire et avec un locataire.

## Pour un nouveau propriétaire

Il devient membre du syndicat. Un seul PDF, en trois documents :

| Document | Contenu |
|---|---|
| **Guide de bienvenue** | Ses charges par an et sa prochaine échéance, la prochaine AG ; qui fait quoi (syndic, conseil syndical, assemblée générale) ; ses charges rubrique par rubrique et comment payer ; ce que le fonds et le plan de travaux représentent pour ses lots ; la vie de l'immeuble |
| **Fiche synthétique** | La même que dans le dossier de vente |
| **Bulletin de choix** | À rendre au syndic : mail ou courrier (et l'adresse), grands caractères, prélèvement automatique, proche aidant |

Le bulletin répond à une obligation récente : depuis le décret n° 2025-1292 du 22 décembre 2025, les notifications se font par voie électronique sauf demande de courrier, et chaque copropriétaire doit communiquer son adresse électronique s'il n'a pas choisi le courrier. Une fois le bulletin rendu, le syndic met à jour la fiche (`coproprietaires` et `lots`) ; Mitoyen gère ensuite tout seul le canal, les grands caractères, le mandat SEPA et la copie au proche aidant.

Le pack se prépare à partir des numéros de lots : l'acquéreur n'a pas besoin d'être déjà dans la fiche, et le nom de l'ancien propriétaire n'apparaît nulle part. C'est la suite naturelle du [dossier de vente](dossier-de-vente.md).

## Pour un locataire

Il n'a aucun lien juridique avec le syndicat : le pack est **remis par son bailleur**. Le bailleur doit d'ailleurs lui communiquer les extraits du règlement de copropriété sur la destination de l'immeuble et l'usage des parties privatives et communes, ainsi que la quote-part du lot loué dans chaque catégorie de charges (loi n° 89-462 du 6 juillet 1989, art. 3).

| Mitoyen fournit | Le bailleur ajoute |
|---|---|
| Qui contacter : le bailleur pour le logement, le syndic pour les parties communes | Les extraits du règlement de copropriété |
| La vie de l'immeuble | |
| **La quote-part du logement dans chaque catégorie de charges**, calculée à partir des tantièmes | |

Aucune donnée du locataire n'entre dans Mitoyen : ni nom, ni contact.

## La vie de l'immeuble

Une section `vie_immeuble` de la fiche réunit les informations pratiques : ordures, encombrants, tranquillité, vélos, jardin, urgences. Ce ne sont pas des règles : le règlement de copropriété fait foi. **N'y écrivez jamais un code d'accès** (digicode, boîte à clés) : le pack circule.

## Essayer

```bash
# Nouveau propriétaire des lots 2 et 21 (en grands caractères : ajouter &grand=true)
curl "localhost:8000/copros/mimosas/bienvenue/proprietaire.pdf?lots=2&lots=21&nom=Nouveau%20Propri%C3%A9taire" \
  -H "X-API-Key: $MOTEUR_TOKEN" -o bienvenue.pdf
# Locataire du lot 9
curl "localhost:8000/copros/mimosas/bienvenue/locataire.pdf?lots=9" -H "X-API-Key: $MOTEUR_TOKEN" -o locataire.pdf
```
