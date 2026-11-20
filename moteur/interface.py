"""
interface.py — L'interface web du syndic.

Des pages HTML servies par le moteur lui-même (Jinja2), rendues vivantes par htmx :
un clic sur « Confirmer » met à jour la ligne sans recharger la page. Pas de
framework JavaScript, pas de service externe : la police et htmx sont servis par
l'application.

Connexion : un mot de passe (INTERFACE_MOT_DE_PASSE, à défaut MOTEUR_TOKEN) ouvre une
session signée de 8 heures. Les PDF sont ceux des routes existantes de l'API, qui
acceptent la session du syndic à la place de la clé.

Les écrans de séance d'AG sur tablette et le module chantier relèvent du dépôt privé.
"""

from __future__ import annotations

import hmac
import os
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import api as A

ICI = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(ICI / "templates"))
routeur = APIRouter(prefix="/app", include_in_schema=False)
statiques = StaticFiles(directory=str(ICI / "static"))

MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
        "novembre", "décembre"]
JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]


# --------------------------------------------------------------------------- #
# Outils de présentation
# --------------------------------------------------------------------------- #
def euros(m) -> str:
    from avis_appel import euros as e
    return e(Decimal(str(m)))


def euros_rond(m) -> str:
    from avis_appel import euros_rond as e
    return e(Decimal(str(m)))


def date_fr(d, longue: bool = False) -> str:
    if not d:
        return ""
    d = date.fromisoformat(d) if isinstance(d, str) else d
    if longue:
        return f"{JOURS[d.weekday()]} {d.day}{'er' if d.day == 1 else ''} {MOIS[d.month - 1]} {d.year}"
    return f"{d.day}{'er' if d.day == 1 else ''} {MOIS[d.month - 1]} {d.year}"


templates.env.filters.update(euros=euros, euros_rond=euros_rond, date_fr=date_fr)


def aujourdhui(request: Request) -> date:
    """La date du jour, ou celle de la démonstration (?au=2027-05-20, gardée en session)."""
    au = request.query_params.get("au")
    if au:
        try:
            request.session["au"] = date.fromisoformat(au).isoformat()
        except ValueError:
            pass
    if request.query_params.get("au") == "":
        request.session.pop("au", None)
    return date.fromisoformat(request.session["au"]) if request.session.get("au") else date.today()


def connecte(request: Request) -> bool:
    return bool(request.session.get("syndic"))


def page(request: Request, gabarit: str, copro: str | None = None, **ctx) -> HTMLResponse:
    fiche = A.fiche_de(copro) if copro else None
    ctx.setdefault("onglet", gabarit.split(".")[0])
    return templates.TemplateResponse(request, gabarit, {
        "copro": copro, "fiche": fiche, "copros": A.lister_copros(), "jour": aujourdhui(request),
        "jour_demo": request.session.get("au"), "message": request.session.pop("message", None), **ctx})


def exiger(request: Request, copro: str | None = None) -> None:
    if not connecte(request):
        raise HTTPException(303, headers={"Location": "/app/connexion"})
    if copro is not None:
        if copro not in A.lister_copros():
            raise HTTPException(404, "Copropriété inconnue")


def retour(chemin: str, message: str | None = None, request: Request | None = None) -> RedirectResponse:
    if message and request is not None:
        request.session["message"] = message
    return RedirectResponse(chemin, status_code=303)


def mot_de_passe_attendu() -> str:
    return os.getenv("INTERFACE_MOT_DE_PASSE") or A.TOKEN


# --------------------------------------------------------------------------- #
# Connexion
# --------------------------------------------------------------------------- #
@routeur.get("/connexion", response_class=HTMLResponse)
def connexion_page(request: Request):
    return page(request, "connexion.html", erreur=None)


@routeur.post("/connexion", response_class=HTMLResponse)
def connexion(request: Request, mot_de_passe: str = Form(...)):
    attendu = mot_de_passe_attendu()
    if attendu and hmac.compare_digest(mot_de_passe.encode(), attendu.encode()):
        request.session.clear()
        request.session["syndic"] = True
        return retour("/app", None)
    return page(request, "connexion.html", erreur="Mot de passe incorrect.")


@routeur.post("/deconnexion")
def deconnexion(request: Request):
    request.session.clear()
    return retour("/app/connexion")


@routeur.get("", response_class=HTMLResponse)
def accueil(request: Request):
    exiger(request)
    copros = A.lister_copros()
    if len(copros) == 1:
        return retour(f"/app/{copros[0]}")
    return page(request, "choix.html", onglet="choix")


