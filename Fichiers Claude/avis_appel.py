"""
avis_appel.py — Génère les avis d'appel de fonds en PDF à partir d'une fiche copro.

Usage :
    python core/avis_appel.py config/mimosas.yaml --trimestre 1
    python core/avis_appel.py config/mimosas.yaml --trimestre 1 --final

Produit dans out/<exercice>-T<n>/ :
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
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

sys.path.insert(0, str(Path(__file__).parent))
from fiche_copro import appel_par_lot, charger_fiche, rubriques, valider  # noqa: E402

# --------------------------------------------------------------------------- #
# Mise en forme
# --------------------------------------------------------------------------- #
ENCRE = colors.HexColor("#1F2933")
GRIS = colors.HexColor("#616E7C")
FILET = colors.HexColor("#CBD2D9")
FOND = colors.HexColor("#F5F7FA")
ACCENT = colors.HexColor("#2F5D8A")

base = getSampleStyleSheet()
S = {
    "titre": ParagraphStyle("titre", parent=base["Title"], fontName="Helvetica-Bold", fontSize=18,
                            textColor=ENCRE, alignment=0, spaceAfter=2),
    "sous": ParagraphStyle("sous", parent=base["Normal"], fontSize=10.5, textColor=GRIS),
    "txt": ParagraphStyle("txt", parent=base["Normal"], fontSize=9.5, leading=13, textColor=ENCRE),
    "petit": ParagraphStyle("petit", parent=base["Normal"], fontSize=8, leading=10.5, textColor=GRIS),
    "droite": ParagraphStyle("droite", parent=base["Normal"], fontSize=9.5, leading=13,
                             textColor=ENCRE, alignment=TA_RIGHT),
}


def euros(m: Decimal) -> str:
    """1234.5 -> '1 234,50 €' (format français)."""
    s = f"{m:,.2f}".replace(",", " ").replace(".", ",")
    return f"{s} €"


def fr(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def tampon(projet: bool, pied: str):
    """Dessine le pied de page et, en mode projet, la mention PROJET en filigrane."""
    def _dessiner(canvas, doc):
        canvas.saveState()
        if projet:
            canvas.setFont("Helvetica-Bold", 90)
            canvas.setFillColor(colors.Color(0.85, 0.2, 0.2, alpha=0.12))
            canvas.translate(A4[0] / 2, A4[1] / 2)
            canvas.rotate(35)
            canvas.drawCentredString(0, 0, "PROJET")
            canvas.rotate(-35)
            canvas.translate(-A4[0] / 2, -A4[1] / 2)
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(GRIS)
        canvas.drawString(18 * mm, 12 * mm, pied)
        canvas.drawRightString(A4[0] - 18 * mm, 12 * mm, f"Page {doc.page}")
        canvas.restoreState()
    return _dessiner


def tableau(donnees, largeurs, aligner_droite_depuis: int, lignes_total: int = 1):
    t = Table(donnees, colWidths=largeurs, repeatRows=1)
    style = [
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8.5),
        ("FONT", (0, 1), (-1, -1), "Helvetica", 9),
        ("TEXTCOLOR", (0, 0), (-1, 0), GRIS),
        ("TEXTCOLOR", (0, 1), (-1, -1), ENCRE),
        ("LINEBELOW", (0, 0), (-1, 0), 0.8, ENCRE),
        ("LINEBELOW", (0, 1), (-1, -1 - lignes_total), 0.3, FILET),
        ("ALIGN", (aligner_droite_depuis, 0), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    if lignes_total:
        style += [
            ("FONT", (0, -lignes_total), (-1, -1), "Helvetica-Bold", 10),
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

    chemin = dossier / f"avis_{cid}.pdf"
    doc = SimpleDocTemplate(str(chemin), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=16 * mm, bottomMargin=20 * mm,
                            title=f"Appel de fonds {exercice} T{trimestre} – {copro['nom']}")
    h = []

    # En-tête : émetteur à gauche, destinataire à droite
    emetteur = Paragraph(
        f"<b>{ident['nom']}</b><br/>{ident['adresse']}<br/>"
        f"Syndic {'coopératif' if mode == 'cooperatif' else 'bénévole'} : {syndic['nom']}<br/>"
        f"Immatriculation : {ident['immatriculation_registre']}", S["txt"])
    destinataire = Paragraph(f"<b>{copro['nom']}</b><br/>Copropriétaire {cid}<br/>"
                             f"Envoi : {copro['envoi']}", S["txt"])
    entete = Table([[emetteur, destinataire]], colWidths=[100 * mm, 74 * mm])
    entete.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                                ("BOX", (1, 0), (1, 0), 0.5, FILET),
                                ("LEFTPADDING", (1, 0), (1, 0), 8), ("TOPPADDING", (1, 0), (1, 0), 6),
                                ("BOTTOMPADDING", (1, 0), (1, 0), 6)]))
    h += [entete, Spacer(1, 10 * mm)]

    h.append(Paragraph("Avis d'appel de fonds", S["titre"]))
    h.append(Paragraph(f"Exercice {exercice} · appel {trimestre} sur {nb_appels} · "
                       f"émis le {fr(emission)} · <b>exigible le {fr(exigibilite)}</b>", S["sous"]))
    h.append(Spacer(1, 7 * mm))

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
    h.append(tableau(lignes, [12 * mm, 30 * mm, 66 * mm, 30 * mm, 36 * mm], aligner_droite_depuis=3))
    h.append(Spacer(1, 6 * mm))

    # Bloc de paiement
    cpt = adf["compte_separe"]
    ref = adf["reference_virement"].format(exercice=exercice, trimestre=trimestre, coproprietaire=cid)
    paiement = Table([[Paragraph(
        f"<b>Règlement par virement avant le {fr(exigibilite)}</b><br/>"
        f"Titulaire : {cpt['titulaire']}<br/>IBAN : {cpt['iban']} · BIC : {cpt['bic']}<br/>"
        f"Référence à indiquer : <b>{ref}</b>", S["txt"])]], colWidths=[174 * mm])
    paiement.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), FOND),
                                  ("LINEBEFORE", (0, 0), (0, -1), 2.5, ACCENT),
                                  ("LEFTPADDING", (0, 0), (-1, -1), 10),
                                  ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]))
    h += [paiement, Spacer(1, 6 * mm)]

    # Rappel de la base de calcul
    budget = sum((r["annuel"] for r in rubs if r["id"] != "fonds_travaux"), Decimal("0"))
    ft = next(r["annuel"] for r in rubs if r["id"] == "fonds_travaux")
    h.append(Paragraph(
        f"Base de calcul : budget prévisionnel {exercice} de {euros(budget)} voté en assemblée générale, "
        f"et cotisation au fonds de travaux de {euros(ft)}, appelés en {nb_appels} fractions égales et répartis "
        f"selon les tantièmes de chaque lot. Le fonds de travaux reste attaché aux lots et n'est pas remboursable "
        f"en cas de vente.", S["petit"]))

    pied = (f"{ident['nom']} – document préparé par l'assistant du syndic"
            + (" – PROJET, non valable avant validation" if projet else ""))
    doc.build(h, onFirstPage=tampon(projet, pied), onLaterPages=tampon(projet, pied))
    return chemin, total


# --------------------------------------------------------------------------- #
# Récapitulatif à faire valider par le syndic
# --------------------------------------------------------------------------- #
def generer_recap(fiche, totaux, trimestre, dossier, projet) -> Path:
    ident = fiche["copropriete"]
    exercice = fiche["budget_previsionnel"]["exercice"]
    emission, exigibilite = dates_appel(fiche, trimestre)
    copros = {c["id"]: c for c in fiche["coproprietaires"]}
    chemin = dossier / "recap_syndic.pdf"
    doc = SimpleDocTemplate(str(chemin), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=16 * mm, bottomMargin=20 * mm,
                            title=f"Récapitulatif appel de fonds {exercice} T{trimestre}")
    h = [Paragraph(f"Récapitulatif – appel {trimestre} de l'exercice {exercice}", S["titre"]),
         Paragraph(f"{ident['nom']} · émission le {fr(emission)} · exigible le {fr(exigibilite)}", S["sous"]),
         Spacer(1, 7 * mm)]

    lignes = [["ID", "Copropriétaire", "Envoi", "Lots", "Montant"]]
    for cid in sorted(totaux):
        lots = ", ".join(str(l["numero"]) for l in fiche["lots"] if l["proprietaire"] == cid)
        lignes.append([cid, copros[cid]["nom"], copros[cid]["envoi"], lots, euros(totaux[cid])])
    lignes.append(["", "", "", "Total appelé", euros(sum(totaux.values()))])
    h.append(tableau(lignes, [16 * mm, 50 * mm, 24 * mm, 48 * mm, 36 * mm], aligner_droite_depuis=4))
    h.append(Spacer(1, 4 * mm))

    nb_courrier = sum(1 for cid in totaux if copros[cid]["envoi"] == "courrier")
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
    doc.build(h, onFirstPage=tampon(projet, pied), onLaterPages=tampon(projet, pied))
    return chemin


# --------------------------------------------------------------------------- #
def main() -> int:
    p = argparse.ArgumentParser(description="Génère les avis d'appel de fonds en PDF.")
    p.add_argument("fiche")
    p.add_argument("--trimestre", type=int, required=True, choices=[1, 2, 3, 4])
    p.add_argument("--sortie", default="out")
    p.add_argument("--final", action="store_true", help="Retire la mention PROJET (après validation)")
    a = p.parse_args()

    fiche = charger_fiche(a.fiche)
    rapport = valider(fiche)
    if not rapport.ok:
        print("❌ Fiche invalide, aucun avis généré :")
        for e in rapport.erreurs:
            print(f"   - {e}")
        return 1

    exercice = fiche["budget_previsionnel"]["exercice"]
    dossier = Path(a.sortie) / f"{exercice}-T{a.trimestre}"
    dossier.mkdir(parents=True, exist_ok=True)
    projet = not a.final
    montants = appel_par_lot(fiche)

    proprios = sorted({l["proprietaire"] for l in fiche["lots"]})
    totaux, envoi = {}, []
    copros = {c["id"]: c for c in fiche["coproprietaires"]}
    for cid in proprios:
        chemin, total = generer_avis(fiche, cid, montants, a.trimestre, dossier, projet)
        totaux[cid] = total
        ref = fiche["appels_de_fonds"]["reference_virement"].format(
            exercice=exercice, trimestre=a.trimestre, coproprietaire=cid)
        envoi.append({"coproprietaire": cid, "nom": copros[cid]["nom"], "envoi": copros[cid]["envoi"],
                      "email": copros[cid].get("email"), "montant": euros(total), "reference": ref,
                      "fichier": chemin.name})
    recap = generer_recap(fiche, totaux, a.trimestre, dossier, projet)

    emission, exigibilite = dates_appel(fiche, a.trimestre)
    (dossier / "envoi.json").write_text(json.dumps({
        "copropriete": fiche["copropriete"]["nom"], "exercice": exercice, "trimestre": a.trimestre,
        "date_emission": emission.isoformat(), "date_exigibilite": exigibilite.isoformat(),
        "date_exigibilite_fr": fr(exigibilite), "dossier": str(dossier.resolve()),
        "statut": "projet" if projet else "valide", "total": euros(sum(totaux.values())), "avis": envoi,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"✅ {len(totaux)} avis + récapitulatif générés dans {dossier}/ "
          f"({'PROJET' if projet else 'version finale'}), total {sum(totaux.values())} €")
    return 0


if __name__ == "__main__":
    sys.exit(main())
