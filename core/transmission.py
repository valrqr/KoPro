"""
transmission.py — Transmettre la copropriété à un autre syndic (dossier de transfert).

Une copropriété qui quitte Mitoyen, pour un syndic professionnel ou un autre bénévole, doit pouvoir
partir avec tout. L'article 18-2 de la loi du 10 juillet 1965 impose d'ailleurs à l'ancien syndic de
remettre au nouveau, à compter de la fin de ses fonctions :
    - sous 15 jours : la situation de trésorerie, les références des comptes bancaires et les
      coordonnées de la banque ;
    - sous un mois : l'ensemble des documents et archives du syndicat, y compris dématérialisés ;
    - dans les deux mois suivants : le solde des fonds après apurement, l'état des comptes des
      copropriétaires et celui des comptes du syndicat.

Le dossier de transfert réunit tout cela dans une archive ZIP :

    00_bordereau.pdf                  ce qui est remis, la situation de trésorerie, la signature des deux syndics
    01_etat_des_comptes.pdf           solde de chaque copropriétaire et échéances impayées
    donnees/*.csv                     copropriétaires, lots et tantièmes, appels de fonds, opérations
                                      bancaires, relances, contrats, diagnostics, budget (UTF-8, « ; »)
    donnees/fiche_copropriete.yaml    la fiche complète, lisible par n'importe quel logiciel
    archives/data/…                   tout ce que Mitoyen conserve (AG, devis, relevés, règlement…)
    archives/documents_produits/…     tous les documents produits (avis d'appel, PV, courriers…)
    LISEZMOI.txt                      le mode d'emploi de l'archive

Les formats sont ouverts (PDF, CSV, YAML) : aucun logiciel particulier n'est nécessaire pour reprendre
la gestion. L'archive contient des données personnelles : elle ne se transmet qu'au nouveau syndic.
"""

from __future__ import annotations

import csv
import io
import json
import zipfile
from datetime import date
from decimal import Decimal
from pathlib import Path

import yaml

FICHIERS_EXCLUS = {".tmp", ".lock", ".pyc"}


class TransmissionInvalide(ValueError):
    pass


def _d(v) -> Decimal | None:
    if v in (None, ""):
        return None
    return Decimal(str(v)).quantize(Decimal("0.01"))


def _csv(lignes: list[dict], colonnes: list[str]) -> bytes:
    """CSV lisible par un tableur français : « ; », virgule décimale, UTF-8 avec BOM."""
    flux = io.StringIO()
    w = csv.writer(flux, delimiter=";", lineterminator="\r\n")
    w.writerow(colonnes)
    for l in lignes:
        rang = []
        for c in colonnes:
            v = l.get(c, "")
            if isinstance(v, Decimal):
                v = f"{v:.2f}".replace(".", ",")
            elif isinstance(v, (list, dict)):
                v = json.dumps(v, ensure_ascii=False, default=str)
            elif v is None:
                v = ""
            rang.append(str(v))
        w.writerow(rang)
    return ("﻿" + flux.getvalue()).encode("utf-8")


def parametres(f: dict) -> dict:
    """Ce que le syndic sortant indique : le successeur, la date, les soldes du relevé bancaire."""
    def date_(cle, lib, obligatoire=False):
        v = (f.get(cle) or "").strip()
        if not v:
            if obligatoire:
                raise TransmissionInvalide(f"{lib} : choisissez une date.")
            return None
        try:
            return date.fromisoformat(v)
        except ValueError:
            raise TransmissionInvalide(f"{lib} : date invalide.")

    def montant(cle, lib):
        v = (f.get(cle) or "").replace(" ", "").replace(" ", "").replace("€", "").replace(",", ".")
        if not v:
            return None
        try:
            return _d(v)
        except Exception:
            raise TransmissionInvalide(f"{lib} : « {f.get(cle)} » n'est pas un montant.")

    successeur = (f.get("successeur") or "").strip()
    if not successeur:
        raise TransmissionInvalide("Nouveau syndic : indiquez son nom.")
    p = {"successeur": successeur[:120], "successeur_adresse": (f.get("successeur_adresse") or "").strip()[:300],
         "cessation": date_("cessation", "Fin de vos fonctions", True),
         "date_soldes": date_("date_soldes", "Date du relevé"),
         "solde_courant": montant("solde_courant", "Solde du compte courant"),
         "solde_fonds_travaux": montant("solde_fonds_travaux", "Solde du compte du fonds de travaux")}
    if (p["solde_courant"] is not None or p["solde_fonds_travaux"] is not None) and not p["date_soldes"]:
        p["date_soldes"] = p["cessation"]
    return p


