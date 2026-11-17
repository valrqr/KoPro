"""Tests de fumée du moteur : python -m pytest tests/  (ou python tests/test_api.py)"""
import os
import tempfile
from pathlib import Path

os.environ["MOTEUR_TOKEN"] = "test-token"
os.environ["MOTEUR_OUT_DIR"] = tempfile.mkdtemp()
os.environ["MOTEUR_DATA_DIR"] = tempfile.mkdtemp()

import sys  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fastapi.testclient import TestClient  # noqa: E402
from moteur.api import app  # noqa: E402

c = TestClient(app)
H = {"X-API-Key": "test-token"}
BASE = "/copros/mimosas/appels/2027/T1"


def test_sante():
    assert c.get("/sante").json() == {"statut": "ok"}


def test_cle_obligatoire():
    assert c.get("/copros").status_code == 401
    assert c.get("/copros", headers={"X-API-Key": "mauvaise"}).status_code == 401


def test_fiche_valide_et_alertes():
    r = c.get("/copros/mimosas/fiche", params={"au": "2027-02-15"}, headers=H).json()
    assert r["valide"] and len(r["alertes"]) == 4


def test_generation_complete():
    r = c.post(BASE, headers=H)
    assert r.status_code == 200
    d = r.json()
    assert d["statut"] == "projet" and d["nb_avis"] == 14 and d["nb_courriers"] == 2
    assert d["total"] == "5 250,00 €"
    assert c.post(BASE, params={"final": "true"}, headers=H).json()["statut"] == "valide"
    assert c.get(BASE, headers=H).json()["statut"] == "valide"
    pdf = c.get(f"{BASE}/avis/CP14", headers=H)
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
    assert c.get(f"{BASE}/recap", headers=H).content[:4] == b"%PDF"


def test_garde_fous():
    assert c.post("/copros/inconnue/appels/2027/T1", headers=H).status_code == 404
    assert c.post("/copros/mimosas/appels/2026/T1", headers=H).status_code == 409
    assert c.post("/copros/mimosas/appels/2027/T5", headers=H).status_code == 422
    assert c.get("/copros/..%2Fsecret/fiche", headers=H).status_code in (404, 422)
    assert c.get(f"{BASE}/avis/CP99", headers=H).status_code == 404


def test_echeancier_copro():
    r = c.get("/copros/mimosas/echeancier", params={"au": "2027-02-15"}, headers=H).json()
    assert r["syndic"]["email"] == "camille.durand@exemple.fr"
    assert r["nb_alertes"] == 4
    types = [a["type"] for a in r["alertes"]]
    assert types.count("contrat") == 2 and types.count("diagnostic") == 2
    jardin = next(a for a in r["alertes"] if a["titre"] == "Entretien du jardin")
    assert "sans reconduction" in jardin["message"] and jardin["date"] == "2027-03-31"
    ascenseur = next(a for a in r["alertes"] if a["titre"] == "Maintenance ascenseur")
    assert ascenseur["date"] == "2027-03-30"          # 30/06 - 3 mois, au jour près


def test_echeancier_ag_et_urgence():
    r = c.get("/copros/mimosas/echeancier", params={"au": "2027-06-01"}, headers=H).json()
    ag = next(a for a in r["alertes"] if a["type"] == "ag")
    assert ag["date"] == "2027-06-30" and ag["niveau"] == "urgent" and "09/06/2027" in ag["message"]
    assert r["alertes"][0]["niveau"] == "urgent"     # les urgences en premier


def test_echeancier_global():
    r = c.get("/echeancier", params={"au": "2027-02-15", "horizon": 60}, headers=H).json()
    assert r["nb_copros"] == 1 and r["rapports"][0]["copro"] == "mimosas"
    assert c.get("/echeancier", params={"horizon": 0}, headers=H).status_code == 422


PDF = b"%PDF-1.4 devis de test"


