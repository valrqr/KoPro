"""
papier.py — Le pack papier : Mitoyen pour ceux qui n'utilisent pas (ou peu) le numérique.

Principe : le papier n'est pas un mode dégradé. Un copropriétaire « papier » reçoit la même
information, au même moment, avec les mêmes droits. C'est le syndic, aidé de Mitoyen, qui
absorbe la complexité.

    1. CANAL TRACÉ : courrier ou mail, avec la trace de la demande de papier et sa date de prise
       d'effet ; rappel du droit au papier sur les documents (loi 1965, art. 42-1)
    2. LISIBILITÉ : grands caractères sur demande, encadré « Ce que vous devez faire », lexique
    3. PAYER SANS INFORMATIQUE : talon à joindre au chèque, mandat de prélèvement SEPA papier
    4. VOTER SUR PAPIER : bulletin de sondage pré-rempli, saisi ensuite par le syndic
    5. AFFICHE DU HALL : la vie de l'immeuble, sans aucune donnée personnelle
    6. REGISTRE DES ENVOIS : recommandés, dates de notification, contrôle des délais d'AG
    7. PROCHE AIDANT : copie des documents, avec l'accord écrit du copropriétaire, sans droit de vote
    8. LOT POSTAL : un PDF prêt à imprimer (ou à confier à un service d'envoi postal) et son manifeste

Règles utilisées (à vérifier auprès de l'ADIL avant usage réel) :
    - depuis le décret du 22 décembre 2025, la notification électronique est le principe ; un
      copropriétaire peut demander le courrier à tout moment, par tout moyen donnant date
      certaine ; la demande prend effet le lendemain du 8e jour suivant sa réception (le jour
      même si elle est faite en AG) ; le syndic rappelle ce droit sur chaque appel de fonds et
      chaque convocation.
"""

from __future__ import annotations

import csv
import json
import secrets
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

MOYENS_DEMANDE = ("lettre_recommandee", "main_propre", "email_accuse", "extranet", "ag")
DELAI_EFFET_JOURS = 9                   # lendemain du 8e jour suivant la réception
CANAUX_ENVOI = ("lrar", "lre", "main_propre", "courrier_simple", "email")
CANAUX_NOTIFICATION = ("lrar", "lre", "main_propre")   # valent notification (date certaine)
MENTION_PAPIER = ("Vous pouvez demander à tout moment à recevoir les notifications et mises en demeure du "
                  "syndic par courrier postal. Il suffit de le lui signaler par un moyen qui permet d'en dater "
                  "la réception : lettre recommandée, remise en main propre contre signature, mail avec "
                  "accusé de lecture, ou déclaration en assemblée générale.")
LEXIQUE = [
    ("Tantièmes", "La part de l'immeuble qui correspond à votre lot. Ils servent à calculer ce que vous payez "
                  "et le poids de votre voix en assemblée générale."),
    ("Quote-part", "Votre part d'une dépense commune, calculée avec vos tantièmes."),
    ("Charges générales", "Les dépenses de tout l'immeuble : assurance, ménage, électricité des parties communes…"),
    ("Charges spéciales", "Les dépenses d'un équipement qui ne sert pas à tous, comme l'ascenseur : seuls les "
                          "lots concernés les paient."),
    ("Budget prévisionnel", "L'estimation des dépenses de l'année, votée en assemblée générale et appelée en "
                            "plusieurs fois."),
    ("Fonds de travaux", "Une épargne obligatoire de la copropriété pour les futurs travaux. Elle reste attachée "
                         "à votre lot si vous vendez."),
    ("Appel de fonds", "La demande de paiement de votre part du budget, envoyée avant chaque échéance."),
    ("Syndic", "La personne qui gère l'immeuble au nom de tous. Ici, un copropriétaire élu (syndic bénévole "
               "ou coopératif)."),
    ("Assemblée générale (AG)", "La réunion annuelle où les copropriétaires décident ensemble. Vous pouvez y "
                                "venir, donner pouvoir à quelqu'un ou voter par correspondance."),
]


class PapierInvalide(ValueError):
    pass


# --------------------------------------------------------------------------- #
# 1. Canal tracé
# --------------------------------------------------------------------------- #
def _copro(fiche: dict, cid: str) -> dict:
    c = next((c for c in fiche["coproprietaires"] if c["id"] == cid), None)
    if not c:
        raise PapierInvalide(f"Copropriétaire inconnu : {cid}")
    return c


def effet_demande_papier(demande: dict) -> date:
    recue = demande["recue_le"]
    return recue if demande.get("moyen") == "ag" else recue + timedelta(days=DELAI_EFFET_JOURS)


