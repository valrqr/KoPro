# Syndic IA

Un assistant open source pour les syndics **bénévoles et coopératifs** de copropriétés de moins de 50 lots.

**Principe : l'agent prépare, le syndic valide et signe.** L'outil assiste un syndic humain, il ne le remplace jamais.

> ⚠️ Projet en phase 1 (open source / portfolio). Toutes les données du dépôt sont **fictives**.

## Architecture

```
┌─────────────┐   HTTP + X-API-Key   ┌──────────────────────────┐
│    n8n      │ ───────────────────► │  moteur (Python/FastAPI) │
│ orchestrer, │ ◄─────────────────── │  valider, calculer,      │
│ mails,      │     JSON + PDF       │  générer les documents   │
│ validation  │                      │                          │
└─────────────┘                      └──────────────────────────┘
                                        ▲              │
                              config/*.yaml       out/<copro>/…
                              (une fiche par copro)  (avis, récaps)
```

| Dossier | Contenu |
|---|---|
| `config/` | Une fiche de configuration par copro. `mimosas.yaml` = copro démo fictive de 24 lots |
| `core/` | Le socle commun : validation de fiche, échéancier, appels de fonds, PDF |
| `moteur/` | L'API HTTP autour du socle, et son image Docker |
| `workflows/` | Les workflows n8n à importer |
| `tests/` | Tests du moteur |

## Démarrage rapide (Docker)

```bash
cp .env.example .env
# remplir MOTEUR_TOKEN et N8N_ENCRYPTION_KEY :
python -c "import secrets; print(secrets.token_urlsafe(32))"

mkdir -p out          # sous Linux : sudo chown 1000 out
docker compose up -d --build
```

- Documentation interactive du moteur : http://localhost:8000/docs (bouton *Authorize* → ta clé)
- n8n : http://localhost:5678

### Brancher le workflow dans n8n

1. **Import** : Workflows → *Import from File* → `workflows/appel_de_fonds.json`
2. **Identifiant « Moteur syndic »** : type *Header Auth*, nom `X-API-Key`, valeur = ton `MOTEUR_TOKEN`
3. **Identifiant « SMTP copro »** : ton serveur d'envoi de mails
4. **Nœud Config** : ton adresse dans `email_syndic`, et garde `mode_test = true` pour commencer

En mode test, **tous les mails arrivent chez le syndic**, préfixés `[TEST]`. Aucun copropriétaire ne reçoit rien.

## Sans Docker (développement)

```bash
pip install -r moteur/requirements.txt
python core/fiche_copro.py config/mimosas.yaml --date 2027-02-15     # contrôle + alertes
python core/avis_appel.py config/mimosas.yaml --trimestre 1          # avis en PROJET
MOTEUR_TOKEN=dev uvicorn moteur.api:app --reload                     # l'API
python tests/test_api.py                                             # les tests
```

## Routes du moteur

| Méthode | Route | Rôle |
|---|---|---|
| GET | `/sante` | Le moteur répond-il ? (sans clé) |
| GET | `/copros` | Fiches disponibles |
| GET | `/copros/{copro}/fiche?au=AAAA-MM-JJ` | Validation de la fiche + alertes de l'échéancier |
| POST | `/copros/{copro}/appels/{exercice}/T{n}?final=false` | Génère avis + récap, renvoie la liste d'envoi |
| GET | `/copros/{copro}/appels/{exercice}/T{n}` | Relit la liste d'envoi |
| GET | `/copros/{copro}/appels/{exercice}/T{n}/recap` | PDF de la récap à signer |
| GET | `/copros/{copro}/appels/{exercice}/T{n}/avis/{id}` | PDF d'un avis |

## Données personnelles

- Les fiches des vraies copros et le dossier `out/` sont exclus du dépôt (`.gitignore`).
- Le moteur n'est exposé que sur `127.0.0.1` et sur le réseau interne Docker.
- En production : hébergement en Europe, HTTPS, sauvegardes chiffrées.

## Comment ce projet est construit

KoPro est développé avec l'aide d'une IA (Claude, d'Anthropic), qui a écrit une grande partie du code.
Mon rôle : définir le besoin et le périmètre, choisir le cadre juridique et l'architecture,
tester, déployer et faire évoluer l'outil. Chaque brique est relue, testée et documentée avant publication.

## Licence

AGPL-3.0.