def _nouveau_dossier(**kw):
    corps = {"titre": "Remplacement de la porte du garage", "nature": "reparation", **kw}
    r = c.post("/copros/mimosas/travaux", json=corps, headers=H)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _devis(did, entreprise, ht, validite="2027-12-31", pdf=PDF):
    files = {"fichier": ("devis.pdf", pdf, "application/pdf")} if pdf else None
    return c.post(f"/copros/mimosas/travaux/{did}/devis", headers=H, files=files,
                  data={"entreprise": entreprise, "montant_ht": ht, "taux_tva": "10",
                        "delai_jours": "15", "valide_jusqu_au": validite})


def test_travaux_comparatif_et_aiguillage():
    did = _nouveau_dossier()
    assert _devis(did, "Portes Azur", "3200").status_code == 201
    assert _devis(did, "Fermetures Niçoises", "2 780,00").status_code == 201
    assert _devis(did, "Menuiserie du Port", "4150", validite="2027-05-20", pdf=None).status_code == 201
    r = c.get(f"/copros/mimosas/travaux/{did}", params={"au": "2027-06-01"}, headers=H).json()
    comp = r["comparatif"]
    assert [d["id"] for d in comp["devis"]] == ["D2", "D1", "D3"]          # tri par prix TTC
    assert comp["reference_ttc"] == "3058.00" and comp["devis"][0]["moins_cher"]
    assert comp["devis"][2]["expire"]
    assert sum(float(q["parts"]["D2"]) for q in comp["quotes_parts"]) == 3058.00   # au centime
    assert any("expiré" in a for a in comp["alertes"]) and any("PDF" in a for a in comp["alertes"])
    aig = r["aiguillage"]
    assert aig["circuit"] == "assemblee_generale" and aig["notes"]          # 3 058 € > seuil CS 1 500 €
    assert c.get(f"/copros/mimosas/travaux/{did}/devis/D1/pdf", headers=H).content == PDF
    pdf = c.get(f"/copros/mimosas/travaux/{did}/comparatif.pdf", params={"au": "2027-06-01"}, headers=H)
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
    assert any(x["id"] == did for x in c.get("/copros/mimosas/travaux", headers=H).json())


def test_travaux_circuits():
    petit = _nouveau_dossier(titre="Remplacer une ampoule du hall", nature="entretien")
    _devis(petit, "Élec Express", "90")
    assert c.get(f"/copros/mimosas/travaux/{petit}", headers=H).json()["aiguillage"]["circuit"] == "syndic"
    urgent = _nouveau_dossier(titre="Fuite sur la colonne d'eau", urgence=True)
    _devis(urgent, "Plomberie 06", "1800")
    assert c.get(f"/copros/mimosas/travaux/{urgent}", headers=H).json()["aiguillage"]["circuit"] == "syndic_urgence"
    seul = _nouveau_dossier(titre="Ravalement de la façade", nature="amelioration")
    _devis(seul, "Façades Sud", "20000")
    r = c.get(f"/copros/mimosas/travaux/{seul}", headers=H).json()
    assert any("Mise en concurrence" in a for a in r["comparatif"]["alertes"])


def test_travaux_garde_fous():
    did = _nouveau_dossier(titre="Test garde-fous")
    assert _devis(did, "Faux PDF", "100", pdf=b"MZ executable").status_code == 422
    assert c.post(f"/copros/mimosas/travaux/{did}/devis", headers=H,
                  data={"entreprise": "X", "montant_ht": "-5"}).status_code == 422
    assert c.post(f"/copros/mimosas/travaux/{did}/devis", headers=H,
                  data={"entreprise": "X", "montant_ht": "100", "taux_tva": "7"}).status_code == 422
    assert c.post("/copros/mimosas/travaux", json={"titre": "Test", "nature": "magie"}, headers=H).status_code == 422
    assert c.get("/copros/mimosas/travaux/../../etc", headers=H).status_code == 404
    assert c.get("/copros/mimosas/travaux/2027-999-inconnu", headers=H).status_code == 404
    assert c.get(f"/copros/mimosas/travaux/{did}/comparatif.pdf", headers=H).status_code == 409


if __name__ == "__main__":
    for nom, f in list(globals().items()):
        if nom.startswith("test_"):
            f(); print("✓", nom)
