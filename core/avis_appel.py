"""
avis_appel.py — Génère les avis d'appel de fonds en PDF à partir d'une fiche copro.

Usage :
    python core/avis_appel.py config/mimosas.yaml --trimestre 1
    python core/avis_appel.py config/mimosas.yaml --trimestre 1 --final

Produit dans out/<copro>/<exercice>-T<n>/ :
    - avis_<ID>.pdf      un avis par copropriétaire, détaillé lot par lot
    - recap_syndic.pdf   la feuille de contrôle que le syndic relit et signe
    - envoi.json         la liste d'envoi (email / courrier) que n8n lira ensuite

Par défaut, chaque page porte la mention PROJET : l'agent prépare, le syndic
valide. L'option --final retire la mention, une fois la récap signée.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

sys.path.insert(0, str(Path(__file__).parent))
from fiche_copro import appel_par_lot, charger_fiche, rubriques, valider  # noqa: E402

# --------------------------------------------------------------------------- #
# Mise en forme
# --------------------------------------------------------------------------- #
# Charte : noir et blanc, comme le logo, et une seule couleur vive (ORANGE) réservée
# aux chiffres importants. L'orange est assez foncé pour rester lisible en petit et
# ressort en gris moyen sur une imprimante noir et blanc.
ENCRE = colors.HexColor("#1E1E1E")      # le noir du logo
GRIS = colors.HexColor("#5C5C5C")
FILET = colors.HexColor("#D4D4D4")
FOND = colors.HexColor("#F4F4F4")
ACCENT = ENCRE                          # cadres, filets, encadrés : en noir
ORANGE = colors.HexColor("#C2410C")     # chiffres importants uniquement
ORANGE_HTML = "#C2410C"                 # la même, pour les balises <font> des paragraphes


def fort(texte: str) -> str:
    """Chiffre important dans un paragraphe : gras et orange."""
    return f"<font color='{ORANGE_HTML}'><b>{texte}</b></font>"
MARGE_HAUTE = 20 * mm                   # place du bandeau d'en-tête

base = getSampleStyleSheet()
S = {
    "titre": ParagraphStyle("titre", parent=base["Title"], fontName="Helvetica-Bold", fontSize=20, leading=24,
                            textColor=ENCRE, alignment=0, spaceAfter=2),
    "sous": ParagraphStyle("sous", parent=base["Normal"], fontSize=11, leading=14, textColor=GRIS),
    "txt": ParagraphStyle("txt", parent=base["Normal"], fontSize=10, leading=14, textColor=ENCRE),
    "petit": ParagraphStyle("petit", parent=base["Normal"], fontSize=9, leading=12, textColor=GRIS),
    "droite": ParagraphStyle("droite", parent=base["Normal"], fontSize=10, leading=14,
                             textColor=ENCRE, alignment=TA_RIGHT),
}


def styles(grand: bool = False) -> dict:
    """Styles de texte. grand=True : version « grands caractères » (14 pt pour le texte courant),
    pour les copropriétaires qui l'ont demandée."""
    if not grand:
        return S
    return {
        "titre": ParagraphStyle("titre_g", parent=S["titre"], fontSize=24, leading=29),
        "sous": ParagraphStyle("sous_g", parent=S["sous"], fontSize=14, leading=18),
        "txt": ParagraphStyle("txt_g", parent=S["txt"], fontSize=14, leading=19),
        "petit": ParagraphStyle("petit_g", parent=S["petit"], fontSize=12, leading=16),
        "droite": ParagraphStyle("droite_g", parent=S["droite"], fontSize=14, leading=19),
    }


def euros(m: Decimal) -> str:
    """1234.5 -> '1 234,50 €' (format français)."""
    s = f"{m:,.2f}".replace(",", " ").replace(".", ",")
    return f"{s} €"