def canal(fiche: dict, cid: str, jour: date | None = None) -> dict:
    """Canal à utiliser pour ce copropriétaire à cette date, et pourquoi."""
    c = _copro(fiche, cid)
    jour = jour or date.today()
    d = c.get("demande_papier")
    if d:
        effet = effet_demande_papier(d)
        if jour >= effet:
            return {"canal": "courrier", "motif": f"Demande de courrier reçue le {d['recue_le']:%d/%m/%Y}, "
                                                    f"effective depuis le {effet:%d/%m/%Y}", "effet": effet.isoformat()}
        return {"canal": "email" if c.get("email") else "courrier",
                "motif": f"Demande de courrier reçue le {d['recue_le']:%d/%m/%Y} : effective le {effet:%d/%m/%Y}",
                "effet": effet.isoformat()}
    if c["envoi"] == "courrier" or not c.get("email"):
        return {"canal": "courrier", "motif": "Pas d'adresse électronique ou courrier choisi", "effet": None}
    return {"canal": "email", "motif": "Adresse électronique connue", "effet": None}


def adresse_postale(fiche: dict, cid: str) -> list[str]:
    c = _copro(fiche, cid)
    if c.get("adresse"):
        return [c["nom"], *[x.strip() for x in c["adresse"].split(",")]]
    lots = [l for l in fiche["lots"] if l["proprietaire"] == cid and l["type"] == "appartement"]
    lot = f"Lot {lots[0]['numero']} ({lots[0]['desc']})" if lots else ""
    return [c["nom"], fiche["copropriete"]["nom"], *([lot] if lot else []),
            *[x.strip() for x in fiche["copropriete"]["adresse"].split(",")]]


def valider_papier(fiche: dict) -> tuple[list[str], list[str]]:
    """(erreurs, alertes) sur les champs d'accessibilité de la fiche."""
    err, alertes = [], []
    for c in fiche["coproprietaires"]:
        cid = c["id"]
        if c["envoi"] not in ("email", "courrier"):
            err.append(f"{cid} : canal d'envoi inconnu « {c['envoi']} » (email ou courrier)")
        d = c.get("demande_papier")
        if d is not None:
            if not isinstance(d.get("recue_le"), date):
                err.append(f"{cid} : la date de réception de la demande de courrier est obligatoire")
            if d.get("moyen") not in MOYENS_DEMANDE:
                err.append(f"{cid} : moyen de demande inconnu « {d.get('moyen')} » ({', '.join(MOYENS_DEMANDE)})")
        elif c["envoi"] == "courrier" and c.get("email"):
            alertes.append(f"{cid} reçoit par courrier alors qu'une adresse électronique est connue : "
                           f"conserver la trace de sa demande (demande_papier)")
        if "grands_caracteres" in c and not isinstance(c["grands_caracteres"], bool):
            err.append(f"{cid} : grands_caracteres doit valoir true ou false")
        a = c.get("aidant")
        if a is not None:
            if not a.get("nom"):
                err.append(f"{cid} : le nom du proche aidant est obligatoire")
            if not (a.get("email") or a.get("adresse")):
                err.append(f"{cid} : il faut un mail ou une adresse pour le proche aidant")
            if not isinstance(a.get("consentement_le"), date):
                err.append(f"{cid} : la date de l'accord écrit du copropriétaire pour son aidant est obligatoire")
        if c.get("paiement", "virement") not in ("virement", "cheque", "prelevement"):
            err.append(f"{cid} : mode de paiement inconnu « {c.get('paiement')} »")
        if c.get("paiement") == "prelevement":
            m = c.get("mandat_sepa") or {}
            if not m.get("rum") or not isinstance(m.get("signe_le"), date):
                err.append(f"{cid} : prélèvement sans mandat SEPA signé (rum et signe_le)")
            if not fiche["appels_de_fonds"]["compte_separe"].get("ics"):
                err.append("Prélèvement SEPA : l'identifiant créancier (ics) du syndicat est obligatoire")
    return err, alertes


def copie_aidant(fiche: dict, cid: str) -> dict | None:
    a = _copro(fiche, cid).get("aidant")
    if not a:
        return None
    return {"nom": a["nom"], "email": a.get("email"), "adresse": a.get("adresse"),
            "note": "Copie pour information, avec l'accord du copropriétaire. Ne contient jamais de lien de vote."}


# --------------------------------------------------------------------------- #
# 2 et 3. Blocs réutilisables : encadré, lexique, talon
# --------------------------------------------------------------------------- #
def encadre_action(texte: str, st: dict):
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, Table, TableStyle
    from avis_appel import ACCENT, FOND
    t = Table([[Paragraph(f"<b>Ce que vous devez faire</b><br/>{texte}", st["txt"])]], colWidths=[174 * mm])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), FOND), ("BOX", (0, 0), (-1, -1), 1.2, ACCENT),
                           ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                           ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]))
    return t