# --------------------------------------------------------------------------- #
# Tableau de bord : ce qui est à faire
# --------------------------------------------------------------------------- #
def _etat_impayes(fiche, copro, jour):
    if not A.banque.charger_operations(A.DOSSIER_DATA, copro):
        return None
    return A.impayes.etat(fiche, A.DOSSIER_SORTIE, A.DOSSIER_DATA, copro, jour)


def _appels(fiche, copro):
    import json
    ex = fiche["budget_previsionnel"]["exercice"]
    res = []
    for t, mmjj in enumerate(fiche["appels_de_fonds"]["dates_exigibilite"], 1):
        mm, jj = (int(x) for x in mmjj.split("-"))
        f = A.dossier_appel(A.DOSSIER_SORTIE, copro, ex, t) / "envoi.json"
        e = json.loads(f.read_text(encoding="utf-8")) if f.is_file() else None
        res.append({"trimestre": t, "exercice": ex, "date": date(ex, mm, jj), "statut": e["statut"] if e else None,
                    "total": e.get("total") if e else None, "nb": len(e["avis"]) if e else 0})
    return res


def _prochaine_ag(copro, jour):
    futures = [a for a in A.assemblee.lister_ag(A.DOSSIER_DATA, copro)
               if a["date"] >= jour.isoformat() and not a["depouillee"]]
    return futures[0] if futures else None


@routeur.get("/{copro}", response_class=HTMLResponse)
def tableau_de_bord(request: Request, copro: str):
    exiger(request, copro)
    jour = aujourdhui(request)
    fiche = A.fiche_de(copro)
    ech = A.rapport_echeancier(copro, fiche, jour, 120)
    imp = _etat_impayes(fiche, copro, jour)
    ag = _prochaine_ag(copro, jour)
    a_faire = []
    if imp:
        n = sum(1 for p in imp["relances"] if p["etat"] == "a_preparer")
        if imp["a_confirmer"]:
            n_c = len(imp["a_confirmer"])
            a_faire.append({"texte": f"{n_c} paiement{'s' if n_c > 1 else ''} à confirmer",
                            "detail": "Des virements reconnus par le nom ou le montant attendent votre accord.",
                            "lien": f"/app/{copro}/impayes#a-confirmer", "niveau": "urgent"})
        if n:
            a_faire.append({"texte": f"{n} relance{'s' if n > 1 else ''} à préparer", "detail": "Mitoyen a préparé la liste ; la lettre "
                            "se génère en un clic.", "lien": f"/app/{copro}/impayes#relances", "niveau": "urgent"})
        for p in imp["relances"]:
            if p["etat"] == "delai_expire":
                a_faire.append({"texte": f"Mise en demeure sans effet : {p['nom']}", "detail": p["message"],
                                "lien": f"/app/{copro}/impayes#relances", "niveau": "urgent"})
    if ag:
        cal = A.assemblee.calendrier(fiche, A.ag_de(copro, ag["id"]), jour)
        limite = date.fromisoformat(cal["limite_notification"])
        if 0 <= (limite - jour).days <= 14:
            a_faire.append({"texte": "Convocations de l'AG à notifier",
                            "detail": f"Au plus tard le {date_fr(limite)} pour l'AG du {date_fr(ag['date'])}, avec le "
                                      "formulaire de vote par correspondance.",
                            "lien": f"/app/{copro}/ag/{ag['id']}",
                            "niveau": "urgent" if (limite - jour).days <= 7 else "important"})
    for a in _appels(fiche, copro):
        if a["statut"] != "valide" and 0 <= (a["date"] - jour).days <= 45:
            a_faire.append({"texte": f"Appel de fonds du {a['trimestre']}{'er' if a['trimestre'] == 1 else 'e'} "
                                     f"trimestre à {'valider' if a['statut'] else 'préparer'}",
                            "detail": f"Exigible le {date_fr(a['date'])}.", "lien": f"/app/{copro}/appels",
                            "niveau": "urgent" if (a["date"] - jour).days <= 21 else "important"})
            break
    for al in ech["alertes"]:
        if al["type"] == "impayes":
            continue
        if al["niveau"] in ("urgent", "important") or len(a_faire) < 6:
            a_faire.append({"texte": al["titre"], "detail": al["message"],
                            "date": al.get("date"), "lien": None, "niveau": al["niveau"]})
    ordre = {"urgent": 0, "important": 1}
    a_faire.sort(key=lambda x: ordre.get(x["niveau"], 2))
    solde_ft = (fiche["fonds_travaux"].get("solde") or {}).get("montant")
    return page(request, "tableau.html", copro, a_faire=a_faire[:10], reste=max(0, len(a_faire) - 10), imp=imp,
                ag=ag, solde_ft=solde_ft, appels=_appels(fiche, copro))


