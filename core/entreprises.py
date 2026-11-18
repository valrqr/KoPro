"""
entreprises.py — Fiche de vérification des entreprises qui ont remis un devis.

Pour chaque devis, à partir de sources PUBLIQUES et FRANÇAISES, gratuites et sans clé :
    - le SIRET lu dans le devis (et sa validité : clé de Luhn)
    - le registre des entreprises (API Recherche d'entreprises, annuaire-entreprises.data.gouv.fr) :
      nom officiel, entreprise active ou fermée, date de création, activité, label RGE
    - le BODACC (annonces légales) : procédures collectives (sauvegarde, redressement, liquidation)
    - le texte du devis : mention, ou non, d'une assurance décennale

Règle d'or : on distingue toujours « rien trouvé » de « pas pu vérifier ».
Une source injoignable n'est jamais présentée comme un résultat rassurant.
"""

from __future__ import annotations

import json
import re
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import date

USER_AGENT = "KoPro/0.1 (assistant open source pour syndics benevoles; https://github.com/valrqr/kopro)"
URL_REGISTRE = "https://recherche-entreprises.api.gouv.fr/search"
URL_BODACC = ("https://bodacc-datadila.opendatasoft.com/api/explore/v2.1/catalog/datasets/"
              "annonces-commerciales/records")
ANNEES_PROCEDURES = 5          # procédures collectives prises en compte
ANNEES_JEUNE_ENTREPRISE = 2

SIRET = re.compile(r"(?<!\d)(\d{3})[  .]?(\d{3})[  .]?(\d{3})[  .]?(\d{5})(?!\d)")
SIREN_SEUL = re.compile(r"(?:SIREN|RCS|R\.C\.S\.?|RM)\D{0,30}?(\d{3})[  .]?(\d{3})[  .]?(\d{3})(?!\d)", re.I)
DECENNALE = re.compile(r"[^.\n]{0,80}d[ée]cennale[^.\n]{0,120}", re.I)