def lexique(st: dict) -> list:
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, Spacer
    h = [Paragraph("Les mots de votre copropriété", st["titre"]), Spacer(1, 4 * mm)]
    for mot, definition in LEXIQUE:
        h += [Paragraph(f"<b>{mot}</b> — {definition}", st["txt"]), Spacer(1, 2.5 * mm)]
    h += [Spacer(1, 4 * mm), Paragraph(MENTION_PAPIER, st["petit"])]
    return h


def talon(fiche: dict, cid: str, montant: Decimal, reference: str, echeance: date, st: dict):
    """Coupon à découper et à joindre au chèque."""
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import KeepTogether, Paragraph, Spacer, Table, TableStyle
    from avis_appel import GRIS, euros
    c = _copro(fiche, cid)
    cpt = fiche["appels_de_fonds"]["compte_separe"]
    sid = fiche["gouvernance"]["syndic"]["coproprietaire_id"]
    syndic = _copro(fiche, sid)
    lot_syndic = next((l for l in fiche["lots"] if l["proprietaire"] == sid and l["type"] == "appartement"), None)
    corps = (f"<b>Talon à joindre à votre chèque</b><br/>"
             f"{fiche['copropriete']['nom']} · {c['nom']} ({cid})<br/>"
             f"Montant : <b>{euros(montant)}</b> avant le <b>{echeance:%d/%m/%Y}</b> · réf. {reference}<br/>"
             f"Chèque à l'ordre de : <b>{cpt['titulaire']}</b><br/>"
             f"À déposer dans la boîte aux lettres du syndic ({syndic['nom']}"
             + (f", lot {lot_syndic['numero']}" if lot_syndic else "") + f") ou à envoyer au "
             f"{fiche['copropriete']['adresse']}.")
    t = Table([[Paragraph("Découpez en suivant les pointillés", st["petit"])], [Paragraph(corps, st["txt"])]],
              colWidths=[174 * mm])
    t.setStyle(TableStyle([("LINEABOVE", (0, 1), (-1, 1), 1, GRIS, None, (3, 3)),
                           ("TOPPADDING", (0, 1), (-1, 1), 8), ("LEFTPADDING", (0, 0), (-1, -1), 6),
                           ("FONT", (0, 0), (-1, 0), "Helvetica", 7)]))
    return KeepTogether([Spacer(1, 4 * mm), t])


# --------------------------------------------------------------------------- #
# 3 bis. Mandat de prélèvement SEPA papier
# --------------------------------------------------------------------------- #
def generer_mandat_sepa(fiche: dict, cid: str, chemin: Path) -> Path:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    from avis_appel import FILET, styles, tampon

    c = _copro(fiche, cid)
    cpt = fiche["appels_de_fonds"]["compte_separe"]
    if not cpt.get("ics"):
        raise PapierInvalide("Le syndicat n'a pas d'identifiant créancier SEPA (ics) : à demander à sa banque")
    st = styles(bool(c.get("grands_caracteres")))
    prefixe = fiche["appels_de_fonds"]["reference_virement"].split("-")[0]
    rum = (c.get("mandat_sepa") or {}).get("rum") or f"{prefixe}-{cid}"
    doc = SimpleDocTemplate(str(chemin), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=16 * mm, bottomMargin=20 * mm, title="Mandat de prélèvement SEPA")
    h = [Paragraph("Mandat de prélèvement SEPA", st["titre"]),
         Paragraph(f"Paiement récurrent des appels de fonds · Référence unique du mandat (RUM) : <b>{rum}</b>", st["sous"]),
         Spacer(1, 5 * mm),
         Paragraph(f"En signant ce formulaire de mandat, vous autorisez le {cpt['titulaire']} à envoyer des "
                   f"instructions à votre banque pour débiter votre compte, et votre banque à débiter votre compte "
                   f"conformément aux instructions du {cpt['titulaire']}. Vous bénéficiez du droit d'être "
                   f"remboursé par votre banque selon les conditions décrites dans la convention que vous avez "
                   f"passée avec elle. Une demande de remboursement doit être présentée dans les 8 semaines suivant "
                   f"la date de débit de votre compte pour un prélèvement autorisé. Vos droits concernant le "
                   f"présent mandat sont expliqués dans un document que vous pouvez obtenir auprès de votre banque.",
                   st["txt"]),
         Spacer(1, 5 * mm)]
    ligne = "_" * 48
    blocs = [
        ["Créancier", Paragraph(f"{cpt['titulaire']}<br/>{fiche['copropriete']['adresse']}<br/>"
                                f"Identifiant créancier SEPA (ICS) : {cpt['ics']}", st["txt"])],
        ["Débiteur", Paragraph(f"{c['nom']} ({cid})<br/>Adresse : {ligne}<br/>{ligne}", st["txt"])],
        ["Compte à débiter", Paragraph(f"IBAN : {ligne}<br/>BIC : ____________________", st["txt"])],
        ["Type de paiement", Paragraph("Récurrent (chaque appel de fonds)", st["txt"])],
        ["Signature", Paragraph(f"Fait à ____________________, le ____ / ____ / ________<br/><br/><br/>"
                                f"Signature :", st["txt"])],
    ]
    t = Table(blocs, colWidths=[40 * mm, 134 * mm])
    t.setStyle(TableStyle([("FONT", (0, 0), (0, -1), "Helvetica-Bold", 12 if st is not None and st["txt"].fontSize > 12 else 9),
                           ("VALIGN", (0, 0), (-1, -1), "TOP"), ("GRID", (0, 0), (-1, -1), 0.4, FILET),
                           ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]))
    h += [t, Spacer(1, 6 * mm),
          Paragraph("Joignez un RIB et remettez ce mandat au syndic. Il est conservé par le syndicat, pas envoyé "
                    "à votre banque. Une fois signé, vous n'avez plus rien à faire : chaque appel de fonds sera "
                    "prélevé à sa date d'échéance, après un avis envoyé à l'avance. Vous pouvez y mettre fin à tout "
                    "moment en prévenant le syndic.", st["txt"]),
          Spacer(1, 4 * mm), Paragraph(MENTION_PAPIER, st["petit"])]
    pied = f"{fiche['copropriete']['nom']} – mandat de prélèvement SEPA – {c['nom']}"
    doc.build(h, onFirstPage=tampon(False, pied), onLaterPages=tampon(False, pied))
    return chemin


