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
    POST /copros/{copro}/travaux/{id}/resume                résumé IA des devis (brouillon)
    PUT  /copros/{copro}/travaux/{id}/resume                le syndic valide (et corrige) le résumé
    POST /copros/{copro}/travaux/{id}/verifications         vérifie les entreprises (registre, BODACC, décennale)
    POST /copros/{copro}/travaux/{id}/sondages              ouvre un sondage consultatif, renvoie les liens personnels
    GET  /copros/{copro}/travaux/{id}/sondages/{s}          résultats du sondage
    POST /copros/{copro}/travaux/{id}/sondages/{s}/cloture  clôture anticipée
    POST /copros/{copro}/travaux/{id}/ag                    dépouillement d'AG (une résolution par devis)
    POST /copros/{copro}/travaux/{id}/conseil               vote du conseil syndical (délégation)
    GET  /vote/{copro}/{id}/{s}/{jeton}                     page de vote d'un copropriétaire (sans clé d'API)

Sécurité : chaque requête doit porter l'en-tête  X-API-Key: <MOTEUR_TOKEN>.
Lancement local : uvicorn moteur.api:app --reload   (depuis la racine du projet)
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Path as PathParam, Query, Security, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE / "core"))
from avis_appel import FicheInvalide, dossier_appel, generer_appel  # noqa: E402
from fiche_copro import HORIZON_ALERTES_JOURS, alertes_echeancier, charger_fiche, echeances, valider  # noqa: E402
import resume_ia  # noqa: E402
import travaux  # noqa: E402
import vote  # noqa: E402

from . import page_vote  # noqa: E402

DOSSIER_CONFIG = Path(os.getenv("MOTEUR_CONFIG_DIR", RACINE / "config"))
DOSSIER_SORTIE = Path(os.getenv("MOTEUR_OUT_DIR", RACINE / "out"))
DOSSIER_DATA = Path(os.getenv("MOTEUR_DATA_DIR", RACINE / "data"))   # dossiers de travaux, devis
TOKEN = os.getenv("MOTEUR_TOKEN", "")
URL_PUBLIQUE = os.getenv("MOTEUR_URL_PUBLIQUE", "http://localhost:8000").rstrip("/")

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


class ValidationResume(BaseModel):
    texte: str | None = Field(None, max_length=1500, description="Texte corrigé par le syndic (facultatif)")


@app.post("/copros/{copro}/travaux/{dossier_id}/resume", status_code=201, dependencies=[Depends(verifier_cle)])
def generer_resume(copro: str = Copro, dossier_id: str = Dossier, au: date | None = Query(None)) -> dict:
    chemin, d = dossier_de(copro, dossier_id)
    fiche = fiche_de(copro)
    comp = travaux.comparatif(fiche, d, au)
    try:
        resume = resume_ia.generer(fiche, d, comp, chemin)
    except ValueError as e:
        raise HTTPException(409, str(e))
    except resume_ia.IAIndisponible as e:
        raise HTTPException(503, str(e))
    d["resume_ia"] = resume
    travaux.sauver_dossier(chemin, d)
    return resume_ia.etat(resume, d)


@app.put("/copros/{copro}/travaux/{dossier_id}/resume", dependencies=[Depends(verifier_cle)])
def valider_resume(corps: ValidationResume, copro: str = Copro, dossier_id: str = Dossier) -> dict:
    chemin, d = dossier_de(copro, dossier_id)
    actuel = resume_ia.etat(d.get("resume_ia"), d)
    if not actuel:
        raise HTTPException(404, "Aucun résumé à valider : générez-le d'abord")
    if actuel["obsolete"]:
        raise HTTPException(409, "Les devis ont changé depuis le résumé : générez-en un nouveau")
    try:
        d["resume_ia"] = resume_ia.valider(d["resume_ia"], corps.texte, comp=travaux.comparatif(fiche_de(copro), d))
    except ValueError as e:
        raise HTTPException(422, str(e))
    travaux.sauver_dossier(chemin, d)
    return resume_ia.etat(d["resume_ia"], d)


class DemandeVerification(BaseModel):
    siren: dict[str, str] = Field(default_factory=dict,
                                  description="SIREN saisis à la main quand le devis n'en porte pas, ex. {\"D2\": \"123456789\"}")


@app.post("/copros/{copro}/travaux/{dossier_id}/verifications", dependencies=[Depends(verifier_cle)])
def verifier_entreprises(corps: DemandeVerification | None = None, copro: str = Copro,
                         dossier_id: str = Dossier) -> list[dict]:
    fiche_de(copro)
    _, d = dossier_de(copro, dossier_id)
    if not d["devis"]:
        raise HTTPException(409, "Ajoutez au moins un devis avant de vérifier les entreprises")
    try:
        return travaux.verifier_entreprises(DOSSIER_DATA, copro, d, (corps.siren if corps else None))
    except travaux.DossierInvalide as e:
        raise HTTPException(422, str(e))