# --------------------------------------------------------------------------- #
# Impayés
# --------------------------------------------------------------------------- #
@routeur.get("/{copro}/impayes", response_class=HTMLResponse)
def impayes_page(request: Request, copro: str):
    exiger(request, copro)
    jour = aujourdhui(request)
    fiche = A.fiche_de(copro)
    imp = _etat_impayes(fiche, copro, jour)
    regs = {r["id"]: r for r in A.impayes.charger_relances(A.DOSSIER_DATA, copro)}
    return page(request, "impayes.html", copro, imp=imp, relances_reg=regs,
                coproprietaires=sorted(fiche["coproprietaires"], key=lambda c: c["nom"]))


@routeur.post("/{copro}/impayes/releve")
async def impayes_releve(request: Request, copro: str, fichier: UploadFile = File(...)):
    exiger(request, copro)
    contenu = await fichier.read(A.banque.TAILLE_MAX + 1)
    try:
        r = A.banque.importer(A.DOSSIER_DATA, copro, contenu, fichier.filename or "releve.csv", aujourdhui(request))
    except A.banque.ReleveInvalide as e:
        return retour(f"/app/{copro}/impayes", f"Relevé refusé : {e}", request)
    msg = (f"Relevé importé : {r['nouvelles']} opération(s) nouvelle(s)"
           + (f", {r['deja_connues']} déjà connue(s)" if r["deja_connues"] else "")
           + (f", {len(r['ignorees'])} ligne(s) illisible(s)" if r["ignorees"] else "") + ".")
    return retour(f"/app/{copro}/impayes", msg, request)


@routeur.post("/{copro}/impayes/operations/{op_id}", response_class=HTMLResponse)
def impayes_affecter(request: Request, copro: str, op_id: str, statut: str = Form(...),
                     coproprietaire: str | None = Form(None)):
    exiger(request, copro)
    fiche = A.fiche_de(copro)
    try:
        A.impayes.affecter(fiche, A.DOSSIER_DATA, copro, op_id, statut, coproprietaire, aujourdhui(request))
    except A.impayes.RecouvrementInvalide as e:
        return HTMLResponse(f'<tr><td colspan="5" class="erreur">{e}</td></tr>')
    nom = next((c["nom"] for c in fiche["coproprietaires"] if c["id"] == coproprietaire), "")
    texte = f"Rattaché à {nom}." if statut == "confirme" else "Ignoré : sans rapport avec les charges."
    return templates.TemplateResponse(request, "_ligne_faite.html", {"texte": texte, "colonnes": 5,
                                                                     "copro": copro, "op_id": op_id})


@routeur.post("/{copro}/impayes/relances/{cid}", response_class=HTMLResponse)
def impayes_preparer(request: Request, copro: str, cid: str):
    exiger(request, copro)
    fiche = A.fiche_de(copro)
    jour = aujourdhui(request)
    try:
        A.impayes.preparer_relance(fiche, A.DOSSIER_SORTIE, A.DOSSIER_DATA, copro, cid,
                                   A.DOSSIER_SORTIE / copro / "recouvrement", jour)
    except A.impayes.RecouvrementInvalide as e:
        return HTMLResponse(f'<tr><td colspan="5" class="erreur">{e}</td></tr>')
    return _ligne_relance(request, copro, cid, jour)


@routeur.post("/{copro}/impayes/relances/{cid}/envoi", response_class=HTMLResponse)
def impayes_envoyee(request: Request, copro: str, cid: str, relance: str = Form(...), envoyee_le: date = Form(...),
                    moyen: str = Form("email"), presentee_le: str = Form("")):
    exiger(request, copro)
    fiche = A.fiche_de(copro)
    try:
        A.impayes.marquer(fiche, A.DOSSIER_DATA, copro, relance, "envoyee", envoyee_le, moyen,
                          date.fromisoformat(presentee_le) if presentee_le else None)
    except (A.impayes.RecouvrementInvalide, ValueError) as e:
        return HTMLResponse(f'<tr><td colspan="5" class="erreur">{e}</td></tr>')
    return _ligne_relance(request, copro, cid, aujourdhui(request))