# --------------------------------------------------------------------------- #
# 4. Bulletin de sondage papier
# --------------------------------------------------------------------------- #
def generer_bulletin(fiche: dict, dossier: dict, sondage: dict, cid: str, comp: dict, resume: dict | None,
                     chemin: Path) -> Path:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
    from avis_appel import euros, styles, tableau, tampon
    from ag import _case

    c = _copro(fiche, cid)
    if cid not in sondage["electeurs"]:
        raise PapierInvalide(f"{cid} n'est pas concerné par ce sondage (clé de répartition)")
    st = styles(bool(c.get("grands_caracteres")))
    grand = bool(c.get("grands_caracteres"))
    clos = datetime.fromisoformat(sondage["clos_le"])
    sid = fiche["gouvernance"]["syndic"]["coproprietaire_id"]
    syndic = _copro(fiche, sid)
    lot_syndic = next((l for l in fiche["lots"] if l["proprietaire"] == sid and l["type"] == "appartement"), None)
    parts = next((q["parts"] for q in comp["quotes_parts"] if q["coproprietaire"] == cid), {})
    devis = {d["id"]: d for d in comp["devis"]}

    doc = SimpleDocTemplate(str(chemin), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=16 * mm, bottomMargin=20 * mm, title=f"Bulletin – {dossier['titre']}")
    h = [Paragraph("Votre avis sur les devis", st["titre"]),
         Paragraph(f"{fiche['copropriete']['nom']} · {dossier['titre']} · {c['nom']}", st["sous"]),
         Spacer(1, 4 * mm),
         encadre_action(f"Cochez <b>une seule case</b>, signez, puis déposez ce bulletin dans la boîte aux lettres "
                        f"du syndic ({syndic['nom']}" + (f", lot {lot_syndic['numero']}" if lot_syndic else "")
                        + f") <b>avant le {clos:%d/%m/%Y à %Hh%M}</b>.", st),
         Spacer(1, 4 * mm),
         Paragraph("<b>Sondage consultatif</b> : votre avis aide le syndic à préparer la décision, mais ne vaut pas "
                   "vote. Les travaux seront décidés selon les règles de la copropriété (syndic, conseil syndical "
                   "ou assemblée générale).", st["txt"]),
         Spacer(1, 4 * mm)]
    if resume and resume.get("statut") == "valide" and not resume.get("obsolete"):
        h += [Paragraph(f"<b>En bref</b> — {resume['resume']}", st["txt"]),
              Paragraph("Résumé rédigé par une IA à partir des devis, relu et validé par le syndic.", st["petit"]),
              Spacer(1, 4 * mm)]
    lignes = [["", "Devis", "Total TTC", "Votre part"]]
    for did in sondage["options"]:
        d = devis[did]
        lignes.append([_case(), f"{did} · {d['entreprise']}", euros(d["montant_ttc"]),
                       euros(parts[did]) if did in parts else "—"])
    lignes += [[_case(), "Aucun de ces devis", "", ""], [_case(), "Sans avis", "", ""]]
    h.append(tableau(lignes, [12 * mm, 92 * mm, 35 * mm, 35 * mm], aligner_droite_depuis=2, lignes_total=0, grand=grand))
    h += [Spacer(1, 8 * mm),
          Paragraph("Date : ____ / ____ / ________ &nbsp;&nbsp;&nbsp; Signature :", st["txt"]), Spacer(1, 14 * mm),
          Paragraph("Le comparatif complet des devis est disponible sur demande auprès du syndic. Si vous avez aussi "
                    "reçu un lien personnel, une seule réponse est comptée : la dernière reçue avant la clôture.",
                    st["petit"])]
    pied = f"{fiche['copropriete']['nom']} – bulletin de sondage {sondage['id']} – {c['nom']}"
    doc.build(h, onFirstPage=tampon(False, pied), onLaterPages=tampon(False, pied))
    return chemin


