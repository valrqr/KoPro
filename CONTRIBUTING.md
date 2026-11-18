# Contribuer à KoPro

Merci de vouloir améliorer KoPro ! Ce document explique comment contribuer et à quelles conditions.

## Les contributions bienvenues sans condition

Tout le monde peut, sans rien signer :

- **signaler un bug** ou une erreur de calcul (ouvrir une *issue*, avec la fiche de démo si possible) ;
- **signaler une erreur juridique** : délai, majorité, obligation mal interprétée. Merci d'indiquer la source (article de loi, fiche ANIL ou ADIL, service-public.fr) ;
- **proposer une idée** ou partager votre expérience de syndic bénévole ou coopératif.

Ces retours sont précieux : KoPro s'adresse à des copropriétaires, pas à des juristes, et chaque erreur signalée protège quelqu'un.

## Les contributions de code : l'accord de contribution

KoPro est publié sous licence **AGPL-3.0** et le restera. Son auteur souhaite aussi pouvoir, plus tard, proposer KoPro sous d'autres conditions (par exemple une licence commerciale ou un service hébergé) pour financer le projet.

Pour que ce soit possible, toute contribution de code, de documentation ou de données (*pull request*) n'est acceptée qu'avec l'accord suivant. Vous l'acceptez en recopiant la phrase indiquée plus bas dans la description de votre *pull request*.

### Ce que vous accordez

1. **Vous restez propriétaire de votre contribution.** Vous ne cédez pas vos droits d'auteur : vous pouvez réutiliser votre code comme vous le souhaitez.
2. **Vous accordez au mainteneur du projet (Valentin Rouquier) une licence** non exclusive, mondiale, gratuite, irrévocable et pour toute la durée des droits d'auteur, pour utiliser, modifier, reproduire et distribuer votre contribution, et pour la sous-licencier, y compris sous des conditions autres que l'AGPL-3.0, commerciales comprises.
3. **Vous garantissez que vous avez le droit de la proposer** : c'est votre propre travail, ou vous disposez des autorisations nécessaires (par exemple celle de votre employeur si vous l'avez écrite dans le cadre de votre travail). Si elle reprend du code d'un tiers, vous indiquez sa provenance et sa licence.

### Ce à quoi le mainteneur s'engage

- **Toute contribution acceptée reste disponible sous licence AGPL-3.0**, dans le dépôt public, même si une autre licence est proposée en parallèle.
- **Les contributeurs sont crédités** dans l'historique du projet.

### La phrase à recopier

> J'ai lu le fichier CONTRIBUTING.md et j'accepte l'accord de contribution de KoPro pour cette contribution et les suivantes.

Sans cette phrase, la *pull request* ne pourra pas être intégrée. Elle reste bien sûr consultable, et l'idée qu'elle porte peut être reprise autrement.

> Cet accord sera remplacé par un accord de contribution formel (CLA), relu par un juriste, si le projet grandit. Les contributeurs déjà acceptés en seront informés.

## Avant d'envoyer du code

- **Les tests passent** : `python -m pytest tests/`.
- **Pas de données réelles.** Aucune vraie copropriété, adresse, personne, IBAN ou devis réel dans le dépôt : uniquement des données fictives, comme la résidence Les Mimosas.
- **Pas de secret.** Le fichier `.env` et les clés d'API ne doivent jamais être commités.
- **L'agent prépare, le syndic valide.** Aucune fonction ne doit envoyer, payer ou décider à la place du syndic sans validation humaine.
- **Pas de conseil juridique personnalisé.** Les règles de droit sont présentées comme indicatives, avec leur source quand c'est possible, et renvoient vers un professionnel ou l'ADIL.
- **De l'argent en `Decimal`, jamais en flottant**, réparti au centime près sans perte (voir `repartir` dans `core/fiche_copro.py`).
- **Le code, les messages et la documentation sont en français**, dans un langage compréhensible par un copropriétaire non spécialiste.

## Questions

Ouvrez une *issue* sur [github.com/valrqr/kopro](https://github.com/valrqr/kopro).