# --------------------------------------------------------------------------- #
# Votes
# --------------------------------------------------------------------------- #
class OuvertureSondage(BaseModel):
    options: list[str] | None = Field(None, description="Devis soumis à l'avis (défaut : tous les devis non expirés)")
    duree_jours: int = Field(7, ge=1, le=30, description="Durée du sondage")


ID_SONDAGE = r"^S\d{1,3}$"
JETON = r"^[A-Za-z0-9_-]{20,40}$"


def _sondage_de(d: dict, sid: str) -> dict:
    s = next((x for x in d.get("sondages", []) if x["id"] == sid), None)
    if not s:
        raise HTTPException(404, f"Aucun sondage {sid} dans ce dossier")
    return s


@app.post("/copros/{copro}/travaux/{dossier_id}/sondages", status_code=201, dependencies=[Depends(verifier_cle)])
def ouvrir_sondage(corps: OuvertureSondage, copro: str = Copro, dossier_id: str = Dossier) -> dict:
    chemin, d = dossier_de(copro, dossier_id)
    fiche = fiche_de(copro)
    options = corps.options or [x["id"] for x in travaux.comparatif(fiche, d)["devis"] if not x["expire"]]
    try:
        sondage, jetons = vote.ouvrir_sondage(fiche, d, options, corps.duree_jours)
    except ValueError as e:
        raise HTTPException(422, str(e))
    d.setdefault("sondages", []).append(sondage)
    travaux.sauver_dossier(chemin, d)
    copros = {c["id"]: c for c in fiche["coproprietaires"]}
    return {"sondage": sondage["id"], "clos_le": sondage["clos_le"], "options": options,
            "invitations": [{"coproprietaire": cid, "nom": copros[cid]["nom"], "email": copros[cid].get("email"),
                             "envoi": copros[cid]["envoi"],
                             "lien": f"{URL_PUBLIQUE}/vote/{copro}/{dossier_id}/{sondage['id']}/{j}"}
                            for cid, j in jetons.items()],
            "avertissement": "Les liens ne sont affichés qu'une fois : ils ne sont pas conservés en clair."}


@app.get("/copros/{copro}/travaux/{dossier_id}/sondages/{sid}", dependencies=[Depends(verifier_cle)])
def resultats_sondage(copro: str = Copro, dossier_id: str = Dossier, sid: str = PathParam(..., pattern=ID_SONDAGE)) -> dict:
    _, d = dossier_de(copro, dossier_id)
    return vote.resultats_sondage(_sondage_de(d, sid))


@app.post("/copros/{copro}/travaux/{dossier_id}/sondages/{sid}/cloture", dependencies=[Depends(verifier_cle)])
def cloturer_sondage(copro: str = Copro, dossier_id: str = Dossier, sid: str = PathParam(..., pattern=ID_SONDAGE)) -> dict:
    chemin, d = dossier_de(copro, dossier_id)
    s = _sondage_de(d, sid)
    s["cloture_anticipee"] = datetime.now().replace(microsecond=0).isoformat()
    travaux.sauver_dossier(chemin, d)
    return vote.resultats_sondage(s)


class Resolution(BaseModel):
    devis: str = Field(..., pattern=ID_DEVIS)
    bulletins: dict[str, str] = Field(..., description="{copropriétaire: pour | contre | abstention} ; absents omis")
    second_vote_24: dict[str, str] | None = Field(None, description="Second vote (art. 25-1), si l'AG y a procédé")


class Depouillement(BaseModel):
    majorite: str = Field("24", pattern=r"^(24|25|26)$")
    resolutions: list[Resolution] = Field(..., min_length=1, description="Dans l'ordre de l'ordre du jour")


@app.post("/copros/{copro}/travaux/{dossier_id}/ag", dependencies=[Depends(verifier_cle)])
def depouiller_ag(corps: Depouillement, copro: str = Copro, dossier_id: str = Dossier) -> dict:
    chemin, d = dossier_de(copro, dossier_id)
    voix = vote.electorat(fiche_de(copro), d["cle"])
    try:
        res = vote.depouiller_ag([r.model_dump() for r in corps.resolutions], voix, corps.majorite)
    except ValueError as e:
        raise HTTPException(422, str(e))
    d["ag"] = res
    travaux.sauver_dossier(chemin, d)
    return res


class VoteConseil(BaseModel):
    resolutions: list[Resolution] = Field(..., min_length=1)


