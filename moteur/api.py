"""
api.py — Le moteur du syndic, exposé en petite API HTTP.

n8n (ou n'importe quel autre orchestrateur) appelle ces routes au lieu de lancer
des scripts. Le moteur ne fait que préparer : il n'envoie jamais rien lui-même.

Routes :
    GET  /sante                                             le moteur répond-il ?
    GET  /copros                                            fiches disponibles
    GET  /copros/{copro}/fiche                              validation + alertes de l'échéancier
    GET  /copros/{copro}/echeancier?au=&horizon=            échéances structurées d'une copro
    GET  /echeancier?au=&horizon=                           échéances de toutes les copros (workflow hebdo)
    POST /copros/{copro}/appels/{exercice}/T{n}?final=false génère avis + récap, renvoie la liste d'envoi
    GET  /copros/{copro}/appels/{exercice}/T{n}             relit la liste d'envoi déjà générée
    GET  /copros/{copro}/appels/{exercice}/T{n}/recap       PDF de la récap à valider
    GET  /copros/{copro}/appels/{exercice}/T{n}/avis/{id}   PDF d'un avis
    GET  /copros/{copro}/travaux                            dossiers de travaux
    POST /copros/{copro}/travaux                            créer un dossier (JSON)
    GET  /copros/{copro}/travaux/{id}                       dossier + comparatif + qui décide
    POST /copros/{copro}/travaux/{id}/devis                 ajouter un devis (formulaire + PDF)
    GET  /copros/{copro}/travaux/{id}/devis/{devis}/pdf     PDF d'un devis
    GET  /copros/{copro}/travaux/{id}/comparatif.pdf        comparatif à joindre ou diffuser

Sécurité : chaque requête doit porter l'en-tête  X-API-Key: <MOTEUR_TOKEN>.
Lancement local : uvicorn moteur.api:app --reload   (depuis la racine du projet)
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Path as PathParam, Query, Security, UploadFile
from fastapi.responses import FileResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE / "core"))
from avis_appel import FicheInvalide, dossier_appel, generer_appel  # noqa: E402
from fiche_copro import HORIZON_ALERTES_JOURS, alertes_echeancier, charger_fiche, echeances, valider  # noqa: E402
import travaux  # noqa: E402

DOSSIER_CONFIG = Path(os.getenv("MOTEUR_CONFIG_DIR", RACINE / "config"))
DOSSIER_SORTIE = Path(os.getenv("MOTEUR_OUT_DIR", RACINE / "out"))
DOSSIER_DATA = Path(os.getenv("MOTEUR_DATA_DIR", RACINE / "data"))   # dossiers de travaux, devis
TOKEN = os.getenv("MOTEUR_TOKEN", "")

# Identifiants autorisés : lettres, chiffres, tirets. Empêche de sortir des dossiers (../).
SLUG = r"^[a-z0-9][a-z0-9-]{0,40}$"
ID_COPRO = r"^[A-Za-z0-9_-]{1,20}$"
ID_DOSSIER = r"^\d{4}-\d{3}-[a-z0-9-]{1,40}$"
ID_DEVIS = r"^D\d{1,3}$"

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


def rapport_echeancier(copro: str, fiche: dict, au: date, horizon: int) -> dict:
    gouv = fiche["gouvernance"]
    sid = gouv["syndic"]["coproprietaire_id"]
    syndic = next((c for c in fiche["coproprietaires"] if c["id"] == sid), {})
    alertes = echeances(fiche, au, horizon)
    return {
        "copro": copro, "copropriete": fiche["copropriete"]["nom"],
        "au": au.isoformat(), "au_fr": f"{au:%d/%m/%Y}", "horizon_jours": horizon,
        "syndic": {"nom": syndic.get("nom"), "email": syndic.get("email")},
        "nb_alertes": len(alertes),
        "nb_urgentes": sum(1 for a in alertes if a["niveau"] == "urgent"),
        "alertes": alertes,
    }


Horizon = Query(HORIZON_ALERTES_JOURS, ge=1, le=730, description="Nombre de jours à anticiper")


@app.get("/copros/{copro}/echeancier", dependencies=[Depends(verifier_cle)])
def echeancier_copro(copro: str = Copro, au: date | None = Query(None), horizon: int = Horizon) -> dict:
    return rapport_echeancier(copro, fiche_de(copro), au or date.today(), horizon)


@app.get("/echeancier", dependencies=[Depends(verifier_cle)])
def echeancier_global(au: date | None = Query(None), horizon: int = Horizon) -> dict:
    """Une entrée par copro, pour le récapitulatif hebdomadaire envoyé à chaque syndic."""
    jour = au or date.today()
    rapports = [rapport_echeancier(c, fiche_de(c), jour, horizon) for c in lister_copros()]
    return {"au": jour.isoformat(), "nb_copros": len(rapports), "rapports": rapports}


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


# --------------------------------------------------------------------------- #
# Travaux et devis
# --------------------------------------------------------------------------- #
class NouveauDossier(BaseModel):
    titre: str = Field(..., min_length=3, max_length=120, examples=["Remplacement de la porte du garage"])
    description: str = Field("", max_length=4000)
    nature: str = Field("reparation", description="entretien | reparation | amelioration")
    urgence: bool = False
    cle: str = Field("generales", description="Clé de répartition de la fiche")


def _json(v):
    """Montants en texte (« 3058.00 ») : jamais de flottant pour de l'argent."""
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, dict):
        return {k: _json(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_json(x) for x in v]
    return v


Dossier = PathParam(..., pattern=ID_DOSSIER)


def dossier_de(copro: str, dossier_id: str) -> tuple[Path, dict]:
    chemin = travaux.dossier_dir(DOSSIER_DATA, copro, dossier_id)
    if not (chemin / "dossier.yaml").is_file():
        raise HTTPException(404, f"Aucun dossier de travaux « {dossier_id} »")
    return chemin, travaux.charger_dossier(chemin)


@app.get("/copros/{copro}/travaux", dependencies=[Depends(verifier_cle)])
def lister_travaux(copro: str = Copro) -> list[dict]:
    fiche_de(copro)
    return travaux.lister_dossiers(DOSSIER_DATA, copro)


@app.post("/copros/{copro}/travaux", status_code=201, dependencies=[Depends(verifier_cle)])
def creer_travaux(corps: NouveauDossier, copro: str = Copro) -> dict:
    try:
        return travaux.creer_dossier(DOSSIER_DATA, copro, fiche_de(copro), corps.titre, corps.description,
                                     corps.nature, corps.urgence, corps.cle)
    except travaux.DossierInvalide as e:
        raise HTTPException(422, str(e))


@app.get("/copros/{copro}/travaux/{dossier_id}", dependencies=[Depends(verifier_cle)])
def voir_travaux(copro: str = Copro, dossier_id: str = Dossier, au: date | None = Query(None)) -> dict:
    _, d = dossier_de(copro, dossier_id)
    return _json(travaux.synthese(fiche_de(copro), d, au))


@app.post("/copros/{copro}/travaux/{dossier_id}/devis", status_code=201, dependencies=[Depends(verifier_cle)])
async def ajouter_devis(copro: str = Copro, dossier_id: str = Dossier,
                        entreprise: str = Form(..., max_length=120),
                        montant_ht: str = Form(..., description="Montant hors taxes, ex. 2780 ou 2 780,50"),
                        taux_tva: str = Form("10", description="5.5, 10 ou 20"),
                        delai_jours: int | None = Form(None, ge=0, le=3650),
                        valide_jusqu_au: date | None = Form(None),
                        remarques: str = Form("", max_length=2000),
                        fichier: UploadFile | None = File(None, description="Le devis en PDF (10 Mo max)")) -> dict:
    fiche_de(copro)
    _, d = dossier_de(copro, dossier_id)
    pdf = None
    if fichier is not None and fichier.filename:
        pdf = await fichier.read(travaux.TAILLE_MAX_PDF + 1)
    try:
        devis = travaux.ajouter_devis(DOSSIER_DATA, copro, d, entreprise, montant_ht, taux_tva,
                                      delai_jours, valide_jusqu_au, remarques, pdf)
    except travaux.DossierInvalide as e:
        raise HTTPException(422, str(e))
    return _json(devis)


@app.get("/copros/{copro}/travaux/{dossier_id}/devis/{devis_id}/pdf", dependencies=[Depends(verifier_cle)])
def pdf_devis(copro: str = Copro, dossier_id: str = Dossier,
              devis_id: str = PathParam(..., pattern=ID_DEVIS)) -> FileResponse:
    chemin, d = dossier_de(copro, dossier_id)
    devis = next((x for x in d["devis"] if x["id"] == devis_id), None)
    if not devis or not devis.get("fichier"):
        raise HTTPException(404, f"Pas de PDF pour le devis {devis_id}")
    return FileResponse(chemin / devis["fichier"], media_type="application/pdf",
                        filename=f"{dossier_id}_{devis_id}.pdf")


@app.get("/copros/{copro}/travaux/{dossier_id}/comparatif.pdf", dependencies=[Depends(verifier_cle)])
def pdf_comparatif(copro: str = Copro, dossier_id: str = Dossier, au: date | None = Query(None)) -> FileResponse:
    chemin, d = dossier_de(copro, dossier_id)
    if not d["devis"]:
        raise HTTPException(409, "Ajoutez au moins un devis avant de générer le comparatif")
    fiche = fiche_de(copro)
    sortie = travaux.generer_pdf(fiche, travaux.synthese(fiche, d, au), chemin / "comparatif.pdf", au)
    return FileResponse(sortie, media_type="application/pdf", filename=f"comparatif_{dossier_id}.pdf")
