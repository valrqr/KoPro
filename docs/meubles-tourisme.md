# Les meublés de tourisme

[← Retour à l'accueil](../README.md)

Les locations de courte durée (Airbnb et autres) divisent souvent les copropriétés. La loi n° 2024-1039 du 19 novembre 2024, dite Le Meur, donne au syndic des obligations nouvelles et aux copropriétaires un moyen de voter une interdiction, sous conditions. Mitoyen applique les premières et prépare le second, **sans prendre parti** : il ne pousse ni à louer ni à interdire.

## Ce que la loi demande au syndic

| Obligation (loi du 10 juillet 1965, art. 9-2) | Ce que fait Mitoyen |
|---|---|
| Le copropriétaire qui déclare son lot en meublé de tourisme (ou son locataire autorisé) en informe le syndic | Registre des déclarations : lot, date, résidence principale ou non, numéro d'enregistrement en mairie, fin de la location |
| Le syndic inscrit un point d'information à l'ordre du jour de la plus prochaine AG | Le point est **ajouté automatiquement** à la prochaine AG créée, une seule fois, comme point **sans vote** |
| Le syndic affiche l'information dans les parties communes | Une affiche A4 en grands caractères, avec le nombre de logements déclarés et la date de la prochaine AG |

L'affiche ne donne ni nom ni numéro de lot : l'information porte sur l'activité dans l'immeuble, pas sur les personnes. Le point d'information de l'AG cite les numéros de lots, qui suffisent à informer l'assemblée.

## Une interdiction est-elle possible ?

Depuis la loi Le Meur, l'AG peut modifier le règlement pour interdire la location en meublé de tourisme des **lots d'habitation qui ne sont pas des résidences principales**. Le vote se fait à la double majorité de l'article 26 au lieu de l'unanimité, mais seulement si le règlement **interdit déjà toute activité commerciale dans les lots qui ne sont pas destinés au commerce**. Le Conseil constitutionnel a validé ce dispositif le 19 mars 2026.

`GET /copros/{copro}/meubles-tourisme` vérifie ce qui peut l'être à partir de la section `reglement` de la fiche :

| Condition | D'où vient la réponse |
|---|---|
| Le règlement interdit l'activité commerciale dans les lots non commerciaux | `reglement.activite_commerciale_interdite`, à recopier du règlement. Non renseigné : « à vérifier » |
| Le règlement n'autorise pas déjà expressément les meublés de tourisme | `reglement.meubles_tourisme`. S'il les autorise, revenir dessus demande l'unanimité |
| Seules les résidences secondaires et logements loués sont visés | Rappel : une résidence principale (occupée au moins 8 mois par an) reste hors du champ |

Si un copropriétaire demande l'inscription de la question, `GET /copros/{copro}/meubles-tourisme/resolution` fournit un projet de résolution : la clause à insérer et le mandat donné au syndic pour la faire établir par un notaire. Le projet est à faire relire avant l'envoi des convocations.

## La passerelle de l'article 26-1

Si la double majorité de l'article 26 n'est pas atteinte, mais que **la moitié des votants**, représentant **au moins un tiers des voix**, a voté pour, l'AG peut procéder immédiatement à un **second vote à la majorité de l'article 25**. Le dépouillement de Mitoyen le signale et accepte ce second vote (`second_vote_25`). C'est valable pour toutes les décisions de l'article 26, pas seulement celle-ci.

## Essayer

```bash
# Un propriétaire informe le syndic
curl -X POST localhost:8000/copros/mimosas/meubles-tourisme/declarations -H "X-API-Key: $MOTEUR_TOKEN" \
  -H "Content-Type: application/json" -d '{"lot": 9, "recue_le": "2027-07-01", "residence_principale": false}'
# L'affiche du hall
curl localhost:8000/copros/mimosas/meubles-tourisme/affichage.pdf -H "X-API-Key: $MOTEUR_TOKEN" -o affiche.pdf
# La prochaine AG créée reçoit le point d'information (voir « points_ajoutes » dans la réponse)
```

> Aide à la gestion, pas un conseil juridique. L'application de l'article 26 d) fait encore l'objet de débats (nature commerciale ou non de l'activité, rédaction du règlement) : en cas de doute, consulter un notaire, un avocat ou l'ADIL.
