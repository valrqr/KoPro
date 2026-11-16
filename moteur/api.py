"""
api.py — Le moteur du syndic, exposé en petite API HTTP.

n8n (ou n'importe quel autre orchestrateur) appelle ces routes au lieu de lancer
des scripts. Le moteur ne fait que préparer : il n'envoie jamais rien lui-même.

Routes :
    GET  /sante                                             le moteur répond-il ?
    GET  /copros                                            fiches disponibles
    GET  /copros/{copro}/fiche                              validation + alertes de l'échéancier
    POST /copros/{copro}/appels/{exercice}/T{n}?final=false génère avis + récap, renvoie la liste d'envoi
    GET  /copros/{copro}/appels/{exercice}/T{n}             relit la liste d'envoi déjà générée
    GET  /copros/{copro}/appels/{exercice}/T{n}/recap       PDF de la récap à valider
    GET  /copros/{copro}/appels/{exercice}/T{n}/avis/{id}   PDF d'un avis

Sécurité : chaque requête doit porter l'en-tête  X-API-Key: <MOTEUR_TOKEN>.
Lancement local : uvicorn moteur.api:app --reload   (depuis la racine du projet)
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import date
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Path as PathParam, Query, Security
from fastapi.responses import FileResponse
from fastapi.security import APIKeyHeader

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE / "core"))
from avis_appel import FicheInvalide, dossier_appel, generer_appel  # noqa: E402
from fiche_copro import alertes_echeancier, charger_fiche, valider  # noqa: E402

DOSSIER_CONFIG = Path(os.getenv("MOTEUR_CONFIG_DIR", RACINE / "config"))
DOSSIER_SORTIE = Path(os.getenv("MOTEUR_OUT_DIR", RACINE / "out"))
TOKEN = os.getenv("MOTEUR_TOKEN", "")

# Identifiants autorisés : lettres, chiffres, tirets. Empêche de sortir des dossiers (../).
SLUG = r"^[a-z0-9][a-z0-9-]{0,40}$"
ID_COPRO = r"^[A-Za-z0-9_-]{1,20}$"

app = FastAPI(title="Moteur syndic IA", version="0.1.0",
              description="Prépare les documents du syndic. Le syndic valide, n8n envoie.")


cle_api = APIKeyHeader(name="X-API-Key", auto_error=False,
                       description="La valeur de MOTEUR_TOKEN (fichier .env)")


def verifier_cle(x_api_key: str | None = Security(cle_api)) -> None:
    if not TOKEN:
        raise HTTPException(500, "MOTEUR_TOKEN n'est pas défini côté moteur")
    if x_api_key != TOKEN:
        raise HTTPException(401, "Clé d'API invalide ou absente (en-tête X-API-Key)")


def fiche_de(copro: str) -> dict:
    chemin = DOSSIER_CONFIG / f"{copro}.yaml"
    if not chemin.is_file():
        raise HTTPException(404, f"Aucune fiche pour la copro « {copro} »")
    return charger_fiche(chemin)


def verifier_exercice(fiche: dict, exercice: int) -> None:
    attendu = fiche["budget_previsionnel"]["exercice"]
    if exercice != attendu:
        raise HTTPException(409, f"La fiche porte sur l'exercice {attendu}, pas {exercice}")


Copro = PathParam(..., pattern=SLUG)
Trimestre = PathParam(..., ge=1, le=4)


# --------------------------------------------------------------------------- #
@app.get("/sante")
def sante() -> dict:
    return {"statut": "ok"}


@app.get("/copros", dependencies=[Depends(verifier_cle)])
def lister_copros() -> list[str]:
    return sorted(p.stem for p in DOSSIER_CONFIG.glob("*.yaml"))


@app.get("/copros/{copro}/fiche", dependencies=[Depends(verifier_cle)])
def controler_fiche(copro: str = Copro, au: date | None = Query(None, description="Date de référence")) -> dict:
    fiche = fiche_de(copro)
    rapport = valider(fiche)
    return {"copro": copro, "valide": rapport.ok, "erreurs": rapport.erreurs,
            "alertes": alertes_echeancier(fiche, au or date.today())}


@app.post("/copros/{copro}/appels/{exercice}/T{trimestre}", dependencies=[Depends(verifier_cle)])
def generer(copro: str = Copro, exercice: int = PathParam(...), trimestre: int = Trimestre,
            final: bool = Query(False, description="true = sans filigrane PROJET, après validation")) -> dict:
    fiche = fiche_de(copro)
    verifier_exercice(fiche, exercice)
    try:
        return generer_appel(fiche, copro, trimestre, DOSSIER_SORTIE, final)
    except FicheInvalide as e:
        raise HTTPException(422, {"message": "Fiche invalide, aucun avis généré", "erreurs": e.erreurs})


def dossier_existant(copro: str, exercice: int, trimestre: int) -> Path:
    d = dossier_appel(DOSSIER_SORTIE, copro, exercice, trimestre)
    if not (d / "envoi.json").is_file():
        raise HTTPException(404, "Appel pas encore généré : appelez d'abord la route POST")
    return d


@app.get("/copros/{copro}/appels/{exercice}/T{trimestre}", dependencies=[Depends(verifier_cle)])
def relire(copro: str = Copro, exercice: int = PathParam(...), trimestre: int = Trimestre) -> dict:
    d = dossier_existant(copro, exercice, trimestre)
    return json.loads((d / "envoi.json").read_text(encoding="utf-8"))


@app.get("/copros/{copro}/appels/{exercice}/T{trimestre}/recap", dependencies=[Depends(verifier_cle)])
def recap(copro: str = Copro, exercice: int = PathParam(...), trimestre: int = Trimestre) -> FileResponse:
    d = dossier_existant(copro, exercice, trimestre)
    return FileResponse(d / "recap_syndic.pdf", media_type="application/pdf", filename="recap_syndic.pdf")


@app.get("/copros/{copro}/appels/{exercice}/T{trimestre}/avis/{cid}", dependencies=[Depends(verifier_cle)])
def avis(copro: str = Copro, exercice: int = PathParam(...), trimestre: int = Trimestre,
         cid: str = PathParam(..., pattern=ID_COPRO)) -> FileResponse:
    d = dossier_existant(copro, exercice, trimestre)
    fichier = d / f"avis_{cid}.pdf"
    if not fichier.is_file():
        raise HTTPException(404, f"Pas d'avis pour {cid}")
    return FileResponse(fichier, media_type="application/pdf", filename=fichier.name)
