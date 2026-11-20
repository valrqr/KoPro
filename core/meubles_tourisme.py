"""
meubles_tourisme.py — Locations de type Airbnb : ce que la loi demande au syndic.

Depuis la loi n° 2024-1039 du 19 novembre 2024 (dite Le Meur) :

1. INFORMATION (loi 1965, art. 9-2). Le copropriétaire dont le lot fait l'objet d'une
   déclaration de meublé de tourisme (code du tourisme, art. L. 324-1-1), ou son
   locataire autorisé par son intermédiaire, en informe le syndic. Le syndic inscrit un
   point d'information à l'ordre du jour de la plus prochaine AG et affiche
   l'information dans les parties communes.
   → Mitoyen tient le registre des déclarations, ajoute le point d'information à l'AG
     suivante dès sa création et produit l'affichage du hall.

2. INTERDICTION (art. 26 d et al. 6). L'AG peut modifier le règlement pour interdire la
   location en meublé de tourisme des lots à usage d'habitation qui ne sont pas des
   résidences principales, à la double majorité de l'article 26, mais seulement si le
   règlement interdit déjà toute activité commerciale dans les lots qui ne sont pas
   spécifiquement à destination commerciale. Passerelle possible vers un second vote à
   la majorité de l'article 25 (art. 26-1).
   → Mitoyen vérifie ce qu'il peut vérifier, liste ce qui reste à faire vérifier, et
     fournit un projet de résolution si un copropriétaire en demande l'inscription.

Mitoyen reste NEUTRE : il n'encourage ni n'interdit la location touristique. Il prépare,
les copropriétaires décident. Les noms des loueurs n'apparaissent ni sur l'affichage ni
dans le point d'information : les numéros de lots suffisent à l'information de l'AG.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

LOI = "loi n° 2024-1039 du 19 novembre 2024"
TYPES_HABITATION = ("appartement", "maison", "studio", "habitation")
AVERTISSEMENT = ("Aide préparée par l'assistant du syndic, pas un conseil juridique. La rédaction d'une modification "
                 "du règlement de copropriété doit être relue par un notaire, qui la publiera.")


class DeclarationInvalide(ValueError):
    pass


def chemin_registre(racine: Path, copro: str) -> Path:
    return Path(racine) / copro / "meubles_tourisme.json"


def charger(racine: Path, copro: str) -> list[dict]:
    f = chemin_registre(racine, copro)
    return json.loads(f.read_text(encoding="utf-8")) if f.is_file() else []


def _sauver(racine: Path, copro: str, reg: list[dict]) -> None:
    f = chemin_registre(racine, copro)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(reg, ensure_ascii=False, indent=1), encoding="utf-8")


def enregistrer(fiche: dict, racine: Path, copro: str, lot: int, recue_le: date, informe_par: str = "proprietaire",
                residence_principale: bool | None = None, numero_enregistrement: str | None = None,
                debut: date | None = None) -> dict:
    """Inscrit au registre l'information reçue (art. 9-2)."""
    l = next((x for x in fiche["lots"] if x["numero"] == lot), None)
    if not l:
        raise DeclarationInvalide(f"Lot {lot} inconnu")
    if l["type"] not in TYPES_HABITATION:
        raise DeclarationInvalide(f"Le lot {lot} ({l['type']}) n'est pas un lot à usage d'habitation")
    if informe_par not in ("proprietaire", "locataire"):
        raise DeclarationInvalide("informe_par : proprietaire ou locataire (autorisé par le propriétaire)")
    reg = charger(racine, copro)
    if any(d["lot"] == lot and not d.get("fin_le") for d in reg):
        raise DeclarationInvalide(f"Une déclaration est déjà en cours pour le lot {lot}")
    d = {"id": f"MT{len(reg) + 1:03d}", "lot": lot, "coproprietaire": l["proprietaire"], "recue_le": recue_le.isoformat(),
         "informe_par": informe_par, "residence_principale": residence_principale,
         "numero_enregistrement": (numero_enregistrement or "").strip()[:40] or None,
         "debut": debut.isoformat() if debut else None, "fin_le": None, "inscrite_ag": None}
    _sauver(racine, copro, reg + [d])
    return d


def terminer(racine: Path, copro: str, did: str, fin_le: date) -> dict:
    reg = charger(racine, copro)
    d = next((x for x in reg if x["id"] == did), None)
    if not d:
        raise DeclarationInvalide("Déclaration inconnue")
    if fin_le < date.fromisoformat(d["recue_le"]):
        raise DeclarationInvalide("La fin ne peut pas précéder la déclaration")
    d["fin_le"] = fin_le.isoformat()
    _sauver(racine, copro, reg)
    return d


