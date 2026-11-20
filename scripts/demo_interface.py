"""
demo_interface.py — Prépare des données fictives pour découvrir l'interface.

    python scripts/demo_interface.py
    MOTEUR_DATA_DIR=demo/data MOTEUR_OUT_DIR=demo/out INTERFACE_MOT_DE_PASSE=demo \\
        uvicorn moteur.api:app --reload

puis ouvrir http://localhost:8000/app/mimosas?au=2027-05-20 (mot de passe : demo).
Le paramètre « au » fixe la date du jour de la démonstration : le 20 mai 2027, des
paiements attendent confirmation et des relances sont à préparer.

Tout est écrit dans demo/, ignoré par git. Relancer le script repart de zéro.
"""

import shutil
import sys
from datetime import date
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE / "core"))

import ag as assemblee  # noqa: E402
import avis_appel  # noqa: E402
import banque  # noqa: E402
import meubles_tourisme  # noqa: E402
import reglement  # noqa: E402
import plan_travaux  # noqa: E402
import travaux  # noqa: E402
from fiche_copro import charger_fiche  # noqa: E402

DEMO = RACINE / "demo"


def main() -> None:
    if DEMO.exists():
        shutil.rmtree(DEMO)
    data, out = DEMO / "data", DEMO / "out"
    fiche = charger_fiche(RACINE / "config" / "mimosas.yaml")
    for t in (1, 2):
        avis_appel.generer_appel(fiche, "mimosas", t, out, final=True)
    banque.importer(data, "mimosas", (RACINE / "exemples" / "banque" / "releve_mimosas_2027.csv").read_bytes(),
                    "releve_mimosas_2027.csv", date(2027, 5, 20))
    meubles_tourisme.enregistrer(fiche, data, "mimosas", 9, date(2027, 5, 2), residence_principale=False)
    odj = assemblee.ordre_du_jour_type(fiche)
    pt = meubles_tourisme.point_information(fiche, meubles_tourisme.charger(data, "mimosas"), date(2027, 6, 12))
    odj.append({k: v for k, v in pt.items() if not k.startswith("_")})
    a = assemblee.creer_ag(data, "mimosas", fiche, date(2027, 6, 12), "10h", "Salle commune, rez-de-chaussée", odj,
                           aujourd_hui=date(2027, 5, 1))
    meubles_tourisme.marquer_inscrites(data, "mimosas", pt["_declarations"], a["id"])
    # Un dossier de travaux ouvert depuis le plan (porte de hall), avec trois devis
    d = plan_travaux.ouvrir_dossier(data, "mimosas", fiche, "L01", date(2027, 3, 2))
    devis = RACINE / "exemples" / "devis"
    for entreprise, ht, validite, pdf, recu in (
            ("Portes Azur", "3200", date(2027, 7, 31), "devis_portes_azur.pdf", date(2027, 3, 20)),
            ("Fermetures Niçoises", "2780", date(2027, 8, 15), "devis_fermetures_nicoises.pdf", date(2027, 4, 3)),
            ("Menuiserie du Port", "4150", date(2027, 5, 15), "devis_menuiserie_du_port.pdf", date(2027, 4, 18))):
        travaux.ajouter_devis(data, "mimosas", d, entreprise, ht, "10", 30, validite, "",
                              (devis / pdf).read_bytes(), recu)
    # Le règlement en clair, tel que l'IA le rédige à partir du règlement fictif (citations vérifiées)
    r = reglement.importer(data, "mimosas", (RACINE / "exemples" / "reglement" / "reglement_mimosas.pdf").read_bytes(),
                           "reglement_mimosas.pdf", date(2027, 4, 2))
    brouillon = {
        "destination": [("Les logements servent à l'habitation ; une profession libérale est tolérée sans gêne pour les voisins.", "2", "L'immeuble est destiné à l'habitation bourgeoise"),
                        ("Aucun commerce dans un logement.", "2", "Toute activité commerciale est interdite dans les lots à usage d'habitation")],
        "location": [("Vous pouvez louer, meublé de courte durée compris, si le locataire respecte le règlement.", "6", "Les copropriétaires peuvent louer leur lot"),
                     ("Le propriétaire qui loue répond des manquements de son locataire.", "6", "Le copropriétaire bailleur demeure responsable envers le syndicat")],
        "parties_communes": [("Rien ne reste dans le hall ni les couloirs : vélos et poussettes vont au local prévu.", "5", "Les parties communes ne peuvent être encombrées d'aucun objet"),
                             ("Les portes d'entrée restent fermées.", "5", "Les portes d'accès à l'immeuble doivent être maintenues fermées")],
        "travaux": [("Les travaux qui touchent les parties communes ou l'aspect de l'immeuble passent par l'AG.", "7", "ne peuvent être entrepris qu'après autorisation de l'assemblée générale"),
                    ("Prévenez le syndic 15 jours avant le début de vos travaux.", "7", "doit en informer le syndic au moins quinze jours à l'avance")],
        "aspect": [("Pas de climatiseur, d'antenne ni de parabole en façade ou sur un balcon sans accord de l'AG.", "9", "Aucune enseigne, antenne, parabole ni appareil de climatisation"),
                   ("Pas de linge aux fenêtres ni sur les balcons visibles de la rue.", "9", "Il est interdit d'étendre du linge aux fenêtres"),
                   ("Volets et fenêtres se remplacent à l'identique ; les volets sont gris clair.", "10", "de même teinte et de même aspect que ceux d'origine")],
        "nuisances": [("Travaux bruyants : du lundi au vendredi de 8 h à 19 h, le samedi de 9 h à 12 h, jamais le dimanche.", "8", "du lundi au vendredi de 8 heures à 19 heures"),
                      ("Musique et télévision ne doivent pas s'entendre chez les voisins, surtout de 22 h à 7 h.", "11", "en particulier entre 22 heures et 7 heures")],
        "animaux": [("Les animaux sont admis s'ils ne causent ni dégât ni gêne.", "12", "La détention d'animaux domestiques est tolérée"),
                    ("Les chiens restent en laisse dans le hall et le jardin.", "12", "Les chiens doivent être tenus en laisse")],
        "stationnement": [("Les caves servent au rangement, jamais de logement ; rien d'inflammable.", "13", "Les caves ne peuvent servir qu'au rangement"),
                          ("Les places de parking accueillent voitures et deux-roues, pas des stocks.", "13", "ils ne peuvent servir d'entrepôt")],
        "securite": [("Chaque copropriétaire assure son lot (incendie, dégât des eaux, responsabilité civile).", "15", "Chaque copropriétaire est tenu d'assurer son lot"),
                     ("Poubelles en sacs fermés, dans les conteneurs, en respectant le tri.", "14", "dans des sacs fermés, dans les conteneurs du local poubelles")],
        "charges": [("Les charges communes se partagent selon les tantièmes généraux ; l'ascenseur selon ses propres tantièmes, sans le rez-de-chaussée.", "16", "les lots situés au rez-de-chaussée n'y participant pas")],
    }
    themes, ecartees = reglement.controler(r, {k: [{"regle": x, "articles": [n], "citation": q} for x, n, q in v]
                                               for k, v in brouillon.items()})
    assert not ecartees, ecartees
    r.update({"themes": themes, "redaction": "ia"})
    reglement.valider(data, "mimosas", r, date(2027, 4, 5))
    print(f"Données de démonstration prêtes dans {DEMO.relative_to(RACINE)}/")
    print("Lancer :  MOTEUR_DATA_DIR=demo/data MOTEUR_OUT_DIR=demo/out INTERFACE_MOT_DE_PASSE=demo "
          "uvicorn moteur.api:app --reload")
    print("Ouvrir :  http://localhost:8000/app/mimosas?au=2027-05-20   (mot de passe : demo)")


if __name__ == "__main__":
    main()