def saisir_bulletin(sondage: dict, cid: str, choix: str, recu_le: datetime, saisi_le: datetime | None = None) -> dict:
    """Le syndic saisit un bulletin papier reçu avant la clôture."""
    import vote
    if cid not in sondage["electeurs"]:
        raise PapierInvalide(f"{cid} n'est pas concerné par ce sondage")
    if recu_le >= datetime.fromisoformat(sondage["clos_le"]):
        raise PapierInvalide(f"Bulletin reçu le {recu_le:%d/%m/%Y à %Hh%M}, après la clôture du sondage")
    try:
        vote.voter_sondage(sondage, cid, choix, recu_le)
    except ValueError as e:
        raise PapierInvalide(str(e))
    e = sondage["electeurs"][cid]
    e["origine"] = "papier"
    e["saisi_le"] = (saisi_le or datetime.now()).replace(microsecond=0).isoformat()
    return {"coproprietaire": cid, "choix": choix, "vote_le": e["vote_le"], "origine": "papier",
            "trace": f"Saisi d'après bulletin papier reçu le {recu_le:%d/%m/%Y}"}


# --------------------------------------------------------------------------- #
# 5. Affiche du hall
# --------------------------------------------------------------------------- #
MAX_CHANTIERS, MAX_PLAN, MAX_POINTS_AG, MAX_DECISIONS = 4, 3, 4, 6   # une affiche tient sur une page
MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
        "novembre", "décembre"]


def contenu_affiche(fiche: dict, racine: Path, copro: str, jour: date) -> dict:
    """Ce qui s'affiche dans le hall : rien de personnel (ni nom de copropriétaire hors syndic,
    ni montant individuel, ni vote nominatif)."""
    import ag as assemblee
    import travaux
    from fiche_copro import echeances
    import plan_travaux

    ags = [a for a in assemblee.lister_ag(racine, copro)]
    futures = [a for a in ags if date.fromisoformat(a["date"]) >= jour]
    passees = [a for a in ags if date.fromisoformat(a["date"]) < jour and a["depouillee"]]
    prochaine = None
    if futures:
        a = assemblee.charger_ag(racine, copro, futures[0]["id"])
        prochaine = {"date": date.fromisoformat(a["date"]), "heure": a["heure"], "lieu": a["lieu"],
                     "titre": a["titre"], "points": [r["titre"] for r in a["resolutions"]]}
    limite_ag = next((e for e in echeances(fiche, jour, 365) if e["type"] == "ag"), None)
    decisions = []
    if passees:
        a = assemblee.charger_ag(racine, copro, passees[-1]["id"])
        dep = assemblee.depouiller(fiche, a, a["depouillement"]["votes"], a["depouillement"]["amendees"])
        decisions = {"date": date.fromisoformat(a["date"]),
                     "resolutions": [(r["titre"], r["adoptee"]) for r in dep["resolutions"]]}
    chantiers, sondages = [], []
    for d in travaux.lister_dossiers(racine, copro):
        dossier = travaux.charger_dossier(travaux.dossier_dir(racine, copro, d["id"]))
        etat = f"{d['nb_devis']} devis reçu(s)" if d["nb_devis"] else "devis demandés"
        for s in dossier.get("sondages", []):
            clos = datetime.fromisoformat(s["clos_le"])
            if clos > datetime.combine(jour, datetime.min.time()) and not s.get("cloture_anticipee"):
                sondages.append({"titre": d["titre"], "clos_le": clos})
                etat = "avis des copropriétaires en cours"
        chantiers.append({"id": d["id"], "titre": d["titre"], "etat": etat})
    chantiers = sorted(chantiers, key=lambda x: x["id"], reverse=True)[:MAX_CHANTIERS]   # les plus récents
    plan = [{"titre": l["intitule"], "annee": l["annee"]} for l in plan_travaux.lignes(
        fiche, plan_travaux.charger_suivi(racine, copro)) if l["statut"] in ("prevu", "reporte")
        and l["annee"] in (jour.year, jour.year + 1)][:MAX_PLAN] if fiche.get("plan_travaux") else []
    sid = fiche["gouvernance"]["syndic"]["coproprietaire_id"]
    syndic = _copro(fiche, sid)
    lot_syndic = next((l for l in fiche["lots"] if l["proprietaire"] == sid and l["type"] == "appartement"), None)
    return {"mois": f"{MOIS[jour.month - 1]} {jour.year}", "prochaine_ag": prochaine,
            "limite_ag": limite_ag, "decisions": decisions, "chantiers": chantiers, "sondages": sondages,
            "plan": plan, "syndic": {"nom": syndic["nom"], "email": syndic.get("email"),
                                     "lot": lot_syndic["numero"] if lot_syndic else None}}