def _ligne_relance(request, copro, cid, jour):
    fiche = A.fiche_de(copro)
    imp = A.impayes.etat(fiche, A.DOSSIER_SORTIE, A.DOSSIER_DATA, copro, jour)
    p = next((x for x in imp["relances"] if x["coproprietaire"] == cid), None)
    regs = {r["id"]: r for r in A.impayes.charger_relances(A.DOSSIER_DATA, copro)}
    return templates.TemplateResponse(request, "_ligne_relance.html",
                                      {"p": p, "copro": copro, "relances_reg": regs, "jour": jour})


# --------------------------------------------------------------------------- #
# Appels de fonds
# --------------------------------------------------------------------------- #
@routeur.get("/{copro}/appels", response_class=HTMLResponse)
def appels_page(request: Request, copro: str):
    exiger(request, copro)
    fiche = A.fiche_de(copro)
    return page(request, "appels.html", copro, appels=_appels(fiche, copro))


@routeur.post("/{copro}/appels/{trimestre}")
def appels_generer(request: Request, copro: str, trimestre: int, final: bool = Form(False)):
    exiger(request, copro)
    fiche = A.fiche_de(copro)
    try:
        r = A.generer_appel(fiche, copro, trimestre, A.DOSSIER_SORTIE, final)
    except A.FicheInvalide as e:
        return retour(f"/app/{copro}/appels", "Fiche invalide : " + " ; ".join(e.erreurs[:3]), request)
    msg = (f"{len(r['avis'])} avis {'validés et prêts à envoyer' if final else 'générés en version PROJET : relisez le récapitulatif'}"
           f" ({r['total']}).")
    return retour(f"/app/{copro}/appels", msg, request)


# --------------------------------------------------------------------------- #
# Assemblées générales
# --------------------------------------------------------------------------- #
@routeur.get("/{copro}/ag", response_class=HTMLResponse)
def ag_page(request: Request, copro: str):
    exiger(request, copro)
    jour = aujourdhui(request)
    ags = A.assemblee.lister_ag(A.DOSSIER_DATA, copro)
    return page(request, "ag.html", copro, ags=sorted(ags, key=lambda a: a["date"], reverse=True),
                odj=A.odj_type(copro)["resolutions"], date_conseillee=(jour + timedelta(days=45)).isoformat())


@routeur.post("/{copro}/ag")
def ag_creer(request: Request, copro: str, date_ag: date = Form(...), heure: str = Form(...), lieu: str = Form(...),
             titre: str = Form("Assemblée générale ordinaire"), visio: str = Form(""), odj_type: bool = Form(False),
             autres: str = Form("")):
    exiger(request, copro)
    resolutions = []
    if odj_type:
        resolutions += [A.ResolutionAG(**{k: r[k] for k in ("titre", "majorite") if k in r}, texte=r.get("texte", ""))
                        for r in A.odj_type(copro)["resolutions"]]
    resolutions += [A.ResolutionAG(titre=l.strip()[:200]) for l in autres.splitlines() if len(l.strip()) >= 3]
    try:
        corps = A.NouvelleAG(date=date_ag, heure=heure, lieu=lieu, titre=titre, visio=visio or None,
                             resolutions=resolutions)
        a = A.creer_ag(corps, copro)
    except HTTPException as e:
        return retour(f"/app/{copro}/ag", f"AG non créée : {e.detail}", request)
    except ValueError as e:
        return retour(f"/app/{copro}/ag", f"AG non créée : {e}", request)
    msg = "AG préparée." + (" " + " ".join(a["points_ajoutes"]) + "." if a.get("points_ajoutes") else "")
    return retour(f"/app/{copro}/ag/{a['id']}", msg, request)


@routeur.get("/{copro}/ag/{ag_id}", response_class=HTMLResponse)
def ag_detail(request: Request, copro: str, ag_id: str):
    exiger(request, copro)
    jour = aujourdhui(request)
    fiche = A.fiche_de(copro)
    a = A.ag_de(copro, ag_id)
    cal = A.assemblee.calendrier(fiche, a, jour)
    rap = A.rappel_ag.rappels(fiche, a, copro, jour)
    return page(request, "ag_detail.html", copro, onglet="ag", a=a, cal=cal, rappel=rap,
                coproprietaires=sorted(fiche["coproprietaires"], key=lambda c: c["nom"]))


# --------------------------------------------------------------------------- #
# Copropriétaires : le tableau d'interphone
# --------------------------------------------------------------------------- #
ETAGES = {-1: "Sous-sol", 0: "Rez-de-chaussée"}


