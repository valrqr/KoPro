"""
travaux.py — Dossiers de travaux : devis, comparatif et aiguillage vers le bon circuit de décision.

Un dossier = un besoin (« remplacer la porte du garage ») + plusieurs devis.
Le module :
    1. range le dossier et ses devis (PDF) dans data/<copro>/travaux/<id>/
    2. compare les devis : TTC, écarts, délais, validité, quote-part de chaque copropriétaire
    3. indique QUI peut décider (syndic seul, conseil syndical ou AG), à partir des seuils de la fiche
    4. produit un comparatif PDF, à joindre à une convocation d'AG ou à envoyer aux copropriétaires

⚠️ L'aiguillage est indicatif : il aide le syndic, il ne remplace pas l'avis d'un juriste ou de l'ADIL.
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

import yaml

from fiche_copro import CENT, repartir

NATURES = {
    "entretien": "Entretien courant",
    "reparation": "Réparation",
    "amelioration": "Amélioration ou transformation",
}
TAUX_TVA = {Decimal("5.5"), Decimal("10"), Decimal("20")}
ECART_IMPORTANT = Decimal("0.5")      # 50 % d'écart entre devis : prestations sans doute différentes
ECART_PLAN_PCT = Decimal("20")        # devis > estimation du plan + 20 % : financement à prévoir
TAILLE_MAX_PDF = 10 * 1024 * 1024     # 10 Mo par devis
AVERTISSEMENT = ("Aiguillage indicatif, établi à partir des seuils de la fiche. "
                 "Il ne remplace pas l'avis d'un juriste ou de l'ADIL.")


class DossierInvalide(ValueError):
    pass


# --------------------------------------------------------------------------- #
# Stockage
# --------------------------------------------------------------------------- #
def _slug(texte: str, longueur: int = 40) -> str:
    t = unicodedata.normalize("NFKD", texte).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", t).strip("-")[:longueur].strip("-") or "dossier"


def dossier_dir(racine: Path, copro: str, dossier_id: str) -> Path:
    return Path(racine) / copro / "travaux" / dossier_id


def lister_dossiers(racine: Path, copro: str) -> list[dict]:
    base = Path(racine) / copro / "travaux"
    if not base.is_dir():
        return []
    res = []
    for f in sorted(base.glob("*/dossier.yaml")):
        d = charger_dossier(f.parent)
        res.append({"id": d["id"], "titre": d["titre"], "nature": d["nature"], "urgence": d["urgence"],
                    "cree_le": d["cree_le"], "nb_devis": len(d["devis"])})
    return res


def charger_dossier(chemin: Path) -> dict:
    with open(Path(chemin) / "dossier.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def sauver_dossier(chemin: Path, dossier: dict) -> None:
    chemin.mkdir(parents=True, exist_ok=True)
    with open(chemin / "dossier.yaml", "w", encoding="utf-8") as f:
        yaml.safe_dump(dossier, f, allow_unicode=True, sort_keys=False)


def creer_dossier(racine: Path, copro: str, fiche: dict, titre: str, description: str = "",
                  nature: str = "reparation", urgence: bool = False, cle: str = "generales",
                  aujourd_hui: date | None = None) -> dict:
    titre = (titre or "").strip()
    if not titre:
        raise DossierInvalide("Le titre du dossier est obligatoire")
    if nature not in NATURES:
        raise DossierInvalide(f"Nature inconnue : {nature} (attendu : {', '.join(NATURES)})")
    if cle not in fiche["cles_repartition"]:
        raise DossierInvalide(f"Clé de répartition inconnue : {cle}")
    jour = aujourd_hui or date.today()
    base = Path(racine) / copro / "travaux"
    numero = len(list(base.glob("*/dossier.yaml"))) + 1 if base.is_dir() else 1
    dossier_id = f"{jour.year}-{numero:03d}-{_slug(titre)}"
    dossier = {"id": dossier_id, "titre": titre, "description": description.strip(), "nature": nature,
               "urgence": bool(urgence), "cle": cle, "cree_le": jour.isoformat(), "devis": []}
    sauver_dossier(dossier_dir(racine, copro, dossier_id), dossier)
    return dossier


def ajouter_devis(racine: Path, copro: str, dossier: dict, entreprise: str, montant_ht: str | float,
                  taux_tva: str | float = "10", delai_jours: int | None = None,
                  valide_jusqu_au: date | None = None, remarques: str = "",
                  pdf: bytes | None = None, aujourd_hui: date | None = None) -> dict:
    entreprise = (entreprise or "").strip()
    if not entreprise:
        raise DossierInvalide("Le nom de l'entreprise est obligatoire")
    try:
        ht = Decimal(str(montant_ht).replace(",", ".").replace(" ", "")).quantize(CENT)
        tva = Decimal(str(taux_tva).replace(",", "."))
    except InvalidOperation:
        raise DossierInvalide("Montant ou taux de TVA illisible")
    if ht <= 0:
        raise DossierInvalide("Le montant HT doit être positif")
    if tva not in TAUX_TVA:
        raise DossierInvalide("Taux de TVA attendu : 5,5, 10 ou 20 %")
    if pdf is not None:
        if len(pdf) > TAILLE_MAX_PDF:
            raise DossierInvalide("PDF trop volumineux (10 Mo maximum)")
        if not pdf.startswith(b"%PDF"):
            raise DossierInvalide("Le fichier joint n'est pas un PDF")

    numero = max((int(d["id"][1:]) for d in dossier["devis"]), default=0) + 1
    devis_id = f"D{numero}"
    devis = {"id": devis_id, "entreprise": entreprise, "montant_ht": str(ht), "taux_tva": str(tva),
             "delai_jours": int(delai_jours) if delai_jours not in (None, "") else None,
             "valide_jusqu_au": valide_jusqu_au.isoformat() if valide_jusqu_au else None,
             "remarques": remarques.strip(), "fichier": None,
             "ajoute_le": (aujourd_hui or date.today()).isoformat()}
    chemin = dossier_dir(racine, copro, dossier["id"])
    if pdf is not None:
        nom = f"devis_{devis_id}.pdf"           # nom imposé : jamais celui envoyé par l'utilisateur
        (chemin / nom).write_bytes(pdf)
        devis["fichier"] = nom
    dossier["devis"].append(devis)
    sauver_dossier(chemin, dossier)
    return devis


# --------------------------------------------------------------------------- #
# Comparatif
# --------------------------------------------------------------------------- #
def montant_ttc(devis: dict) -> Decimal:
    ht = Decimal(devis["montant_ht"])
    return (ht * (1 + Decimal(devis["taux_tva"]) / 100)).quantize(CENT)


def comparatif(fiche: dict, dossier: dict, aujourd_hui: date | None = None) -> dict:
    jour = aujourd_hui or date.today()
    cle = dossier["cle"]
    lignes = []
    for d in dossier["devis"]:
        fin = date.fromisoformat(d["valide_jusqu_au"]) if d.get("valide_jusqu_au") else None
        lignes.append({**d, "montant_ttc": montant_ttc(d), "expire": bool(fin and fin < jour)})
    lignes.sort(key=lambda l: l["montant_ttc"])

    valides = [l for l in lignes if not l["expire"]]
    reference = valides[0]["montant_ttc"] if valides else (lignes[0]["montant_ttc"] if lignes else None)
    for rang, l in enumerate(lignes, 1):
        l["rang"] = rang
        l["moins_cher"] = bool(valides) and l is valides[0]
        if reference:
            ecart = l["montant_ttc"] - reference
            l["ecart_eur"] = ecart
            l["ecart_pct"] = (ecart / reference * 100).quantize(Decimal("0.1"))

    # Quote-part de chaque copropriétaire, pour chaque devis, selon la clé du dossier
    poids: dict[str, int] = defaultdict(int)
    for lot in fiche["lots"]:
        poids[lot["proprietaire"]] += lot["tantiemes"][cle]
    poids = {k: v for k, v in poids.items() if v > 0}
    noms = {c["id"]: c["nom"] for c in fiche["coproprietaires"]}
    parts_par_devis = {l["id"]: repartir(l["montant_ttc"], poids) for l in lignes}
    quotes_parts = [{"coproprietaire": cid, "nom": noms[cid], "tantiemes": poids[cid],
                     "parts": {did: parts[cid] for did, parts in parts_par_devis.items()}}
                    for cid in sorted(poids)]

    alertes = []
    seuil_mec = (fiche.get("seuils") or {}).get("mise_en_concurrence")
    if seuil_mec is not None and reference is not None and reference >= Decimal(str(seuil_mec)) and len(valides) < 2:
        alertes.append(f"Mise en concurrence obligatoire au-delà de {seuil_mec} € : il faut au moins 2 devis valides")
    for l in lignes:
        if l["expire"]:
            alertes.append(f"Devis {l['id']} ({l['entreprise']}) expiré le "
                           f"{date.fromisoformat(l['valide_jusqu_au']):%d/%m/%Y} : demander une mise à jour")
        if not l["fichier"]:
            alertes.append(f"Devis {l['id']} ({l['entreprise']}) : PDF du devis non joint")
    for l in lignes:
        for a in (l.get("verification") or {}).get("alertes", []):
            if a["niveau"] == "critique":
                alertes.append(f"{l['id']} ({l['entreprise']}) : {a['message']}")
    if len(valides) >= 2 and (valides[-1]["montant_ttc"] - reference) / reference > ECART_IMPORTANT:
        alertes.append("Écart de plus de 50 % entre les devis : vérifier qu'ils portent sur les mêmes prestations")

    # Dossier ouvert depuis le plan de travaux : comparer à l'estimation du plan
    plan = None
    if dossier.get("estimation_plan") and reference is not None:
        estimation = Decimal(dossier["estimation_plan"])
        ecart = ((reference - estimation) / estimation * 100).quantize(Decimal("0.1"))
        plan = {"ligne": dossier.get("plan_ligne"), "estimation": estimation, "ecart_pct": ecart}
        if ecart > ECART_PLAN_PCT:
            alertes.append(f"Le devis le moins cher dépasse de {str(ecart).replace('.', ',')} % l'estimation du plan "
                           f"de travaux ({estimation} €, ligne {dossier.get('plan_ligne')}) : prévoir le "
                           f"financement complémentaire")

    return {"plan": plan, "dossier": dossier["id"], "cle": cle, "cle_libelle": fiche["cles_repartition"][cle]["libelle"],
            "base_tantiemes": fiche["cles_repartition"][cle]["base"], "nb_devis": len(lignes),
            "reference_ttc": reference, "devis": lignes, "quotes_parts": quotes_parts, "alertes": alertes}


# --------------------------------------------------------------------------- #
# Aiguillage : qui peut décider ?
# --------------------------------------------------------------------------- #
def aiguillage(fiche: dict, dossier: dict, montant: Decimal | None) -> dict:
    seuils = fiche.get("seuils") or {}
    s_cs = seuils.get("consultation_conseil_syndical")
    deleg = seuils.get("delegation_conseil_syndical") or {}
    nature = dossier["nature"]
    notes: list[str] = []

    if dossier["urgence"]:
        res = {"circuit": "syndic_urgence", "titre": "Le syndic peut agir tout de suite",
               "explication": "En cas d'urgence pour la sauvegarde de l'immeuble, le syndic fait exécuter les travaux "
                              "nécessaires, puis convoque sans attendre une assemblée générale.",
               "etapes": ["Choisir le devis et faire intervenir l'entreprise",
                          "Informer les copropriétaires de l'urgence et des travaux engagés",
                          "Convoquer une assemblée générale pour ratifier les travaux et leur financement"],
               "majorite": None}
    elif montant is None:
        res = {"circuit": "a_determiner", "titre": "Ajoutez au moins un devis",
               "explication": "Le circuit de décision dépend du montant des travaux.", "etapes": [], "majorite": None}
    elif nature in ("entretien", "reparation") and s_cs is not None and montant < Decimal(str(s_cs)):
        res = {"circuit": "syndic", "titre": "Le syndic peut décider seul",
               "explication": f"Petite réparation d'entretien courant, sous le seuil de consultation du conseil "
                              f"syndical ({s_cs} €). Elle est financée par le budget prévisionnel.",
               "etapes": ["Choisir le devis", "Commander les travaux", "Archiver la facture dans les documents"],
               "majorite": None}
    elif (deleg.get("active") and deleg.get("plafond_par_depense")
          and montant <= Decimal(str(deleg["plafond_par_depense"])) and nature != "amelioration"):
        res = {"circuit": "conseil_syndical", "titre": "Le conseil syndical décide (délégation de pouvoirs)",
               "explication": f"Dépense inférieure au plafond de délégation voté en AG "
                              f"({deleg['plafond_par_depense']} €).",
               "etapes": ["Soumettre les devis au vote du conseil syndical",
                          "Consigner la décision", "En rendre compte à la prochaine AG"],
               "majorite": "Majorité des membres du conseil syndical"}
    else:
        majorite = ("Article 24 : majorité des voix exprimées (tantièmes) des présents, représentés "
                    "et votants par correspondance" if nature != "amelioration"
                    else "Article 24 ou 25 selon la nature des travaux : à vérifier")
        res = {"circuit": "assemblee_generale", "titre": "Décision en assemblée générale",
               "explication": "Montant ou nature des travaux hors du champ du syndic seul. "
                              "Une AG extraordinaire peut être convoquée si les travaux ne peuvent pas attendre.",
               "etapes": ["Inscrire les travaux à l'ordre du jour",
                          "Une résolution par devis, votées l'une après l'autre : la première adoptée l'emporte",
                          "Joindre les devis et ce comparatif à la convocation (21 jours avant l'AG)",
                          "Joindre le formulaire de vote par correspondance"],
               "majorite": majorite}

    if (res["circuit"] in ("syndic_urgence", "assemblee_generale") and s_cs is not None
            and montant is not None and montant >= Decimal(str(s_cs))):
        notes.append(f"Au-delà de {s_cs} €, le conseil syndical doit être consulté avant de choisir.")
    res["notes"] = notes
    res["avertissement"] = AVERTISSEMENT
    res["montant_reference"] = montant
    return res


def verifier_entreprises(racine: Path, copro: str, dossier: dict, sirens: dict[str, str] | None = None,
                         src=None, aujourd_hui: date | None = None) -> list[dict]:
    """Vérifie chaque entreprise (registre, BODACC, décennale) et range le résultat dans le dossier."""
    import entreprises
    import resume_ia
    chemin = dossier_dir(racine, copro, dossier["id"])
    sirens = sirens or {}
    fiches = []
    for d in dossier["devis"]:
        texte = resume_ia.extraire_texte(chemin / d["fichier"]) if d.get("fichier") else ""
        siren = sirens.get(d["id"])
        if siren is not None and not re.fullmatch(r"\d{9}", siren):
            raise DossierInvalide(f"SIREN saisi pour {d['id']} invalide : 9 chiffres attendus")
        d["verification"] = entreprises.verifier_devis(d, texte, src, siren, aujourd_hui)
        fiches.append(d["verification"])
    sauver_dossier(chemin, dossier)
    return fiches


def synthese(fiche: dict, dossier: dict, aujourd_hui: date | None = None) -> dict:
    import resume_ia
    comp = comparatif(fiche, dossier, aujourd_hui)
    d = {k: v for k, v in dossier.items() if k != "resume_ia"}
    return {"dossier": d, "comparatif": comp, "aiguillage": aiguillage(fiche, dossier, comp["reference_ttc"]),
            "resume_ia": resume_ia.etat(dossier.get("resume_ia"), dossier)}


def _echapper(t: str) -> str:
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# --------------------------------------------------------------------------- #
# PDF du comparatif
# --------------------------------------------------------------------------- #
def generer_pdf(fiche: dict, synth: dict, chemin: Path, aujourd_hui: date | None = None) -> Path:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    from avis_appel import ACCENT, FOND, S, essentiel, euros, euros_rond, tableau, tampon, CanevasNumerote

    jour = aujourd_hui or date.today()
    d, comp, aig = synth["dossier"], synth["comparatif"], synth["aiguillage"]
    ident = fiche["copropriete"]
    doc = SimpleDocTemplate(str(chemin), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=20 * mm, bottomMargin=18 * mm, title=f"Comparatif des devis – {d['titre']}")
    h = [Paragraph(f"Comparatif des devis", S["titre"]),
         Paragraph(f"{ident['nom']} · {d['titre']} · {NATURES[d['nature']]}"
                   f"{' · URGENT' if d['urgence'] else ''} · établi le {jour:%d/%m/%Y}", S["sous"]),
         Spacer(1, 4 * mm)]
    if d.get("description"):
        h += [Paragraph(d["description"], S["txt"]), Spacer(1, 4 * mm)]
    # Chiffres clés, volontairement neutres : aucun devis n'est mis en avant
    montants = [l["montant_ttc"] for l in comp["devis"] if not l["expire"]] or [l["montant_ttc"] for l in comp["devis"]]
    if montants:
        nb_exp = sum(1 for l in comp["devis"] if l["expire"])
        qui = {"assemblee_generale": "L'AG", "conseil_syndical": "Le conseil", "syndic": "Le syndic",
               "syndic_urgence": "Le syndic"}.get(aig["circuit"], "à définir")
        maj = (aig.get("majorite") or "").split(" : ")[0].replace("Article", "article")
        h += [essentiel([
            ("Devis reçus", str(len(comp["devis"])), f"dont {nb_exp} expiré{'s' if nb_exp > 1 else ''}" if nb_exp else "tous valides"),
            ("Fourchette TTC", f"{euros_rond(min(montants))} à {euros_rond(max(montants))}" if len(montants) > 1 else euros_rond(montants[0]),
             "devis en cours de validité" if nb_exp else "de tous les devis"),
            ("Qui décide", qui, maj or "voir ci-dessous")]), Spacer(1, 4 * mm)]

    r = synth.get("resume_ia")
    if r and r.get("statut") == "valide" and not r.get("obsolete"):
        texte = (f"<b>En bref</b><br/>{_echapper(r['resume'])}"
                 f"<br/><font size='7.5' color='#5C5C5C'>Résumé rédigé par une IA (Mistral) à partir des devis, "
                 f"relu et validé par le syndic le {date.fromisoformat(r['valide_le']):%d/%m/%Y}. "
                 f"Il décrit les différences sans recommander de devis : se référer aux devis joints.</font>")
        bref = Table([[Paragraph(texte, S["txt"])]], colWidths=[174 * mm])
        bref.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.8, ACCENT),
                                  ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                                  ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]))
        h += [bref, Spacer(1, 4 * mm)]

    lignes = [["Devis", "Entreprise", "HT", "TVA", "TTC", "Écart", "Délai"]]
    for l in comp["devis"]:
        ecart = "référence" if l.get("moins_cher") else (
            f"+{euros(l['ecart_eur'])} (+{str(l['ecart_pct']).replace('.', ',')} %)" if l.get("ecart_eur") else "—")
        if l["expire"]:
            ecart = "expiré"
        lignes.append([l["id"], Paragraph(l["entreprise"], S["txt"]), euros(Decimal(l["montant_ht"])),
                       f"{l['taux_tva'].replace('.', ',')} %", euros(l["montant_ttc"]), ecart,
                       f"{l['delai_jours']} j" if l.get("delai_jours") else "—"])
    h.append(tableau(lignes, [14 * mm, 44 * mm, 24 * mm, 14 * mm, 26 * mm, 34 * mm, 18 * mm],
                     aligner_droite_depuis=2, lignes_total=0))
    h.append(Spacer(1, 4 * mm))

    bloc = (f"<b>{aig['titre']}</b><br/>{aig['explication']}"
            + (f"<br/>Majorité : {aig['majorite']}" if aig.get("majorite") else "")
            + "".join(f"<br/>• {e}" for e in aig["etapes"])
            + "".join(f"<br/>{n}" for n in aig["notes"])
            + f"<br/><font size='7.5' color='#5C5C5C'>{aig['avertissement']}</font>")
    encadre = Table([[Paragraph(bloc, S["txt"])]], colWidths=[174 * mm])
    encadre.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), FOND),
                                 ("LINEBEFORE", (0, 0), (0, -1), 2.5, ACCENT),
                                 ("LEFTPADDING", (0, 0), (-1, -1), 10),
                                 ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    h += [encadre, Spacer(1, 4 * mm)]

    verifs = [l for l in comp["devis"] if l.get("verification")]
    if verifs:
        import entreprises
        sym = {"critique": "<b>Alerte :</b>", "attention": "À vérifier :", "info": "Info :"}
        lv = [["Devis", "Entreprise au registre et constats", "Alertes"]]
        for l in verifs:
            v = l["verification"]
            alertes_v = [a for a in v["alertes"] if a["niveau"] != "info"]
            lv.append([l["id"], Paragraph(_echapper(entreprises.resume_verifications(v)), S["txt"]),
                       Paragraph("<br/>".join(f"{sym[a['niveau']]} {_echapper(a['message'])}" for a in alertes_v) or "aucune",
                                 S["txt"])])
        h.append(Paragraph(f"Vérification des entreprises · registre des entreprises et BODACC, consultés le "
                           f"{date.fromisoformat(verifs[0]['verification']['verifie_le']):%d/%m/%Y}", S["sous"]))
        h.append(Spacer(1, 2 * mm))
        h.append(tableau(lv, [14 * mm, 80 * mm, 80 * mm], aligner_droite_depuis=3, lignes_total=0))
        h.append(Spacer(1, 4 * mm))

    if comp["alertes"]:
        h.append(Paragraph("<b>Points d'attention</b><br/>" + "<br/>".join(f"• {a}" for a in comp["alertes"]), S["txt"]))
        h.append(Spacer(1, 4 * mm))

    ids = [l["id"] for l in comp["devis"]]
    qp = [["Copropriétaire", "Tantièmes"] + ids]
    for q in comp["quotes_parts"]:
        qp.append([q["nom"], f"{q['tantiemes']} / {comp['base_tantiemes']}"] + [euros(q["parts"][i]) for i in ids])
    qp.append(["Total", ""] + [euros(sum(q["parts"][i] for q in comp["quotes_parts"])) for i in ids])
    largeur_devis = (174 - 50 - 26) / max(len(ids), 1)
    h.append(Paragraph(f"Quote-part de chaque copropriétaire · clé « {comp['cle_libelle']} »", S["sous"]))
    h.append(Spacer(1, 2 * mm))
    h.append(tableau(qp, [50 * mm, 26 * mm] + [largeur_devis * mm] * len(ids), aligner_droite_depuis=1, serre=True))

    pied = f"{ident['nom']} – comparatif préparé par l'assistant du syndic"
    doc.build(h, canvasmaker=CanevasNumerote, onFirstPage=tampon(False, pied), onLaterPages=tampon(False, pied))
    return chemin