FORMATS_AFFICHE = ("A4", "A3")


def generer_affiche(fiche: dict, racine: Path, copro: str, chemin: Path, jour: date | None = None,
                    format_papier: str = "A4") -> Path:
    """Affiche du hall. En A3, la page A4 est agrandie de √2 : mêmes proportions, texte lisible de plus loin."""
    if format_papier not in FORMATS_AFFICHE:
        raise PapierInvalide(f"Format inconnu « {format_papier} » (A4 ou A3)")
    a4 = _generer_affiche_a4(fiche, racine, copro, chemin, jour)
    if format_papier == "A3":
        from pypdf import PdfReader, PdfWriter
        ecrivain = PdfWriter(clone_from=str(a4))
        for page in ecrivain.pages:
            page.scale_by(2 ** 0.5)
        with open(chemin, "wb") as f:
            ecrivain.write(f)
    return chemin


def _generer_affiche_a4(fiche: dict, racine: Path, copro: str, chemin: Path, jour: date | None = None) -> Path:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    from avis_appel import ACCENT, ENCRE, FOND, GRIS, tampon

    jour = jour or date.today()
    c = contenu_affiche(fiche, racine, copro, jour)
    T = ParagraphStyle("t", fontName="Helvetica-Bold", fontSize=30, leading=35, textColor=ENCRE)
    ST = ParagraphStyle("st", fontName="Helvetica", fontSize=16, leading=20, textColor=GRIS)
    H = ParagraphStyle("h", fontName="Helvetica-Bold", fontSize=19, leading=24, textColor=ACCENT, spaceBefore=4)
    P = ParagraphStyle("p", fontName="Helvetica", fontSize=15, leading=20, textColor=ENCRE)
    PP = ParagraphStyle("pp", fontName="Helvetica", fontSize=11, leading=14, textColor=GRIS)

    def section(titre, lignes):
        corps = [Paragraph(titre, H), Spacer(1, 2 * mm)] + [Paragraph(l, P) for l in lignes]
        t = Table([[corps]], colWidths=[174 * mm])
        t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), FOND), ("LINEBEFORE", (0, 0), (0, -1), 4, ACCENT),
                               ("LEFTPADDING", (0, 0), (-1, -1), 12), ("TOPPADDING", (0, 0), (-1, -1), 8),
                               ("BOTTOMPADDING", (0, 0), (-1, -1), 10)]))
        return [t, Spacer(1, 5 * mm)]

    doc = SimpleDocTemplate(str(chemin), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=16 * mm, bottomMargin=20 * mm, title=f"Affiche du hall – {c['mois']}")
    h = [Paragraph(f"La vie de la {fiche['copropriete']['nom']}", T),
         Paragraph(f"{c['mois'].capitalize()} · à lire dans le hall", ST), Spacer(1, 7 * mm)]
    a = c["prochaine_ag"]
    if a:
        lignes = [f"<b>{a['date'].day} {MOIS[a['date'].month - 1]} {a['date'].year} à {a['heure']}</b> — {a['lieu']}",
                  "Au programme : " + " · ".join(a["points"][:MAX_POINTS_AG])
                  + (f" et {len(a['points']) - MAX_POINTS_AG} autre(s) point(s)" if len(a["points"]) > MAX_POINTS_AG else ""),
                  "Vous recevrez votre convocation et le formulaire pour voter par correspondance."]
        h += section("Prochaine assemblée générale", lignes)
    elif c["limite_ag"]:
        h += section("Assemblée générale", [f"L'assemblée générale annuelle doit se tenir avant le "
                                            f"<b>{c['limite_ag']['date_fr']}</b>. La date vous sera communiquée."])
    if c["sondages"]:
        h += section("Votre avis compte", [f"<b>{s['titre']}</b> : donnez votre avis avant le "
                                           f"{s['clos_le']:%d/%m/%Y}. Bulletin papier disponible auprès du syndic."
                                           for s in c["sondages"]])
    lignes = [f"<b>{x['titre']}</b> : {x['etat']}" for x in c["chantiers"]]
    lignes += [f"Prévu au plan de travaux en {x['annee']} : {x['titre']}" for x in c["plan"]]
    if lignes:
        h += section("Travaux", lignes)
    if c["decisions"]:
        d = c["decisions"]
        h += section(f"Décisions de l'AG du {d['date']:%d/%m/%Y}",
                     [f"<b>{'Adoptée' if ok else 'Rejetée'}</b> — {t}" for t, ok in d["resolutions"][:MAX_DECISIONS]])
    s = c["syndic"]
    h += section("Votre syndic", [f"<b>{s['nom']}</b>" + (f" · boîte aux lettres du lot {s['lot']}" if s["lot"] else "")
                                  + (f" · {s['email']}" if s["email"] else ""),
                                  "Tous les documents de la copropriété peuvent vous être remis sur papier : "
                                  "il suffit de le demander."])
    h.append(Paragraph("Cette affiche ne contient aucune information personnelle (ni montant individuel, ni vote "
                       "nominatif). Préparée par l'assistant Mitoyen pour le syndic.", PP))
    pied = f"{fiche['copropriete']['nom']} – affiche du hall – {c['mois']}"
    doc.build(h, onFirstPage=tampon(False, pied), onLaterPages=tampon(False, pied))
    return chemin