# --------------------------------------------------------------------------- #
# Les données
# --------------------------------------------------------------------------- #
def tables(fiche: dict, etat: dict, relances: list[dict]) -> dict[str, tuple[list[dict], list[str]]]:
    """Les tableaux CSV du dossier : {nom: (lignes, colonnes)}."""
    noms = {c["id"]: c["nom"] for c in fiche["coproprietaires"]}
    cles = list(fiche.get("cles_repartition") or {})
    copros = [{"id": c["id"], "nom": c["nom"], "email": c.get("email"), "envoi": c.get("envoi"),
               "adresse": c.get("adresse"), "telephone": c.get("telephone"),
               "lots": ", ".join(str(l["numero"]) for l in fiche["lots"] if l["proprietaire"] == c["id"])}
              for c in fiche["coproprietaires"]]
    lots = [{"numero": l["numero"], "type": l.get("type"), "description": l.get("desc"), "etage": l.get("etage"),
             "proprietaire_id": l["proprietaire"], "proprietaire": noms.get(l["proprietaire"]),
             **{f"tantiemes_{k}": (l.get("tantiemes") or {}).get(k, 0) for k in cles}}
            for l in sorted(fiche["lots"], key=lambda x: x["numero"])]
    comptes = [{"coproprietaire_id": c["coproprietaire"], "nom": c["nom"], "du_exigible": c["du_exigible"],
                "verse": c["verse"], "solde": c["solde"], "impaye": c["impaye"],
                "retard_max_jours": c["retard_max_jours"]}
               for c in sorted(etat["comptes"].values(), key=lambda x: x["nom"])]
    echeances = [{"coproprietaire_id": c["coproprietaire"], "nom": c["nom"], "echeance": l["echeance"],
                  "libelle": l["libelle"], "date": l["date"], "montant": l["montant"], "paye": l["paye"],
                  "reste": l["reste"]}
                 for c in etat["comptes"].values() for l in c["echeances"]]
    operations = [{"id": o.get("operation"), "date": o.get("date"), "libelle": o.get("libelle"),
                   "montant": _d(o.get("montant")), "statut": o.get("statut"),
                   "coproprietaires": ", ".join(f"{noms.get(p['coproprietaire'], p['coproprietaire'])} "
                                                f"({_d(p['montant'])})" for p in o.get("parts") or [])}
                  for o in etat["operations"]]
    rel = [{k: v for k, v in r.items() if not isinstance(v, (list, dict))} for r in relances]
    col_rel = sorted({k for r in rel for k in r}) or ["id"]
    contrats = [{**c} for c in fiche.get("contrats") or []]
    col_ct = sorted({k for c in contrats for k in c}) or ["id"]
    diag = [{**d} for d in fiche.get("diagnostics") or []]
    col_dg = sorted({k for d in diag for k in d}) or ["id"]
    budget = [{"poste": p.get("libelle") or p.get("poste") or p.get("id"), "cle": p.get("cle"),
               "montant": _d(p.get("montant"))} for p in (fiche.get("budget_previsionnel") or {}).get("postes") or []]
    return {
        "coproprietaires": (copros, ["id", "nom", "lots", "email", "envoi", "adresse", "telephone"]),
        "lots": (lots, ["numero", "type", "description", "etage", "proprietaire_id", "proprietaire"]
                 + [f"tantiemes_{k}" for k in cles]),
        "comptes_coproprietaires": (comptes, ["coproprietaire_id", "nom", "du_exigible", "verse", "solde", "impaye",
                                              "retard_max_jours"]),
        "echeances": (echeances, ["coproprietaire_id", "nom", "echeance", "libelle", "date", "montant", "paye", "reste"]),
        "operations_bancaires": (operations, ["id", "date", "libelle", "montant", "statut", "coproprietaires"]),
        "relances": (rel, col_rel),
        "contrats": (contrats, col_ct),
        "diagnostics": (diag, col_dg),
        "budget_previsionnel": (budget, ["poste", "cle", "montant"]),
    }


def _fichiers(racine: Path | None, copro: str) -> list[Path]:
    if not racine:
        return []
    base = Path(racine) / copro
    if not base.is_dir():
        return []
    return sorted(p for p in base.rglob("*") if p.is_file() and p.suffix not in FICHIERS_EXCLUS
                  and "transmission" not in p.relative_to(base).parts and not p.name.startswith("."))