def euros_rond(m: Decimal) -> str:
    """1234.5 -> '1 235 €' : pour les chiffres clés, où les centimes encombrent."""
    return f"{Decimal(m):,.0f}".replace(",", "\u00a0") + "\u00a0€"


def fr(d: date) -> str:
    return d.strftime("%d/%m/%Y")


ICONE = Path(__file__).resolve().parent / "ressources" / "icone.png"


def tampon(projet: bool, pied: str):
    """Bandeau d'en-tête (icône et nom Mitoyen, type de document, copropriété), pied de
    page et, en mode projet, la mention PROJET en filigrane. Le numéro « Page x sur y »
    est posé par CanevasNumerote, une fois le nombre de pages connu."""
    copro = pied.split(" – ")[0]

    def _dessiner(canvas, doc):
        canvas.saveState()
        haut, g, d = A4[1], 18 * mm, A4[0] - 18 * mm
        y = haut - 11 * mm                                  # ligne de base du bandeau
        if ICONE.is_file():
            canvas.drawImage(str(ICONE), g, y - 1 * mm, width=7.4 * mm, height=7 * mm, mask="auto",
                             preserveAspectRatio=True)
        canvas.setFillColor(ENCRE)
        canvas.setFont("Helvetica-Bold", 13)
        canvas.drawString(g + 9.5 * mm, y + 1 * mm, "Mitoyen")
        canvas.setFont("Helvetica-Bold", 9)
        canvas.drawRightString(d, y + 3 * mm, (doc.title or "").split(" – ")[0].upper())
        canvas.setFont("Helvetica", 9)
        canvas.setFillColor(GRIS)
        canvas.drawRightString(d, y - 1.5 * mm, copro)
        canvas.setStrokeColor(ENCRE)
        canvas.setLineWidth(1.2)
        canvas.line(g, y - 3.5 * mm, d, y - 3.5 * mm)
        if projet:
            canvas.setFont("Helvetica-Bold", 90)
            canvas.setFillColor(colors.Color(0.76, 0.25, 0.05, alpha=0.10))
            canvas.translate(A4[0] / 2, A4[1] / 2)
            canvas.rotate(35)
            canvas.drawCentredString(0, 0, "PROJET")
            canvas.rotate(-35)
            canvas.translate(-A4[0] / 2, -A4[1] / 2)
        canvas.setStrokeColor(FILET)
        canvas.setLineWidth(0.5)
        canvas.line(g, 16 * mm, d, 16 * mm)
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(GRIS)
        canvas.drawString(g, 11.5 * mm, pied)
        canvas.restoreState()
    return _dessiner