def actives(reg: list[dict], au: date) -> list[dict]:
    return [d for d in reg if d["recue_le"] <= au.isoformat() and (not d.get("fin_le") or d["fin_le"] > au.isoformat())]


# --------------------------------------------------------------------------- #
# 1. Le point d'information à l'AG et l'affichage
# --------------------------------------------------------------------------- #
def point_information(fiche: dict, reg: list[dict], date_ag: date) -> dict | None:
    """Le point à inscrire à l'AG : seulement si une information reçue n'a pas encore été présentée."""
    nouvelles = [d for d in reg if not d.get("inscrite_ag") and d["recue_le"] < date_ag.isoformat()]
    if not nouvelles:
        return None
    lots = {l["numero"]: l for l in fiche["lots"]}
    en_cours = actives(reg, date_ag)

    def decrire(d):
        l = lots[d["lot"]]
        return f"lot {d['lot']} ({l.get('desc') or l['type']}), information reçue le {date.fromisoformat(d['recue_le']):%d/%m/%Y}"

    texte = ("Conformément à l'article 9-2 de la loi du 10 juillet 1965, le syndic informe l'assemblée des lots "
             "déclarés en meublé de tourisme dont il a été informé depuis la précédente assemblée : "
             + " ; ".join(decrire(d) for d in nouvelles) + ". "
             + f"À la date de l'assemblée, {len(en_cours)} lot(s) de la copropriété sont concernés. "
             "Ce point ne donne lieu à aucun vote.")
    return {"titre": "Information sur la location de meublés de tourisme dans la copropriété (loi 1965, art. 9-2)",
            "texte": texte, "majorite": "info", "_declarations": [d["id"] for d in nouvelles]}


def marquer_inscrites(racine: Path, copro: str, ids: list[str], ag_id: str) -> None:
    reg = charger(racine, copro)
    for d in reg:
        if d["id"] in ids:
            d["inscrite_ag"] = ag_id
    _sauver(racine, copro, reg)