def resume(fiche: dict, etat: dict, p: dict, racine_data: Path | None, racine_sortie: Path | None,
           copro: str) -> dict:
    cpt = list(etat["comptes"].values())
    debiteurs = [c for c in cpt if c["solde"] > 0]
    crediteurs = [c for c in cpt if c["solde"] < 0]
    ft = fiche.get("fonds_travaux") or {}
    travaux = []
    if racine_data:
        for f in sorted((Path(racine_data) / copro / "travaux").glob("*/dossier.yaml")):
            d = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
            travaux.append({"titre": d.get("titre") or f.parent.name, "devis": len(d.get("devis") or []),
                            "statut": d.get("statut") or "en préparation"})
    return {
        "debiteurs": len(debiteurs), "total_debiteurs": sum((c["solde"] for c in debiteurs), Decimal("0")),
        "crediteurs": len(crediteurs), "total_crediteurs": -sum((c["solde"] for c in crediteurs), Decimal("0")),
        "solde_net": sum((c["solde"] for c in cpt), Decimal("0")),
        "fonds_travaux_fiche": ft.get("solde"), "cotisation_fonds_travaux": ft.get("cotisation_annuelle"),
        "contrats": fiche.get("contrats") or [], "travaux": travaux,
        "nb_archives": len(_fichiers(racine_data, copro)) + len(_fichiers(racine_sortie, copro)),
        "nb_operations": len(etat["operations"]),
    }