class SourceIndisponible(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# 1. Ce que dit le devis lui-même
# --------------------------------------------------------------------------- #
def luhn_valide(numero: str) -> bool:
    """Clé de contrôle des SIREN (9 chiffres) et SIRET (14 chiffres)."""
    if not numero.isdigit():
        return False
    total = 0
    for i, c in enumerate(reversed(numero)):
        n = int(c) * (2 if i % 2 else 1)
        total += n - 9 if n > 9 else n
    return total % 10 == 0


def lire_identifiant(texte: str) -> dict:
    """Cherche un SIRET (14 chiffres), à défaut un SIREN (9 chiffres) précédé de SIREN/RCS/RM."""
    m = SIRET.search(texte)
    if m:
        siret = "".join(m.groups())
        # Le SIREN ET le SIRET doivent passer la clé de contrôle (exception connue : La Poste, SIREN 356000000)
        valide = luhn_valide(siret[:9]) and (luhn_valide(siret) or siret.startswith("356000000"))
        return {"siret": siret, "siren": siret[:9], "valide": valide}
    m = SIREN_SEUL.search(texte)
    if m:
        siren = "".join(m.groups())
        return {"siret": None, "siren": siren, "valide": luhn_valide(siren)}
    return {"siret": None, "siren": None, "valide": False}


def lire_decennale(texte: str) -> dict:
    m = DECENNALE.search(texte)
    return {"mentionnee": bool(m), "extrait": re.sub(r"\s+", " ", m.group(0)).strip() if m else None}


# --------------------------------------------------------------------------- #
# 2. Sources publiques (remplaçables dans les tests)
# --------------------------------------------------------------------------- #
def _get_json(url: str, params: dict, delai: int = 10) -> dict:
    req = urllib.request.Request(f"{url}?{urllib.parse.urlencode(params)}",
                                 headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=delai) as r:
            return json.loads(r.read())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        raise SourceIndisponible(str(e))


class SourcesPubliques:
    def entreprise(self, siren: str) -> dict | None:
        """Unité légale du registre, ou None si le SIREN n'y figure pas."""
        donnees = _get_json(URL_REGISTRE, {"q": siren, "per_page": 5})
        return next((r for r in donnees.get("results", []) if r.get("siren") == siren), None)

    def procedures_collectives(self, siren: str) -> list[dict]:
        """Annonces BODACC de la famille « procédures collectives » qui concernent CE siren."""
        donnees = _get_json(URL_BODACC, {"where": f'familleavis="collective" and "{siren}"',
                                         "order_by": "dateparution desc", "limit": 20})
        espace = f"{siren[:3]} {siren[3:6]} {siren[6:]}"
        # Contrôle : la recherche plein texte peut ramener des homonymes, on ne garde que le bon SIREN
        return [r for r in donnees.get("results", [])
                if siren in json.dumps(r, ensure_ascii=False) or espace in json.dumps(r, ensure_ascii=False)]


def sources():
    """Point d'entrée unique : les tests le remplacent."""
    return SourcesPubliques()


# --------------------------------------------------------------------------- #
# 3. Fiche d'un devis
# --------------------------------------------------------------------------- #
def _normaliser(t: str) -> set[str]:
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode().lower()
    vides = {"sarl", "sas", "sasu", "eurl", "sa", "sci", "et", "de", "du", "des", "la", "le", "les", "l", "d",
             "exemple", "fictif", "societe", "entreprise", "ets"}
    return {m for m in re.findall(r"[a-z0-9]+", t) if len(m) > 1 and m not in vides}


def _jugement(annonce: dict) -> dict:
    j = annonce.get("jugement")
    if isinstance(j, str):
        try:
            j = json.loads(j)
        except json.JSONDecodeError:
            j = {"nature": j}
    j = j or {}
    return {"date": j.get("date") or annonce.get("dateparution"),
            "nature": j.get("nature") or j.get("famille") or annonce.get("familleavis_lib") or "Procédure collective",
            "tribunal": annonce.get("tribunal"), "parution": annonce.get("dateparution"),
            "lien": annonce.get("url_complete")}


def verifier_devis(devis: dict, texte: str, src=None, siren_force: str | None = None,
                   aujourd_hui: date | None = None) -> dict:
    jour = aujourd_hui or date.today()
    src = src or sources()
    alertes: list[dict] = []

    def alerte(niveau: str, message: str):
        alertes.append({"niveau": niveau, "message": message})

    ident = lire_identifiant(texte or "")
    if siren_force:
        ident = {"siret": None, "siren": siren_force, "valide": luhn_valide(siren_force), "saisi_par_syndic": True}
    decennale = lire_decennale(texte or "")
    fiche = {"devis": devis["id"], "entreprise_devis": devis["entreprise"], "verifie_le": jour.isoformat(),
             "siret_devis": ident.get("siret"), "siren": ident.get("siren"), "siren_valide": ident["valide"],
             "decennale": decennale, "registre": None, "procedures": [],
             "verifications": {"registre": "non_fait", "bodacc": "non_fait"}}

    if not texte:
        alerte("attention", "Texte du devis illisible : SIRET et assurance non vérifiés dans le document")
    if not decennale["mentionnee"] and texte:
        alerte("attention", "Le devis ne mentionne pas d'assurance décennale : demander l'attestation à l'entreprise")

    if not ident["siren"]:
        alerte("attention", "Aucun numéro SIRET trouvé dans le devis : le demander à l'entreprise")
        fiche["verifications"] = {"registre": "sans_siren", "bodacc": "sans_siren"}
        return {**fiche, "alertes": alertes}
    if not ident["valide"]:
        alerte("critique", f"Numéro SIRET/SIREN invalide ({ident['siren']}…) : erreur de saisie ou numéro fictif")
        fiche["verifications"] = {"registre": "siren_invalide", "bodacc": "siren_invalide"}
        return {**fiche, "alertes": alertes}

    siren = ident["siren"]
    # Registre des entreprises
    try:
        e = src.entreprise(siren)
        if e is None:
            fiche["verifications"]["registre"] = "non_trouve"
            alerte("critique", f"SIREN {siren} introuvable au registre des entreprises")
        else:
            fiche["verifications"]["registre"] = "ok"
            siege = e.get("siege") or {}
            comp = e.get("complements") or {}
            fiche["registre"] = {
                "nom": e.get("nom_complet"), "etat": "active" if e.get("etat_administratif") == "A" else "fermée",
                "date_creation": e.get("date_creation"), "date_fermeture": e.get("date_fermeture"),
                "activite": e.get("activite_principale"), "adresse": siege.get("adresse"),
                "rge": bool(comp.get("est_rge")), "entrepreneur_individuel": bool(comp.get("est_entrepreneur_individuel")),
                "lien": f"https://annuaire-entreprises.data.gouv.fr/entreprise/{siren}"}
            r = fiche["registre"]
            if r["etat"] == "fermée":
                alerte("critique", f"Entreprise fermée au registre{' depuis le ' + r['date_fermeture'] if r['date_fermeture'] else ''}")
            if r["date_creation"]:
                creation = date.fromisoformat(r["date_creation"][:10])
                if (jour - creation).days < 365 * ANNEES_JEUNE_ENTREPRISE:
                    alerte("info", f"Entreprise créée le {creation:%d/%m/%Y}, il y a moins de {ANNEES_JEUNE_ENTREPRISE} ans")
            if r["nom"] and not (_normaliser(r["nom"]) & _normaliser(devis["entreprise"])):
                alerte("attention", f"Le SIRET du devis correspond à « {r['nom']} », et non à « {devis['entreprise']} »")
    except SourceIndisponible:
        fiche["verifications"]["registre"] = "indisponible"
        alerte("attention", "Registre des entreprises injoignable : vérification à refaire")

    # BODACC
    try:
        limite = date(jour.year - ANNEES_PROCEDURES, jour.month, min(jour.day, 28))
        for a in src.procedures_collectives(siren):
            j = _jugement(a)
            if j["date"] and date.fromisoformat(str(j["date"])[:10]) >= limite:
                fiche["procedures"].append(j)
        fiche["verifications"]["bodacc"] = "ok"
        for p in fiche["procedures"]:
            alerte("critique", f"Procédure collective publiée au BODACC : {p['nature']} "
                               f"({date.fromisoformat(str(p['date'])[:10]):%d/%m/%Y})")
    except SourceIndisponible:
        fiche["verifications"]["bodacc"] = "indisponible"
        alerte("attention", "BODACC injoignable : procédures collectives non vérifiées")

    return {**fiche, "alertes": alertes}


def resume_verifications(fiche: dict) -> str:
    """Une ligne factuelle, réutilisée dans le PDF et transmise à l'IA."""
    v = fiche["verifications"]
    morceaux = []
    if fiche.get("registre"):
        r = fiche["registre"]
        morceaux.append(f"{r['nom']}, entreprise {r['etat']}, créée le "
                        f"{date.fromisoformat(r['date_creation'][:10]):%d/%m/%Y}" if r.get("date_creation") else r["nom"])
        morceaux.append("label RGE" if r["rge"] else "sans label RGE")
    elif v["registre"] == "siren_invalide":
        morceaux.append("SIRET invalide")
    elif v["registre"] == "sans_siren":
        morceaux.append("pas de SIRET sur le devis")
    elif v["registre"] == "non_trouve":
        morceaux.append("absente du registre")
    elif v["registre"] == "indisponible":
        morceaux.append("registre non consulté")
    if v["bodacc"] == "ok":
        morceaux.append(f"{len(fiche['procedures'])} procédure(s) collective(s) en {ANNEES_PROCEDURES} ans"
                        if fiche["procedures"] else f"aucune procédure collective publiée en {ANNEES_PROCEDURES} ans")
    elif v["bodacc"] == "indisponible":
        morceaux.append("BODACC non consulté")
    morceaux.append("assurance décennale mentionnée" if fiche["decennale"]["mentionnee"]
                    else "assurance décennale non mentionnée")
    return " ; ".join(morceaux)
