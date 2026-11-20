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
    print(f"Données de démonstration prêtes dans {DEMO.relative_to(RACINE)}/")
    print("Lancer :  MOTEUR_DATA_DIR=demo/data MOTEUR_OUT_DIR=demo/out INTERFACE_MOT_DE_PASSE=demo "
          "uvicorn moteur.api:app --reload")
    print("Ouvrir :  http://localhost:8000/app/mimosas?au=2027-05-20   (mot de passe : demo)")


if __name__ == "__main__":
    main()