# --------------------------------------------------------------------------- #
# Les PDF
# --------------------------------------------------------------------------- #
def _bordereau(fiche: dict, p: dict, r: dict, contenu: list[tuple[str, str]], jour: date) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    from avis_appel import CanevasNumerote, FILET, essentiel, euros, styles, tableau, tampon
    from html import escape as e
    S = dict(styles())
    S["sous_titre"] = S["titre"].clone("h2_transmission", fontSize=13.5, leading=17, spaceBefore=4, keepWithNext=1)
    nom = fiche["copropriete"]["nom"]
    syndic_id = fiche["gouvernance"]["syndic"]["coproprietaire_id"]
    sortant = next((c["nom"] for c in fiche["coproprietaires"] if c["id"] == syndic_id), "le syndic")
    cs = (fiche.get("appels_de_fonds") or {}).get("compte_separe") or {}
    h = [Paragraph("Dossier de transfert", S["titre"]),
         Paragraph(f"{e(nom)} · de {e(sortant)} à {e(p['successeur'])} · fin des fonctions le "
                   f"{p['cessation']:%d/%m/%Y}", S["sous"]), Spacer(1, 4 * mm),
         Paragraph("Ce dossier rassemble ce que l'article 18-2 de la loi du 10 juillet 1965 impose de remettre au "
                   "nouveau syndic : la situation de trésorerie et les références bancaires (sous 15 jours), les "
                   "documents et archives (sous un mois), puis le solde des fonds et l'état des comptes (dans les deux "
                   "mois suivants). Tout est dans des formats ouverts : PDF, tableaux CSV, fiche YAML.", S["txt"]),
         Spacer(1, 4 * mm)]
    chiffres = []
    if p["solde_courant"] is not None:
        chiffres.append(("Compte courant", euros(p["solde_courant"]), f"au {p['date_soldes']:%d/%m/%Y}"))
    if p["solde_fonds_travaux"] is not None:
        chiffres.append(("Fonds de travaux", euros(p["solde_fonds_travaux"]), f"au {p['date_soldes']:%d/%m/%Y}"))
    chiffres.append(("Copropriétaires débiteurs", str(r["debiteurs"]), f"{euros(r['total_debiteurs'])} dus au syndicat"))
    h += [essentiel(chiffres[:3]), Spacer(1, 5 * mm)]

    h += [Paragraph("1. Trésorerie et comptes bancaires", S["sous_titre"]), Spacer(1, 1 * mm)]
    lignes = [["Élément", "Valeur"], ["Titulaire", cs.get("titulaire") or "à compléter"], ["IBAN", cs.get("iban") or "à compléter"],
              ["BIC", cs.get("bic") or "à compléter"]]
    if cs.get("ics"):
        lignes.append(["Identifiant créancier SEPA", cs["ics"]])
    lignes += [["Solde du compte courant", euros(p["solde_courant"]) if p["solde_courant"] is not None
                else "à compléter d'après le relevé"],
               ["Solde du compte du fonds de travaux", euros(p["solde_fonds_travaux"])
                if p["solde_fonds_travaux"] is not None else "à compléter d'après le relevé"]]
    if r["fonds_travaux_fiche"]:
        sft = r["fonds_travaux_fiche"]
        lignes.append(["Fonds de travaux selon la fiche", f"{euros(sft.get('montant'))} au "
                       f"{sft.get('au'):%d/%m/%Y}" if hasattr(sft.get("au"), "strftime") else euros(sft.get("montant"))])
    h += [tableau(lignes, [70 * mm, 104 * mm], 1, lignes_total=0, serre=True), Spacer(1, 4 * mm)]

    h += [Paragraph("2. Comptes des copropriétaires", S["sous_titre"]), Spacer(1, 1 * mm),
          Paragraph((f"{r['debiteurs']} copropriétaire{'s' if r['debiteurs'] > 1 else ''} doi{'vent' if r['debiteurs'] > 1 else 't'} "
                     f"{euros(r['total_debiteurs'])} au syndicat" if r["debiteurs"] else "Aucun copropriétaire ne doit d'argent au syndicat")
                    + (f" ; {r['crediteurs']} {'ont' if r['crediteurs'] > 1 else 'a'} versé {euros(r['total_crediteurs'])} "
                       "d'avance" if r["crediteurs"] else "") + ". Le détail par copropriétaire et par échéance est "
                    "dans 01_etat_des_comptes.pdf et dans les tableaux CSV. Les relances et mises en demeure déjà "
                    "envoyées figurent dans donnees/relances.csv.", S["txt"]), Spacer(1, 4 * mm)]

    if r["contrats"] or r["travaux"]:
        h += [Paragraph("3. Contrats et dossiers en cours", S["sous_titre"]), Spacer(1, 1 * mm)]
        lignes = [["Objet", "Prestataire", "Échéance"]]
        for c in r["contrats"]:
            ech = c.get("echeance")
            lignes.append([Paragraph(e(str(c.get("objet") or c.get("id"))), S["txt"]),
                           Paragraph(e(str(c.get("prestataire") or "")), S["txt"]),
                           ech.strftime("%d/%m/%Y") if hasattr(ech, "strftime") else (ech or "")])
        for t in r["travaux"]:
            lignes.append([Paragraph(e(f"Travaux : {t['titre']}"), S["txt"]), f"{t['devis']} devis", str(t["statut"])])
        h += [tableau(lignes, [92 * mm, 50 * mm, 32 * mm], 2, lignes_total=0, serre=True), Spacer(1, 4 * mm)]

    h += [Paragraph("4. Contenu de l'archive", S["sous_titre"]), Spacer(1, 1 * mm)]
    lignes = [["Fichier", "Contenu"]] + [[a, Paragraph(e(b), S["petit"])] for a, b in contenu]
    h += [tableau(lignes, [62 * mm, 112 * mm], 2, lignes_total=0, serre=True), Spacer(1, 6 * mm)]

    sig = Table([[Paragraph(f"<b>Le syndic sortant</b><br/>{e(sortant)}<br/><br/>Remis le : ____ / ____ / ________"
                            "<br/><br/>Signature :", S["txt"]),
                  Paragraph(f"<b>Le nouveau syndic</b><br/>{e(p['successeur'])}<br/><br/>Reçu le : ____ / ____ / ________"
                            "<br/><br/>Signature :", S["txt"])]], colWidths=[87 * mm, 87 * mm], rowHeights=[42 * mm])
    sig.setStyle(TableStyle([("BOX", (0, 0), (0, 0), 0.6, FILET), ("BOX", (1, 0), (1, 0), 0.6, FILET),
                             ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 8),
                             ("TOPPADDING", (0, 0), (-1, -1), 8)]))
    h += [KeepTogether([sig]), Spacer(1, 5 * mm),
          Paragraph(f"Établi avec Mitoyen le {jour:%d/%m/%Y}. L'archive contient des données personnelles : elle ne se "
                    "transmet qu'au nouveau syndic. Pas un conseil juridique : en cas de doute, l'ADIL de votre "
                    "département vous répond gratuitement.", S["petit"])]
    tampon_ = io.BytesIO()
    pied = f"{nom} – dossier de transfert"
    SimpleDocTemplate(tampon_, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=20 * mm,
                      bottomMargin=18 * mm, title="Dossier de transfert", author="Mitoyen").build(
        h, canvasmaker=CanevasNumerote, onFirstPage=tampon(False, pied), onLaterPages=tampon(False, pied))
    return tampon_.getvalue()