class CanevasNumerote(rl_canvas.Canvas):
    """Canevas qui attend la fin du document pour écrire « Page x sur y »."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._pages = []

    def showPage(self):
        self._pages.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._pages)
        for etat in self._pages:
            self.__dict__.update(etat)
            self.setFont("Helvetica", 8)
            self.setFillColor(GRIS)
            self.drawRightString(A4[0] - 18 * mm, 11.5 * mm, f"Page {self._pageNumber} sur {total}")
            super().showPage()
        super().save()


def essentiel(chiffres: list[tuple[str, str, str]], grand: bool = False, largeur: float = 174 * mm):
    """Encadré « L'essentiel » : 2 à 4 chiffres clés en gros, en orange, chacun avec
    son libellé au-dessus et une précision en dessous. chiffres = [(libellé, valeur, précision)]."""
    n = len(chiffres)
    lib = ParagraphStyle("ess_lib", fontName="Helvetica-Bold", fontSize=12 if grand else 8.5,
                         leading=15 if grand else 11, textColor=GRIS)
    val = ParagraphStyle("ess_val", fontName="Helvetica-Bold", fontSize=22 if grand else 17,
                         leading=27 if grand else 21, textColor=ORANGE)
    note = ParagraphStyle("ess_note", fontName="Helvetica", fontSize=12 if grand else 8.5,
                          leading=15 if grand else 11, textColor=ENCRE)
    cellules = [[Paragraph(l.upper(), lib), Paragraph(v, val), Paragraph(p, note)] for l, v, p in chiffres]
    t = Table([cellules], colWidths=[largeur / n] * n)
    style = [("LINEABOVE", (0, 0), (-1, 0), 2, ENCRE), ("LINEBELOW", (0, 0), (-1, 0), 0.5, ENCRE),
             ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 5),
             ("BOTTOMPADDING", (0, 0), (-1, -1), 6), ("LEFTPADDING", (0, 0), (-1, -1), 6)]
    style += [("LINEBEFORE", (i, 0), (i, 0), 0.5, FILET) for i in range(1, n)]
    t.setStyle(TableStyle(style))
    return t


def tableau(donnees, largeurs, aligner_droite_depuis: int, lignes_total: int = 1, grand: bool = False,
            serre: bool = False):
    """serre=True : lignes plus rapprochées, pour les longues listes (même taille de texte)."""
    t = Table(donnees, colWidths=largeurs, repeatRows=1)
    style = [
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 12 if grand else 9),
        ("FONT", (0, 1), (-1, -1), "Helvetica", 13 if grand else 10),
        ("TEXTCOLOR", (0, 0), (-1, 0), GRIS),
        ("TEXTCOLOR", (0, 1), (-1, -1), ENCRE),
        ("LINEBELOW", (0, 0), (-1, 0), 0.8, ENCRE),
        ("LINEBELOW", (0, 1), (-1, -1 - lignes_total), 0.3, FILET),
        ("ALIGN", (aligner_droite_depuis, 0), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 2 if serre else 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2 if serre else 4),
    ]
    if lignes_total:
        style += [
            ("FONT", (0, -lignes_total), (-1, -1), "Helvetica-Bold", 14 if grand else 10.5),
            ("LINEABOVE", (0, -lignes_total), (-1, -lignes_total), 0.8, ENCRE),
            ("BACKGROUND", (0, -lignes_total), (-1, -1), FOND),
        ]
    t.setStyle(TableStyle(style))
    return t


# --------------------------------------------------------------------------- #
# Calendrier de l'appel
# --------------------------------------------------------------------------- #
def dates_appel(fiche: dict, trimestre: int) -> tuple[date, date]:
    adf = fiche["appels_de_fonds"]
    exercice = fiche["budget_previsionnel"]["exercice"]
    mm_, jj = adf["dates_exigibilite"][trimestre - 1].split("-")
    exigibilite = date(exercice, int(mm_), int(jj))
    emission = exigibilite - timedelta(days=adf.get("envoi_jours_avant", 21))
    return emission, exigibilite


# --------------------------------------------------------------------------- #
# Un avis par copropriétaire
# --------------------------------------------------------------------------- #
def generer_avis(fiche, cid, montants, trimestre, dossier, projet) -> tuple[Path, Decimal]:
    import papier
    ident = fiche["copropriete"]
    copro = next(c for c in fiche["coproprietaires"] if c["id"] == cid)
    syndic_id = fiche["gouvernance"]["syndic"]["coproprietaire_id"]
    syndic = next(c for c in fiche["coproprietaires"] if c["id"] == syndic_id)
    mode = fiche["gouvernance"]["mode_gestion"]
    adf = fiche["appels_de_fonds"]
    exercice = fiche["budget_previsionnel"]["exercice"]
    emission, exigibilite = dates_appel(fiche, trimestre)
    nb_appels = len(adf["dates_exigibilite"])
    rubs = rubriques(fiche)
    cles = fiche["cles_repartition"]
    lots = sorted((l for l in fiche["lots"] if l["proprietaire"] == cid), key=lambda l: l["numero"])
    grand = bool(copro.get("grands_caracteres"))
    st = styles(grand)
    canal_avis = papier.canal(fiche, cid, emission)["canal"]
    paiement_mode = copro.get("paiement", "virement")
    # Le chèque est proposé à qui l'a choisi, et à tout copropriétaire qui reçoit ses documents par courrier
    cheque_possible = paiement_mode == "cheque" or (paiement_mode == "virement" and canal_avis == "courrier")

    chemin = dossier / f"avis_{cid}.pdf"
    doc = SimpleDocTemplate(str(chemin), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=20 * mm, bottomMargin=18 * mm,
                            title=f"Appel de fonds {exercice} T{trimestre} – {copro['nom']}")
    h = []

    # En-tête : émetteur à gauche, destinataire à droite
    emetteur = Paragraph(
        f"<b>{ident['nom']}</b><br/>{ident['adresse']}<br/>"
        f"Syndic {'coopératif' if mode == 'cooperatif' else 'bénévole'} : {syndic['nom']}<br/>"
        f"Immatriculation : {ident['immatriculation_registre']}", st["txt"])
    destinataire = Paragraph(f"<b>{copro['nom']}</b><br/>Copropriétaire {cid}<br/>"
                             f"Envoi : {canal_avis}", st["txt"])
    entete = Table([[emetteur, destinataire]], colWidths=[100 * mm, 74 * mm])
    entete.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                                ("BOX", (1, 0), (1, 0), 0.5, FILET),
                                ("LEFTPADDING", (1, 0), (1, 0), 8), ("TOPPADDING", (1, 0), (1, 0), 6),
                                ("BOTTOMPADDING", (1, 0), (1, 0), 6)]))
    h += [entete, Spacer(1, 10 * mm)]

    h.append(Paragraph("Avis d'appel de fonds", st["titre"]))
    h.append(Paragraph(f"Exercice {exercice} · appel {trimestre} sur {nb_appels} · "
                       f"émis le {fr(emission)} · <b>exigible le {fr(exigibilite)}</b>", st["sous"]))
    h.append(Spacer(1, 5 * mm))
    MARQUE_TOTAL = "@@TOTAL@@"     # remplacé une fois le total connu
    h.append(MARQUE_TOTAL)

    # Détail lot par lot
    lignes = [["Lot", "Désignation", "Rubrique", "Tantièmes", "Montant"]]
    total = Decimal("0")
    for lot in lots:
        for rub in rubs:
            m = montants.get((rub["id"], lot["numero"]))
            if not m:
                continue
            t = lot["tantiemes"][rub["cle"]]
            lignes.append([str(lot["numero"]), lot["desc"], rub["libelle"],
                           f"{t} / {cles[rub['cle']]['base']}", euros(m)])
            total += m
    lignes.append(["", "", "", "Total à régler", euros(total)])
    if grand:
        lignes = [[l[0], Paragraph(str(l[1]), st["txt"]) if i and l[1] else l[1],
                   Paragraph(str(l[2]), st["txt"]) if i and l[2] else l[2], l[3], l[4]] for i, l in enumerate(lignes)]
        lignes[0] = ["Lot", "Bien", "Rubrique", "Tantièmes", "Montant"]
        largeurs = [11 * mm, 20 * mm, 78 * mm, 31 * mm, 34 * mm]
    else:
        largeurs = [12 * mm, 30 * mm, 66 * mm, 30 * mm, 36 * mm]
    h.append(tableau(lignes, largeurs, aligner_droite_depuis=3, grand=grand))
    h.append(Spacer(1, 6 * mm))

    # « Ce que vous devez faire », en une phrase, selon le mode de paiement
    cpt = adf["compte_separe"]
    ref = adf["reference_virement"].format(exercice=exercice, trimestre=trimestre, coproprietaire=cid)
    if paiement_mode == "prelevement":
        action = (f"<b>Rien à faire</b> : {fort(euros(total))} seront prélevés sur votre compte le {fort(fr(exigibilite))}, "
                  f"selon le mandat de prélèvement que vous avez signé.")
    elif cheque_possible:
        action = (f"Payer {fort(euros(total))} avant le {fort(fr(exigibilite))} : par chèque à l'ordre du "
                  f"« {cpt['titulaire']} », avec le talon en bas de page, ou par virement (référence {ref}).")
    else:
        action = f"Payer {fort(euros(total))} avant le {fort(fr(exigibilite))}, par virement (référence {ref})."
    i = h.index(MARQUE_TOTAL)
    h[i:i + 1] = [papier.encadre_action(action, st), Spacer(1, 6 * mm)]

    # Bloc de paiement
    if paiement_mode == "prelevement":
        mandat = copro.get("mandat_sepa") or {}
        texte_paiement = (f"<b>Prélèvement le {fr(exigibilite)}</b><br/>Créancier : {cpt['titulaire']} "
                          f"(ICS {cpt.get('ics', '—')})<br/>Mandat {mandat.get('rum', '—')}, signé le "
                          f"{fr(mandat['signe_le']) if mandat.get('signe_le') else '—'}")
    else:
        texte_paiement = (f"<b>Règlement par virement avant le {fr(exigibilite)}</b><br/>"
                          f"Titulaire : {cpt['titulaire']}<br/>IBAN : {cpt['iban']} · BIC : {cpt['bic']}<br/>"
                          f"Référence à indiquer : <b>{ref}</b>")
    paiement = Table([[Paragraph(texte_paiement, st["txt"])]], colWidths=[174 * mm])
    paiement.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), FOND),
                                  ("LINEBEFORE", (0, 0), (0, -1), 2.5, ACCENT),
                                  ("LEFTPADDING", (0, 0), (-1, -1), 10),
                                  ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]))
    h += [paiement, Spacer(1, 6 * mm)]

    # Rappel de la base de calcul
    budget = sum((r["annuel"] for r in rubs if r["id"] != "fonds_travaux"), Decimal("0"))
    ft = next(r["annuel"] for r in rubs if r["id"] == "fonds_travaux")
    base_calcul = [Paragraph(
        f"Base de calcul : budget prévisionnel {exercice} de {euros(budget)} voté en assemblée générale, "
        f"et cotisation au fonds de travaux de {euros(ft)}, appelés en {nb_appels} fractions égales et répartis "
        f"selon les tantièmes de chaque lot. Le fonds de travaux reste attaché aux lots et n'est pas remboursable "
        f"en cas de vente.", st["petit"]),
        Spacer(1, 3 * mm), Paragraph(papier.MENTION_PAPIER, st["petit"])]    # mention obligatoire (loi 1965, art. 42-1)
    from reportlab.platypus import PageBreak
    if grand:
        # Grands caractères : le talon reste en bas de la première page, le reste passe en page 2
        if cheque_possible:
            h.append(papier.talon(fiche, cid, total, ref, exigibilite, st))
        h += [PageBreak(), base_calcul[0], Spacer(1, 6 * mm), *papier.lexique(st)]   # le lexique porte la mention
    else:
        h += base_calcul
        if cheque_possible:
            h.append(papier.talon(fiche, cid, total, ref, exigibilite, st))
        if canal_avis == "courrier":
            h += [PageBreak(), *papier.lexique(st)]

    pied = (f"{ident['nom']} – document préparé par l'assistant du syndic"
            + (" – PROJET, non valable avant validation" if projet else ""))
    doc.build(h, canvasmaker=CanevasNumerote, onFirstPage=tampon(projet, pied), onLaterPages=tampon(projet, pied))
    return chemin, total


# --------------------------------------------------------------------------- #
# Récapitulatif à faire valider par le syndic
# --------------------------------------------------------------------------- #
def generer_recap(fiche, totaux, trimestre, dossier, projet, canaux: dict | None = None) -> Path:
    ident = fiche["copropriete"]
    exercice = fiche["budget_previsionnel"]["exercice"]
    emission, exigibilite = dates_appel(fiche, trimestre)
    copros = {c["id"]: c for c in fiche["coproprietaires"]}
    chemin = dossier / "recap_syndic.pdf"
    doc = SimpleDocTemplate(str(chemin), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=20 * mm, bottomMargin=18 * mm,
                            title=f"Récapitulatif appel de fonds {exercice} T{trimestre}")
    h = [Paragraph(f"Récapitulatif – appel {trimestre} de l'exercice {exercice}", S["titre"]),
         Paragraph(f"{ident['nom']} · émission le {fr(emission)} · exigible le {fr(exigibilite)}", S["sous"]),
         Spacer(1, 7 * mm)]

    lignes = [["ID", "Copropriétaire", "Envoi", "Lots", "Montant"]]
    for cid in sorted(totaux):
        lots = ", ".join(str(l["numero"]) for l in fiche["lots"] if l["proprietaire"] == cid)
        lignes.append([cid, copros[cid]["nom"], (canaux or {}).get(cid, copros[cid]["envoi"]), lots,
                       euros(totaux[cid])])
    lignes.append(["", "", "", "Total appelé", euros(sum(totaux.values()))])
    h.append(tableau(lignes, [16 * mm, 50 * mm, 24 * mm, 48 * mm, 36 * mm], aligner_droite_depuis=4))
    h.append(Spacer(1, 4 * mm))

    nb_courrier = sum(1 for cid in totaux if (canaux or {}).get(cid, copros[cid]["envoi"]) == "courrier")
    h.append(Paragraph(f"{len(totaux)} avis générés, dont {nb_courrier} à envoyer par courrier. "
                       f"Contrôles passés : tantièmes, références, fonds de travaux ≥ 5 %, total au centime.",
                       S["txt"]))
    h.append(Spacer(1, 12 * mm))

    signature = Table([[Paragraph("<b>Validation du syndic</b><br/><br/>Bon pour envoi le : ____ / ____ / ________"
                                  "<br/><br/>Nom : ______________________ &nbsp;&nbsp; Signature :", S["txt"])]],
                      colWidths=[174 * mm], rowHeights=[40 * mm])
    signature.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.8, ENCRE), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                                   ("LEFTPADDING", (0, 0), (-1, -1), 10), ("TOPPADDING", (0, 0), (-1, -1), 8)]))
    h.append(signature)
    pied = f"{ident['nom']} – feuille de contrôle interne, ne pas diffuser"
    doc.build(h, canvasmaker=CanevasNumerote, onFirstPage=tampon(projet, pied), onLaterPages=tampon(projet, pied))
    return chemin


# --------------------------------------------------------------------------- #
# Point d'entrée réutilisable (CLI et API)
# --------------------------------------------------------------------------- #
class FicheInvalide(Exception):
    def __init__(self, erreurs: list[str]):
        super().__init__("Fiche invalide")
        self.erreurs = erreurs


def dossier_appel(racine: Path, copro: str, exercice: int, trimestre: int) -> Path:
    return Path(racine) / copro / f"{exercice}-T{trimestre}"


def generer_appel(fiche: dict, copro: str, trimestre: int, racine: Path, final: bool = False) -> dict:
    import papier
    """Valide la fiche, génère les avis + la récap, écrit envoi.json et le renvoie.
    Lève FicheInvalide si la fiche ne passe pas les contrôles."""
    rapport = valider(fiche)
    if not rapport.ok:
        raise FicheInvalide(rapport.erreurs)

    exercice = fiche["budget_previsionnel"]["exercice"]
    dossier = dossier_appel(racine, copro, exercice, trimestre)
    dossier.mkdir(parents=True, exist_ok=True)
    projet = not final
    montants = appel_par_lot(fiche)
    copros = {c["id"]: c for c in fiche["coproprietaires"]}
    libelles = {r["id"]: r["libelle"] for r in rubriques(fiche)}
    lots_de = {}
    for l in fiche["lots"]:
        lots_de.setdefault(l["proprietaire"], set()).add(l["numero"])

    totaux, envoi = {}, []
    for cid in sorted({l["proprietaire"] for l in fiche["lots"]}):
        chemin, total = generer_avis(fiche, cid, montants, trimestre, dossier, projet)
        totaux[cid] = total
        ref = fiche["appels_de_fonds"]["reference_virement"].format(
            exercice=exercice, trimestre=trimestre, coproprietaire=cid)
        k = papier.canal(fiche, cid, dates_appel(fiche, trimestre)[0])
        aidant = papier.copie_aidant(fiche, cid)
        if aidant and "appels" not in ((copros[cid].get("aidant") or {}).get("documents") or ["appels"]):
            aidant = None
        envoi.append({"coproprietaire": cid, "nom": copros[cid]["nom"], "envoi": k["canal"],
                      "email": copros[cid].get("email"), "montant": euros(total), "reference": ref,
                      # montant brut et détail par rubrique : le recouvrement en a besoin (une mise en
                      # demeure doit indiquer la nature et le montant de chaque provision réclamée)
                      "montant_num": str(total),
                      "detail": [{"rubrique": libelles[rid], "montant": str(sum(
                          (m for (r2, n), m in montants.items() if r2 == rid and n in lots_de[cid]), Decimal("0")))}
                          for rid in libelles if any(r2 == rid and n in lots_de[cid] for (r2, n) in montants)],
                      "fichier": chemin.name, "grands_caracteres": bool(copros[cid].get("grands_caracteres")),
                      "paiement": copros[cid].get("paiement", "virement"),
                      "adresse_postale": papier.adresse_postale(fiche, cid) if k["canal"] == "courrier" else None,
                      "copie_aidant": aidant})
    generer_recap(fiche, totaux, trimestre, dossier, projet,
                  {e["coproprietaire"]: e["envoi"] for e in envoi})

    emission, exigibilite = dates_appel(fiche, trimestre)
    resultat = {
        "copro": copro, "copropriete": fiche["copropriete"]["nom"], "exercice": exercice,
        "trimestre": trimestre, "date_emission": emission.isoformat(),
        "date_exigibilite": exigibilite.isoformat(), "date_exigibilite_fr": fr(exigibilite),
        "dossier": str(dossier), "statut": "projet" if projet else "valide",
        "nb_avis": len(envoi), "nb_courriers": sum(1 for e in envoi if e["envoi"] == "courrier"),
        "nb_copies_aidant": sum(1 for e in envoi if e["copie_aidant"]),
        "total": euros(sum(totaux.values())), "avis": envoi,
    }
    (dossier / "envoi.json").write_text(json.dumps(resultat, ensure_ascii=False, indent=2), encoding="utf-8")
    return resultat


def main() -> int:
    p = argparse.ArgumentParser(description="Génère les avis d'appel de fonds en PDF.")
    p.add_argument("fiche", help="Chemin de la fiche YAML, ex. config/mimosas.yaml")
    p.add_argument("--trimestre", type=int, required=True, choices=[1, 2, 3, 4])
    p.add_argument("--sortie", default="out")
    p.add_argument("--final", action="store_true", help="Retire la mention PROJET (après validation)")
    a = p.parse_args()

    copro = Path(a.fiche).stem
    try:
        r = generer_appel(charger_fiche(a.fiche), copro, a.trimestre, Path(a.sortie), a.final)
    except FicheInvalide as e:
        print("❌ Fiche invalide, aucun avis généré :")
        for err in e.erreurs:
            print(f"   - {err}")
        return 1
    print(f"✅ {r['nb_avis']} avis + récapitulatif générés dans {r['dossier']}/ "
          f"({'PROJET' if r['statut'] == 'projet' else 'version finale'}), total {r['total']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