# --------------------------------------------------------------------------- #
# 6. Registre des envois
# --------------------------------------------------------------------------- #
def chemin_registre(racine: Path, copro: str) -> Path:
    return Path(racine) / copro / "envois.json"


def charger_registre(racine: Path, copro: str) -> list[dict]:
    p = chemin_registre(racine, copro)
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else []


def sauver_registre(racine: Path, copro: str, registre: list[dict]) -> None:
    p = chemin_registre(racine, copro)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(registre, ensure_ascii=False, indent=2), encoding="utf-8")


def enregistrer_envoi(racine: Path, copro: str, fiche: dict, cid: str, document: str, canal_envoi: str,
                      envoye_le: date, numero: str = "", notifie_le: date | None = None, note: str = "") -> dict:
    _copro(fiche, cid)
    if canal_envoi not in CANAUX_ENVOI:
        raise PapierInvalide(f"Canal inconnu « {canal_envoi} » ({', '.join(CANAUX_ENVOI)})")
    if not document or len(document) > 80:
        raise PapierInvalide("Le document doit être identifié (ex. « ag:2027-04-17 » ou « appel:2027-T1 »)")
    if notifie_le and notifie_le < envoye_le:
        raise PapierInvalide("La date de notification ne peut pas précéder la date d'envoi")
    registre = charger_registre(racine, copro)
    e = {"id": f"E{len(registre) + 1:04d}", "coproprietaire": cid, "document": document, "canal": canal_envoi,
         "envoye_le": envoye_le.isoformat(), "numero": numero.strip()[:40],
         "notifie_le": notifie_le.isoformat() if notifie_le else None, "statut": "notifie" if notifie_le else "envoye",
         "note": note.strip()[:300]}
    registre.append(e)
    sauver_registre(racine, copro, registre)
    return e


def mettre_a_jour_envoi(racine: Path, copro: str, eid: str, statut: str | None = None,
                        notifie_le: date | None = None, numero: str | None = None, note: str | None = None) -> dict:
    registre = charger_registre(racine, copro)
    e = next((x for x in registre if x["id"] == eid), None)
    if not e:
        raise PapierInvalide(f"Envoi inconnu : {eid}")
    if statut is not None:
        if statut not in ("prepare", "envoye", "notifie", "retourne", "refuse"):
            raise PapierInvalide(f"Statut inconnu « {statut} »")
        e["statut"] = statut
    if notifie_le is not None:
        if notifie_le < date.fromisoformat(e["envoye_le"]):
            raise PapierInvalide("La date de notification ne peut pas précéder la date d'envoi")
        e["notifie_le"], e["statut"] = notifie_le.isoformat(), "notifie"
    if numero is not None:
        e["numero"] = numero.strip()[:40]
    if note is not None:
        e["note"] = note.strip()[:300]
    sauver_registre(racine, copro, registre)
    return e


def controle_convocations(fiche: dict, ag: dict, registre: list[dict]) -> dict:
    """Pour chaque copropriétaire : sa convocation a-t-elle été notifiée à temps ?
    (au moins 21 jours avant l'AG, délai décompté à partir du lendemain de la notification)"""
    import ag as assemblee
    limite = date.fromisoformat(assemblee.calendrier(fiche, ag)["limite_notification"])
    doc = f"ag:{ag['id']}"
    lignes = []
    for c in sorted(fiche["coproprietaires"], key=lambda c: c["nom"]):
        envois = [e for e in registre if e["coproprietaire"] == c["id"] and e["document"] == doc]
        valides = [e for e in envois if e["canal"] in CANAUX_NOTIFICATION and e.get("notifie_le")]
        if any(date.fromisoformat(e["notifie_le"]) <= limite for e in valides):
            etat = "ok"
        elif valides:
            etat = "tardif"
        elif envois:
            etat = "en_attente"
        else:
            etat = "manquant"
        lignes.append({"id": c["id"], "nom": c["nom"], "etat": etat,
                       "envois": [{k: e[k] for k in ("id", "canal", "envoye_le", "notifie_le", "numero")} for e in envois]})
    return {"limite_notification": limite.isoformat(), "lignes": lignes,
            "nb_ok": sum(1 for l in lignes if l["etat"] == "ok"),
            "a_traiter": [l["nom"] for l in lignes if l["etat"] != "ok"],
            "note": "Seuls la lettre recommandée (papier ou électronique) et la remise en main propre contre "
                    "émargement donnent une date de notification certaine."}


