"""Tests de fumée du moteur : python -m pytest tests/  (ou python tests/test_api.py)"""
import os
import tempfile
from pathlib import Path

os.environ["MOTEUR_TOKEN"] = "test-token"
os.environ["MOTEUR_OUT_DIR"] = tempfile.mkdtemp()

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


if __name__ == "__main__":
    for nom, f in list(globals().items()):
        if nom.startswith("test_"):
            f(); print("✓", nom)
