"""
banque.py — Import des relevés bancaires du compte séparé du syndicat.

Chaque banque exporte son relevé CSV à sa façon : séparateur « ; » ou « , », montants
à virgule (« 1 234,56 »), dates jj/mm/aaaa, lignes d'introduction avant l'en-tête,
une colonne « Montant » signée ou deux colonnes « Débit » et « Crédit ». Ce module
reconnaît ces variantes sans configuration.

Les opérations sont conservées dans data/<copro>/banque/operations.json. Un relevé
importé deux fois, ou deux relevés qui se chevauchent, ne créent pas de doublon :
une opération est identifiée par sa date, son libellé, son montant et son rang
parmi les opérations identiques du même relevé (deux virements de 225 € le même
jour avec le même libellé restent deux opérations).

Le fichier importé n'est jamais modifié ; une copie est archivée dans
data/<copro>/banque/releves/.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import unicodedata
from collections import Counter
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

TAILLE_MAX = 5 * 1024 * 1024        # un relevé de petite copro fait quelques Ko
ENCODAGES = ("utf-8-sig", "cp1252", "latin-1")
MOTS = {
    "date": ("date operation", "date op", "date comptable", "date", "jour"),
    "date_valeur": ("date de valeur", "date valeur", "valeur"),
    "libelle": ("libelle", "intitule", "description", "detail", "operation", "nature", "motif"),
    "montant": ("montant", "somme", "amount"),
    "debit": ("debit", "sortie", "depense"),
    "credit": ("credit", "entree", "recette"),
}


class ReleveInvalide(ValueError):
    pass


def _sans_accent(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", t) if unicodedata.category(c) != "Mn").lower().strip()


def _montant(texte: str) -> Decimal | None:
    """'1 234,56', '-12,00 €', '+225.00', '(45,00)' -> Decimal. Vide -> None."""
    t = (texte or "").strip().replace(" ", "").replace(" ", "").replace(" ", "").replace("€", "")
    t = t.replace("EUR", "").replace("eur", "")
    if not t:
        return None
    negatif = t.startswith("(") and t.endswith(")")
    t = t.strip("()")
    if "," in t and "." in t:                       # 1.234,56 ou 1,234.56
        t = t.replace(".", "").replace(",", ".") if t.rfind(",") > t.rfind(".") else t.replace(",", "")
    else:
        t = t.replace(",", ".")
    try:
        m = Decimal(t)
    except InvalidOperation:
        return None
    return -m if negatif else m


def _date(texte: str) -> date | None:
    t = (texte or "").strip()
    for f in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d", "%d-%m-%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(t, f).date()
        except ValueError:
            continue
    return None


def _decoder(contenu: bytes) -> str:
    for enc in ENCODAGES:
        try:
            return contenu.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ReleveInvalide("Encodage du fichier non reconnu")


def _colonnes(entete: list[str]) -> dict[str, int]:
    """Associe chaque rôle (date, libellé, montant, débit, crédit) à une colonne."""
    noms = [_sans_accent(c) for c in entete]
    res: dict[str, int] = {}
    # date de valeur d'abord, pour ne pas la confondre avec la date d'opération
    for role in ("date_valeur", "date", "debit", "credit", "montant", "libelle"):
        for mot in MOTS[role]:
            for i, n in enumerate(noms):
                if i in res.values():
                    continue
                if n == mot or n.startswith(mot) or (len(mot) > 4 and mot in n):
                    res[role] = i
                    break
            if role in res:
                break
    return res


def lire_releve(contenu: bytes) -> dict:
    """Lit un relevé CSV. Renvoie les opérations et un rapport sur ce qui a été ignoré."""
    if len(contenu) > TAILLE_MAX:
        raise ReleveInvalide("Fichier trop volumineux pour un relevé")
    texte = _decoder(contenu)
    lignes = texte.splitlines()
    echantillon = "\n".join(lignes[:30])
    sep = max((";", ",", "\t"), key=lambda s: echantillon.count(s))
    rangees = list(csv.reader(io.StringIO(texte), delimiter=sep))
    # L'en-tête est la première ligne qui nomme une date, un libellé et un montant (ou débit/crédit)
    i_entete, cols = None, {}
    for i, r in enumerate(rangees[:40]):
        c = _colonnes(r)
        if "date" in c and "libelle" in c and ("montant" in c or ("debit" in c and "credit" in c)):
            i_entete, cols = i, c
            break
    if i_entete is None:
        raise ReleveInvalide("En-tête introuvable : il faut au moins une date, un libellé et un montant "
                             "(ou des colonnes débit et crédit)")
    ops, ignorees = [], []
    for n, r in enumerate(rangees[i_entete + 1:], start=i_entete + 2):
        if not any(x.strip() for x in r):
            continue
        get = lambda role: r[cols[role]] if role in cols and cols[role] < len(r) else ""  # noqa: E731
        d = _date(get("date"))
        if "montant" in cols:
            m = _montant(get("montant"))
        else:
            deb, cred = _montant(get("debit")), _montant(get("credit"))
            m = (cred or Decimal("0")) - abs(deb or Decimal("0")) if (deb is not None or cred is not None) else None
        lib = " ".join(get("libelle").split())
        if d is None or m is None:
            ignorees.append({"ligne": n, "contenu": sep.join(r)[:120],
                             "raison": "date illisible" if d is None else "montant illisible"})
            continue
        ops.append({"date": d.isoformat(), "libelle": lib, "montant": str(m.quantize(Decimal("0.01")))})
    if not ops:
        raise ReleveInvalide("Aucune opération lisible dans le fichier")
    return {"separateur": sep, "colonnes": {k: rangees[i_entete][v] for k, v in cols.items()},
            "operations": ops, "ignorees": ignorees}


def _identifiants(ops: list[dict]) -> list[str]:
    rang: Counter = Counter()
    ids = []
    for o in ops:
        cle = (o["date"], o["libelle"], o["montant"])
        rang[cle] += 1
        brut = "|".join(cle) + f"|{rang[cle]}"
        ids.append("OP" + hashlib.sha1(brut.encode()).hexdigest()[:10])
    return ids


# --------------------------------------------------------------------------- #
# Stockage
# --------------------------------------------------------------------------- #
def dossier_banque(racine: Path, copro: str) -> Path:
    return Path(racine) / copro / "banque"


def charger_operations(racine: Path, copro: str) -> list[dict]:
    f = dossier_banque(racine, copro) / "operations.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.is_file() else []


def sauver_operations(racine: Path, copro: str, ops: list[dict]) -> None:
    d = dossier_banque(racine, copro)
    d.mkdir(parents=True, exist_ok=True)
    ops.sort(key=lambda o: (o["date"], o["id"]))
    (d / "operations.json").write_text(json.dumps(ops, ensure_ascii=False, indent=1), encoding="utf-8")


def importer(racine: Path, copro: str, contenu: bytes, nom_fichier: str = "releve.csv",
             aujourd_hui: date | None = None) -> dict:
    """Ajoute les opérations nouvelles d'un relevé. Les opérations déjà connues sont ignorées."""
    lu = lire_releve(contenu)
    jour = aujourd_hui or date.today()
    existantes = charger_operations(racine, copro)
    connues = {o["id"] for o in existantes}
    nouvelles = []
    for o, oid in zip(lu["operations"], _identifiants(lu["operations"])):
        if oid in connues:
            continue
        nouvelles.append({"id": oid, **o, "importee_le": jour.isoformat(), "releve": nom_fichier})
        connues.add(oid)
    sauver_operations(racine, copro, existantes + nouvelles)
    archive = dossier_banque(racine, copro) / "releves"
    archive.mkdir(parents=True, exist_ok=True)
    sur = re.sub(r"[^A-Za-z0-9._-]", "_", Path(nom_fichier).name)[:80] or "releve.csv"
    (archive / f"{jour:%Y%m%d}_{sur}").write_bytes(contenu)
    dates = [o["date"] for o in lu["operations"]]
    return {"fichier": nom_fichier, "lues": len(lu["operations"]), "nouvelles": len(nouvelles),
            "deja_connues": len(lu["operations"]) - len(nouvelles), "ignorees": lu["ignorees"],
            "periode": {"du": min(dates), "au": max(dates)}, "colonnes": lu["colonnes"]}