@routeur.get("/{copro}/coproprietaires", response_class=HTMLResponse)
def coproprietaires_page(request: Request, copro: str):
    exiger(request, copro)
    jour = aujourdhui(request)
    fiche = A.fiche_de(copro)
    imp = _etat_impayes(fiche, copro, jour)
    syndic = fiche["gouvernance"]["syndic"]["coproprietaire_id"]
    conseil = set(fiche["gouvernance"].get("conseil_syndical", []))
    mt = {d["coproprietaire"] for d in A.meubles_tourisme.actives(A.meubles_tourisme.charger(A.DOSSIER_DATA, copro), jour)}
    cartes = []
    for c in fiche["coproprietaires"]:
        lots = sorted((l for l in fiche["lots"] if l["proprietaire"] == c["id"]), key=lambda l: l["numero"])
        hab = [l for l in lots if l["type"] not in ("cave", "parking")] or lots
        canal = A.papier.canal(fiche, c["id"], jour)["canal"]
        cartes.append({"c": c, "lots": lots, "etage": max((l.get("etage", 0) for l in hab), default=0),
                       "canal": canal, "role": "Syndic" if c["id"] == syndic else "Conseil syndical" if c["id"] in conseil else None,
                       "impaye": imp["comptes"][c["id"]]["impaye"] if imp else None, "meuble_tourisme": c["id"] in mt})
    etages = {}
    for k in sorted(cartes, key=lambda k: (-k["etage"], k["lots"][0]["numero"] if k["lots"] else 0)):
        nom = ETAGES.get(k["etage"], f"{k['etage']}{'er' if k['etage'] == 1 else 'e'} étage")
        etages.setdefault(nom, []).append(k)
    return page(request, "coproprietaires.html", copro, etages=etages)


# --------------------------------------------------------------------------- #
# Documents
# --------------------------------------------------------------------------- #
@routeur.get("/{copro}/documents", response_class=HTMLResponse)
def documents_page(request: Request, copro: str):
    exiger(request, copro)
    jour = aujourdhui(request)
    fiche = A.fiche_de(copro)
    reg = A.meubles_tourisme.charger(A.DOSSIER_DATA, copro)
    par_proprio = {}
    for l in fiche["lots"]:
        par_proprio.setdefault(l["proprietaire"], []).append(l)
    noms = {c["id"]: c["nom"] for c in fiche["coproprietaires"]}
    groupes = [{"nom": noms[cid], "lots": sorted(ls, key=lambda l: l["numero"])}
               for cid, ls in sorted(par_proprio.items(), key=lambda x: noms[x[0]])]
    logements = [l for l in fiche["lots"] if l["type"] not in ("cave", "parking")]
    return page(request, "documents.html", copro, groupes=groupes, logements=logements,
                telechargement=request.session.pop("telechargement", None),
                mt=reg, mt_actives=len(A.meubles_tourisme.actives(reg, jour)),
                eligibilite=A.meubles_tourisme.eligibilite(fiche))


@routeur.post("/{copro}/documents/vente")
def documents_vente(request: Request, copro: str, lots: list[int] = Form(...)):
    exiger(request, copro)
    try:
        r = A.vente.generer_dossier(A.fiche_de(copro), lots, A.DOSSIER_SORTIE / copro / "ventes", A.DOSSIER_DATA,
                                    copro, None, False, aujourdhui(request))
    except A.vente.VenteInvalide as e:
        return retour(f"/app/{copro}/documents", f"Dossier non créé : {e}", request)
    request.session["telechargement"] = f"/copros/{copro}/ventes/{r['id']}.zip"
    a_completer = len(r["a_completer"])
    return retour(f"/app/{copro}/documents#vente", "Dossier de vente prêt"
                  + (f", {a_completer} rubrique(s) à compléter d'après vos comptes." if a_completer else "."), request)


@routeur.post("/{copro}/documents/meubles-tourisme")
def documents_mt(request: Request, copro: str, lot: int = Form(...), recue_le: date = Form(...),
                 residence_principale: str = Form("")):
    exiger(request, copro)
    try:
        A.meubles_tourisme.enregistrer(A.fiche_de(copro), A.DOSSIER_DATA, copro, lot, recue_le, "proprietaire",
                                       {"oui": True, "non": False}.get(residence_principale))
    except A.meubles_tourisme.DeclarationInvalide as e:
        return retour(f"/app/{copro}/documents#meubles", f"Déclaration refusée : {e}", request)
    return retour(f"/app/{copro}/documents#meubles", "Déclaration enregistrée. Le point d'information sera ajouté "
                  "à la prochaine AG que vous préparerez.", request)