# --------------------------------------------------------------------------- #
# L'archive
# --------------------------------------------------------------------------- #
LISEZMOI = """DOSSIER DE TRANSFERT — {nom}
Établi le {jour} par {sortant}, syndic sortant, pour {successeur}.

00_bordereau.pdf              Ce qui est remis, la trésorerie, les comptes bancaires. À signer par les deux syndics.
01_etat_des_comptes.pdf       Le solde de chaque copropriétaire et ses échéances impayées.
donnees/                      Les tableaux, en CSV (séparateur « ; », virgule décimale, UTF-8) :
  coproprietaires.csv           noms, coordonnées, mode d'envoi (courrier ou e-mail), lots
  lots.csv                      lots, propriétaires et tantièmes par clé de répartition
  comptes_coproprietaires.csv   solde de chacun (positif : il doit au syndicat ; négatif : il a payé d'avance)
  echeances.csv                 chaque appel de fonds, ce qui est payé et ce qui reste dû
  operations_bancaires.csv      les opérations importées des relevés et leur rapprochement
  relances.csv                  rappels, relances et mises en demeure
  contrats.csv, diagnostics.csv, budget_previsionnel.csv
  fiche_copropriete.yaml        toute la fiche de la copropriété (format texte, lisible par tout logiciel)
archives/data/                Tout ce que Mitoyen conserve : AG, devis, relevés bancaires, règlement…
archives/documents_produits/  Tous les documents produits : avis d'appel de fonds, PV, courriers…

Rappel (article 18-2 de la loi du 10 juillet 1965) : trésorerie et références bancaires sous 15 jours,
documents et archives sous un mois, solde des fonds et état des comptes dans les deux mois suivants.

Ce dossier contient des données personnelles. Ne le transmettez qu'au nouveau syndic, et supprimez vos
copies une fois la transmission terminée.
"""


def generer(fiche: dict, p: dict, etat: dict, relances: list[dict], racine_data: Path | None,
            racine_sortie: Path | None, copro: str, sortie: Path, jour: date | None = None) -> dict:
    """Produit l'archive ZIP du dossier de transfert dans `sortie`."""
    import impayes
    jour = jour or date.today()
    r = resume(fiche, etat, p, racine_data, racine_sortie, copro)
    tab = tables(fiche, etat, relances)
    contenu = [("00_bordereau.pdf", "ce document, à signer"),
               ("01_etat_des_comptes.pdf", "solde et impayés de chaque copropriétaire"),
               ("donnees/*.csv", f"{len(tab)} tableaux : copropriétaires, lots, comptes, échéances, "
                                 f"{r['nb_operations']} opérations bancaires, relances, contrats…"),
               ("donnees/fiche_copropriete.yaml", "toute la fiche de la copropriété"),
               ("archives/", f"{r['nb_archives']} fichiers : AG, devis, relevés, documents produits")]
    sortie = Path(sortie)
    sortie.mkdir(parents=True, exist_ok=True)
    etat_pdf = sortie / "01_etat_des_comptes.pdf"
    impayes.generer_etat(fiche, etat, etat_pdf)
    syndic_id = fiche["gouvernance"]["syndic"]["coproprietaire_id"]
    sortant = next((c["nom"] for c in fiche["coproprietaires"] if c["id"] == syndic_id), "le syndic")
    nom_archive = f"dossier_transfert_{copro}_{jour:%Y%m%d}.zip"
    archive = sortie / nom_archive
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("LISEZMOI.txt", LISEZMOI.format(nom=fiche["copropriete"]["nom"], jour=f"{jour:%d/%m/%Y}",
                                                    sortant=sortant, successeur=p["successeur"]))
        z.writestr("00_bordereau.pdf", _bordereau(fiche, p, r, contenu, jour))
        z.write(etat_pdf, "01_etat_des_comptes.pdf")
        for nom, (lignes, colonnes) in tab.items():
            z.writestr(f"donnees/{nom}.csv", _csv(lignes, colonnes))
        z.writestr("donnees/fiche_copropriete.yaml",
                   yaml.safe_dump(fiche, allow_unicode=True, sort_keys=False, default_flow_style=False))
        for racine, dossier in ((racine_data, "data"), (racine_sortie, "documents_produits")):
            for f in _fichiers(racine, copro):
                z.write(f, f"archives/{dossier}/{f.relative_to(Path(racine) / copro).as_posix()}")
    resume_ = {"archive": nom_archive, "successeur": p["successeur"], "cessation": p["cessation"].isoformat(),
               "le": jour.isoformat(), "fichiers_archives": r["nb_archives"], "debiteurs": r["debiteurs"],
               "total_debiteurs": str(r["total_debiteurs"])}
    (sortie / "transmission.json").write_text(json.dumps(resume_, ensure_ascii=False, indent=2), encoding="utf-8")
    return {**resume_, "chemin": str(archive)}
