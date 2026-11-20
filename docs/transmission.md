# Changer de syndic : le dossier de transfert

Une copropriété gérée avec Mitoyen n'en est jamais prisonnière. Si l'assemblée générale désigne un autre syndic, professionnel ou bénévole, le syndic sortant prépare en un clic un dossier de transfert complet, dans des formats ouverts : aucun logiciel particulier n'est nécessaire pour reprendre la gestion.

Interface : **Documents > Changer de syndic** (`/app/<copro>/transmission`). API : `POST /copros/<copro>/transmission`.

## Ce que dit la loi

L'article 18-2 de la loi du 10 juillet 1965 impose à l'ancien syndic de remettre au nouveau, à compter de la fin de ses fonctions :

| Délai | Ce qui est remis |
|---|---|
| 15 jours | La situation de trésorerie, les références des comptes bancaires et les coordonnées de la banque |
| 1 mois | L'ensemble des documents et archives du syndicat, y compris dématérialisés |
| 2 mois de plus | Le solde des fonds après apurement, l'état des comptes des copropriétaires et celui des comptes du syndicat |

## Ce que contient l'archive

| Fichier | Contenu |
|---|---|
| `00_bordereau.pdf` | Trésorerie (soldes saisis d'après le relevé), comptes bancaires, comptes des copropriétaires, contrats et travaux en cours, contenu de l'archive, et un cadre de signature pour les deux syndics |
| `01_etat_des_comptes.pdf` | Le solde de chaque copropriétaire et ses échéances impayées |
| `donnees/*.csv` | Copropriétaires, lots et tantièmes, comptes, échéances, opérations bancaires et leur rapprochement, relances, contrats, diagnostics, budget. Séparateur « ; », virgule décimale, UTF-8 : s'ouvrent dans n'importe quel tableur |
| `donnees/fiche_copropriete.yaml` | Toute la fiche de la copropriété, en texte |
| `archives/data/` | Tout ce que Mitoyen conserve : AG, devis, relevés bancaires, règlement… |
| `archives/documents_produits/` | Tous les documents produits : avis d'appel de fonds, PV, courriers… |
| `LISEZMOI.txt` | Le mode d'emploi de l'archive |

Le syndic sortant indique le nom du successeur, la date de fin de ses fonctions et, s'il les a, les soldes du compte courant et du compte du fonds de travaux d'après le relevé bancaire. Mitoyen ne tient pas la comptabilité complète du syndicat : sans ces soldes, le bordereau les laisse « à compléter ».

Les dossiers préparés restent téléchargeables depuis la même page (`out/<copro>/transmission/<date>/`).

## Précautions

- L'archive contient des données personnelles (noms, coordonnées, dettes) : elle ne se transmet qu'au nouveau syndic, et les copies sont à supprimer une fois la transmission terminée.
- Les liens secrets éventuellement envoyés aux copropriétaires (page de vote, suivi de chantier) restent dans les archives : le nouveau syndic les désactive s'il ne reprend pas Mitoyen.
- L'outil inverse, pour une copropriété qui arrive d'un syndic professionnel, est dans le module privé « kit de bascule ».

Pas un conseil juridique : en cas de doute, l'ADIL de votre département vous répond gratuitement.