# --------------------------------------------------------------------------- #
# 8. Lot postal
# --------------------------------------------------------------------------- #
def _page_adresse(fiche: dict, cid: str, titre: str, chemin: Path) -> Path:
    """Page d'adresse placée pour une enveloppe à fenêtre (format DL, fenêtre à droite)."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas as cv
    c = cv.Canvas(str(chemin), pagesize=A4)
    exp = [fiche["copropriete"]["nom"], "Syndic : " + _copro(fiche, fiche["gouvernance"]["syndic"]["coproprietaire_id"])["nom"],
           fiche["copropriete"]["adresse"]]
    c.setFont("Helvetica", 9)
    for i, l in enumerate(exp):
        c.drawString(20 * mm, A4[1] - (20 + 5 * i) * mm, l)
    c.setFont("Helvetica", 12)
    for i, l in enumerate(adresse_postale(fiche, cid)):
        c.drawString(110 * mm, A4[1] - (50 + 6 * i) * mm, l)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(20 * mm, A4[1] - 100 * mm, titre)
    c.setFont("Helvetica", 9)
    c.drawString(20 * mm, A4[1] - 107 * mm, "Documents joints à ce courrier : voir les pages suivantes.")
    c.showPage()
    c.save()
    return chemin


def preparer_lot(fiche: dict, envois: list[dict], dossier: Path, titre: str, document: str,
                 racine_data: Path | None = None, copro: str | None = None, jour: date | None = None) -> dict:
    """envois : [{"coproprietaire": cid, "fichiers": [Path, ...], "aidant": bool}].
    Produit lot_postal.pdf (une page d'adresse puis les documents, pour chaque destinataire) et
    manifeste.csv, et inscrit chaque courrier au registre avec le statut « préparé »."""
    from pypdf import PdfReader, PdfWriter
    jour = jour or date.today()
    dossier.mkdir(parents=True, exist_ok=True)
    ecrivain, manifeste, page = PdfWriter(), [], 1
    for i, e in enumerate(envois):
        cid = e["coproprietaire"]
        adr = _page_adresse(fiche, cid, titre, dossier / f"_adresse_{i}.pdf")
        debut = page
        for f in [adr, *e["fichiers"]]:
            for p in PdfReader(str(f)).pages:
                ecrivain.add_page(p)
                page += 1
        if (page - debut) % 2:                    # recto-verso : chaque courrier commence sur un recto
            ecrivain.add_blank_page(width=595.27, height=841.89)
            page += 1
        adr.unlink()
        lignes = adresse_postale(fiche, cid)
        manifeste.append({"ordre": i + 1, "coproprietaire": cid, "destinataire": lignes[0],
                          "adresse": " / ".join(lignes[1:]), "type_envoi": "lettre recommandée avec accusé de réception",
                          "pages": f"{debut}-{page - 1}", "nb_pages": page - debut})
    pdf = dossier / "lot_postal.pdf"
    with open(pdf, "wb") as f:
        ecrivain.write(f)
    csvp = dossier / "manifeste.csv"
    with open(csvp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(manifeste[0]) if manifeste else ["ordre"], delimiter=";")
        w.writeheader()
        w.writerows(manifeste)
    inscrits = []
    if racine_data and copro:
        for m in manifeste:
            inscrits.append(enregistrer_envoi(racine_data, copro, fiche, m["coproprietaire"], document, "lrar",
                                              jour, note="Préparé dans un lot postal : compléter le numéro de "
                                                         "recommandé et la date de première présentation"))
            mettre_a_jour_envoi(racine_data, copro, inscrits[-1]["id"], statut="prepare")
    return {"pdf": str(pdf), "manifeste": str(csvp), "nb_courriers": len(manifeste), "nb_pages": page - 1,
            "destinataires": manifeste, "registre": [x["id"] for x in inscrits],
            "note": "Imprimez en recto-verso (chaque courrier commence sur un recto) et utilisez des enveloppes "
                    "à fenêtre, ou déposez le PDF et le manifeste sur un service d'envoi de recommandés en ligne."}