@app.post("/copros/{copro}/travaux/{dossier_id}/conseil", dependencies=[Depends(verifier_cle)])
def voter_conseil(corps: VoteConseil, copro: str = Copro, dossier_id: str = Dossier) -> dict:
    chemin, d = dossier_de(copro, dossier_id)
    fiche = fiche_de(copro)
    membres = fiche["gouvernance"].get("conseil_syndical", [])
    aig = travaux.aiguillage(fiche, d, travaux.comparatif(fiche, d)["reference_ttc"])
    res, retenu = [], None
    try:
        for r in corps.resolutions:
            if retenu:
                res.append({"devis": r.devis, "statut": "sans_objet"})
                continue
            v = vote.vote_conseil(r.bulletins, membres)
            v["devis"], v["statut"] = r.devis, "adoptee" if v["adoptee"] else "rejetee"
            retenu = r.devis if v["adoptee"] else None
            res.append(v)
    except ValueError as e:
        raise HTTPException(422, str(e))
    sortie = {"devis_retenu": retenu, "resolutions": res,
              "avertissement": None if aig["circuit"] == "conseil_syndical" else
              "Attention : selon l'aiguillage, ce dossier ne relève pas d'une décision du conseil syndical "
              f"({aig['titre']}). Ce vote ne vaut qu'avis."}
    d["conseil"] = sortie
    travaux.sauver_dossier(chemin, d)
    return sortie


# --- Pages publiques : l'accès se fait par le jeton personnel, sans clé d'API -------------------- #
def _electeur(copro: str, dossier_id: str, sid: str, jeton: str):
    try:
        chemin, d = dossier_de(copro, dossier_id)
        s = _sondage_de(d, sid)
    except HTTPException:
        return None
    cid = vote.trouver_electeur(s, jeton)
    return (chemin, d, s, cid) if cid else None


def _page(copro, dossier_id, sid, jeton, message=None) -> HTMLResponse:
    trouve = _electeur(copro, dossier_id, sid, jeton)
    if not trouve:
        return HTMLResponse(page_vote.page_erreur("Ce lien de vote n'est pas valide. Vérifiez qu'il est complet, "
                                                  "ou demandez-en un nouveau au syndic."), status_code=404)
    _, d, s, cid = trouve
    fiche = fiche_de(copro)
    synth = travaux.synthese(fiche, d)
    nom = next(c["nom"] for c in fiche["coproprietaires"] if c["id"] == cid)
    html = page_vote.page_sondage(fiche["copropriete"]["nom"], d, s, cid, nom, synth["comparatif"],
                                  synth["resume_ia"], f"{URL_PUBLIQUE}/vote/{copro}/{dossier_id}/{sid}/{jeton}/comparatif.pdf",
                                  message, vote.est_ouvert(s))
    return HTMLResponse(html, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


Jeton = PathParam(..., pattern=JETON)
Sid = PathParam(..., pattern=ID_SONDAGE)


@app.get("/vote/{copro}/{dossier_id}/{sid}/{jeton}", response_class=HTMLResponse, include_in_schema=False)
def page_de_vote(copro: str = Copro, dossier_id: str = Dossier, sid: str = Sid, jeton: str = Jeton):
    return _page(copro, dossier_id, sid, jeton)


@app.post("/vote/{copro}/{dossier_id}/{sid}/{jeton}", response_class=HTMLResponse, include_in_schema=False)
def enregistrer_vote(copro: str = Copro, dossier_id: str = Dossier, sid: str = Sid, jeton: str = Jeton,
                     choix: str = Form(..., max_length=20)):
    trouve = _electeur(copro, dossier_id, sid, jeton)
    if not trouve:
        return _page(copro, dossier_id, sid, jeton)
    chemin, d, s, cid = trouve
    try:
        vote.voter_sondage(s, cid, choix)
    except ValueError as e:
        return HTMLResponse(page_vote.page_erreur(str(e)), status_code=409)
    travaux.sauver_dossier(chemin, d)
    return _page(copro, dossier_id, sid, jeton, "Merci, votre avis est enregistré.")


@app.get("/vote/{copro}/{dossier_id}/{sid}/{jeton}/comparatif.pdf", include_in_schema=False)
def comparatif_electeur(copro: str = Copro, dossier_id: str = Dossier, sid: str = Sid, jeton: str = Jeton):
    trouve = _electeur(copro, dossier_id, sid, jeton)
    if not trouve:
        raise HTTPException(404, "Lien invalide")
    chemin, d, _, _ = trouve
    fiche = fiche_de(copro)
    sortie = travaux.generer_pdf(fiche, travaux.synthese(fiche, d), chemin / "comparatif.pdf")
    return FileResponse(sortie, media_type="application/pdf", filename=f"comparatif_{dossier_id}.pdf")