def generer_affichage(fiche: dict, reg: list[dict], chemin: Path, jour: date | None = None,
                      prochaine_ag: date | None = None) -> Path:
    """Affiche A4 pour les parties communes. Sans nom ni numéro de lot : l'information porte sur
    l'activité dans l'immeuble, pas sur les personnes."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    from avis_appel import CanevasNumerote, essentiel, styles, tampon
    S = styles(True)
    j = jour or date.today()
    n = len(actives(reg, j))
    c = fiche["copropriete"]
    h = [Paragraph("Location de meublés de tourisme dans l'immeuble", S["titre"]), Spacer(1, 6 * mm),
         essentiel([("Logements déclarés", str(n), "en meublé de tourisme"),
                    ("Prochaine AG", f"{prochaine_ag:%d/%m/%Y}" if prochaine_ag else "à fixer",
                     "point d'information à l'ordre du jour")], grand=True),
         Spacer(1, 8 * mm),
         Paragraph(f"La loi (loi du 10 juillet 1965, art. 9-2, issue de la {LOI}) prévoit que le syndic soit informé "
                   "des logements de l'immeuble déclarés en meublé de tourisme, qu'il en informe l'assemblée générale "
                   "et qu'il affiche cette information dans les parties communes.", S["txt"]),
         Spacer(1, 4 * mm),
         Paragraph("Propriétaires qui louent ou envisagent de louer en meublé de tourisme : merci d'en informer le "
                   "syndic. Voyageurs comme résidents, le règlement de copropriété s'applique à tous : tranquillité, "
                   "propreté des parties communes, sécurité des accès.", S["txt"]),
         Spacer(1, 6 * mm),
         Paragraph(f"Information affichée le {j:%d/%m/%Y} par le syndic de la {c['nom']}.", S["petit"])]
    chemin.parent.mkdir(parents=True, exist_ok=True)
    pied = f"{c['nom']} – information meublés de tourisme"
    SimpleDocTemplate(str(chemin), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=20 * mm,
                      bottomMargin=18 * mm, title="Meublés de tourisme").build(
        h, canvasmaker=CanevasNumerote, onFirstPage=tampon(False, pied), onLaterPages=tampon(False, pied))
    return chemin


# --------------------------------------------------------------------------- #
# 2. Une interdiction est-elle possible ? Projet de résolution
# --------------------------------------------------------------------------- #
def eligibilite(fiche: dict) -> dict:
    """Ce que Mitoyen peut vérifier à partir de la fiche, et ce qui reste à faire vérifier."""
    r = fiche.get("reglement") or {}
    commerciale = r.get("activite_commerciale_interdite")
    position = r.get("meubles_tourisme", "non_precise")
    lots_hab = [l for l in fiche["lots"] if l["type"] in TYPES_HABITATION]
    conditions = [
        {"condition": "Le règlement interdit toute activité commerciale dans les lots qui ne sont pas "
                      "spécifiquement à destination commerciale (art. 26, al. 6)",
         "statut": "ok" if commerciale is True else "non" if commerciale is False else "a_verifier",
         "detail": {True: "Indiqué dans la fiche (section reglement) : à confirmer sur le texte du règlement.",
                    False: "Le règlement n'interdit pas l'activité commerciale : le vote à l'article 26 n'est pas "
                           "ouvert ; une interdiction relèverait de l'unanimité.",
                    None: "Non renseigné : relire le règlement (clause de destination de l'immeuble)."}[commerciale]},
        {"condition": "Le règlement n'autorise pas déjà expressément la location en meublé de tourisme",
         "statut": "non" if position == "autorise" else "ok",
         "detail": {"autorise": "Le règlement l'autorise expressément : revenir dessus demande l'unanimité.",
                    "interdit": "Le règlement l'interdit déjà : aucune modification n'est nécessaire.",
                    "non_precise": "Le règlement ne se prononce pas."}.get(position, "")},
        {"condition": "Seuls les lots à usage d'habitation qui ne sont pas des résidences principales sont visés",
         "statut": "information",
         "detail": f"{len(lots_hab)} lot(s) à usage d'habitation. Une résidence principale (logement occupé au moins "
                   "8 mois par an) reste hors du champ de l'interdiction, même si elle est louée quelques jours."},
    ]
    if position == "interdit":
        verdict = "deja_interdit"
    elif any(c["statut"] == "non" for c in conditions):
        verdict = "non"
    elif any(c["statut"] == "a_verifier" for c in conditions):
        verdict = "a_verifier"
    else:
        verdict = "possible"
    return {"verdict": verdict, "conditions": conditions,
            "majorite": "Article 26 : majorité des membres du syndicat représentant au moins les deux tiers des voix",
            "passerelle": ("Si cette majorité n'est pas atteinte mais que la moitié des votants, représentant au "
                           "moins un tiers des voix, a voté pour, un second vote immédiat à la majorité de "
                           "l'article 25 est possible (art. 26-1)."),
            "suite": ("Une fois adoptée, la modification doit être rédigée par un notaire puis publiée ; elle peut "
                      "être contestée dans les deux mois suivant la notification du procès-verbal (art. 42)."),
            "avertissement": AVERTISSEMENT}


def projet_resolution(fiche: dict) -> dict:
    """Projet à soumettre à l'AG si un copropriétaire en demande l'inscription (décret 1967, art. 10)."""
    e = eligibilite(fiche)
    if e["verdict"] in ("non", "deja_interdit"):
        raise DeclarationInvalide("Le vote à la majorité de l'article 26 n'est pas ouvert : "
                                  + next(c["detail"] for c in e["conditions"] if c["statut"] == "non")
                                  if e["verdict"] == "non" else "Le règlement interdit déjà les meublés de tourisme.")
    clause = ("« La location des lots à usage d'habitation autres que ceux constituant une résidence principale, au "
              "sens de l'article 2 de la loi n° 89-462 du 6 juillet 1989, en meublés de tourisme au sens du I de "
              "l'article L. 324-1-1 du code du tourisme, est interdite. »")
    texte = ("L'assemblée générale, en application de l'article 26 d) de la loi du 10 juillet 1965, décide de modifier "
             f"le règlement de copropriété pour y insérer la clause suivante : {clause} "
             "Elle donne mandat au syndic pour faire établir par un notaire l'acte modificatif correspondant et le "
             "faire publier, les frais étant répartis selon les charges générales.")
    return {"titre": "Modification du règlement de copropriété : interdiction de la location en meublé de tourisme "
                     "des lots qui ne sont pas des résidences principales",
            "texte": texte, "majorite": "26", "cle": "generales",
            "a_verifier": [c["detail"] for c in e["conditions"] if c["statut"] == "a_verifier"],
            "note": "Projet à faire relire par un notaire avant l'envoi des convocations. " + e["passerelle"]}
