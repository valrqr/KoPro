"""
impayes.py — Rapprochement bancaire, comptes des copropriétaires et relances.

Le principe reste celui de Mitoyen : l'agent prépare, le syndic valide et signe.

1. RAPPROCHEMENT. Chaque crédit du relevé est rattaché à un copropriétaire :
   - automatiquement quand c'est sûr : référence de virement de l'avis d'appel
     (même tronquée ou sans tirets), remise de prélèvements SEPA dont le total
     correspond exactement, rejet de prélèvement portant la référence du mandat ;
   - sous forme de PROPOSITION à confirmer quand c'est probable : nom du
     copropriétaire dans le libellé, ou montant qui ne correspond qu'à un seul
     copropriétaire (un chèque remis en banque, par exemple).
   Une proposition n'est jamais comptée tant que le syndic ne l'a pas confirmée, et
   bloque les relances du copropriétaire concerné : on ne relance pas quelqu'un qui
   a peut-être payé.

2. COMPTES. Les sommes dues sont les appels de fonds VALIDÉS (envoi.json) et les
   ajustements saisis par le syndic (solde antérieur, appel de travaux, frais de
   recouvrement imputables, avoir). Les paiements s'imputent sur les échéances les
   plus anciennes d'abord.

3. RELANCES GRADUÉES, toujours dans l'ordre, jamais en sautant une étape :
   1. rappel amiable (proposé 15 jours après l'échéance) ;
   2. relance (30 jours après l'échéance, au moins 15 jours après le rappel) ;
   3. mise en demeure (45 jours après l'échéance, au moins 15 jours après la
      relance), à envoyer en recommandé. Elle détaille la nature et le montant de
      chaque provision réclamée (Cass. 3e civ., avis du 12 décembre 2024,
      n° 24-70.007), fait courir les intérêts au taux légal (décret du 17 mars 1967,
      art. 36) et rend imputables au débiteur les frais nécessaires engagés ensuite
      (loi du 10 juillet 1965, art. 10-1) ;
   puis, 30 jours après la présentation de la mise en demeure, Mitoyen signale que
   la procédure de l'article 19-2 devient possible. Il ne la lance pas.

⚠️ Aide à la gestion, pas un conseil juridique. En cas de doute, l'ADIL du
département renseigne gratuitement.
"""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import banque

CENT = Decimal("0.01")


def _trimestre(n: int) -> str:
    return "1er" if int(n) == 1 else f"{n}e"
NIVEAUX = {1: "rappel", 2: "relance", 3: "mise_en_demeure"}
LIBELLES = {1: "Rappel amiable", 2: "Relance", 3: "Mise en demeure"}
DEFAUTS = {
    "seuil": 5,                     # en dessous, pas de relance (arrondis, frais bancaires)
    "rappel_jours": 15,             # jours après l'échéance
    "relance_jours": 30,
    "mise_en_demeure_jours": 45,
    "intervalle_min_jours": 15,     # entre deux relances
    "delai_mise_en_demeure_jours": 30,   # art. 19-2
    "fenetre_prelevement_jours": 10,
}
# Taux de l'intérêt légal « autres cas » (le créancier est le syndicat, pas une
# personne physique). Fixés chaque semestre par arrêté : à mettre à jour.
TAUX_LEGAL = {
    "2025-S2": Decimal("2.76"),
    "2026-S1": Decimal("2.62"),
    "2026-S2": Decimal("2.75"),     # arrêté du 26 juin 2026
}
SEP = r"[\s\-_./]*"
RE_REMISE_PRLV = re.compile(r"\bREMISE\b.*\bPRLV\b|\bPRLV\b.*\bREMISE\b|\bREMISE\b.*\bPRELEVEMENTS?\b")
RE_REJET = re.compile(r"\bREJET\b|\bIMPAYE\b|\bRETOUR\b|\bREVERSEMENT\b")
AVERTISSEMENT = ("Aide à la gestion préparée par l'assistant du syndic. Elle ne constitue pas un conseil juridique ; "
                 "en cas de doute, l'ADIL du département renseigne gratuitement.")


class RecouvrementInvalide(ValueError):
    pass


def _d(x) -> Decimal:
    return Decimal(str(x)).quantize(CENT)


def _norm(t: str) -> str:
    t = "".join(c for c in unicodedata.normalize("NFD", t or "") if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", t.upper()).strip()


def _motif(texte: str) -> str:
    """'MIMOSAS-CP05' -> motif tolérant aux espaces, tirets et points supprimés ou changés."""
    mots = re.findall(r"[A-Z0-9]+", _norm(texte))
    return SEP.join(re.escape(m) for m in mots)


def regles(fiche: dict) -> dict:
    return {**DEFAUTS, **(fiche.get("recouvrement") or {})}


# --------------------------------------------------------------------------- #
# 1. Ce qui est dû : appels validés et ajustements
# --------------------------------------------------------------------------- #
def dossier_recouvrement(racine: Path, copro: str) -> Path:
    return Path(racine) / copro / "recouvrement"


def _lire(f: Path, defaut):
    return json.loads(f.read_text(encoding="utf-8")) if f.is_file() else defaut


def _ecrire(f: Path, contenu) -> None:
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(contenu, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


def charger_ajustements(racine: Path, copro: str) -> list[dict]:
    return _lire(dossier_recouvrement(racine, copro) / "ajustements.json", [])


def ajouter_ajustement(fiche: dict, racine: Path, copro: str, cid: str, jour: date, libelle: str,
                       montant, nature: str = "autre") -> dict:
    """Une somme due (montant positif) ou un avoir (négatif) hors appels trimestriels."""
    if cid not in {c["id"] for c in fiche["coproprietaires"]}:
        raise RecouvrementInvalide(f"Copropriétaire inconnu : {cid}")
    if nature not in ("solde_anterieur", "travaux", "frais_recouvrement", "avoir", "autre"):
        raise RecouvrementInvalide("Nature inconnue")
    m = _d(montant)
    if m == 0:
        raise RecouvrementInvalide("Montant nul")
    aj = charger_ajustements(racine, copro)
    a = {"id": f"A{len(aj) + 1:04d}", "coproprietaire": cid, "date": jour.isoformat(), "libelle": libelle[:120],
         "montant": str(m), "nature": nature}
    _ecrire(dossier_recouvrement(racine, copro) / "ajustements.json", aj + [a])
    return a


def echeances(racine_appels: Path, racine_data: Path, copro: str) -> list[dict]:
    """Toutes les sommes dues, appel par appel et copropriétaire par copropriétaire."""
    res = []
    base = Path(racine_appels) / copro
    if base.is_dir():
        for f in sorted(base.glob("*-T*/envoi.json")):
            e = json.loads(f.read_text(encoding="utf-8"))
            if e.get("statut") != "valide":
                continue                         # un projet n'a pas été envoyé : rien n'est dû
            for a in e["avis"]:
                if "montant_num" not in a:
                    continue                     # appel généré avant le module de recouvrement
                res.append({"id": f"{e['exercice']}-T{e['trimestre']}-{a['coproprietaire']}",
                            "coproprietaire": a["coproprietaire"], "type": "appel",
                            "libelle": f"Appel de fonds {e['exercice']}, {_trimestre(e['trimestre'])} trimestre",
                            "date": e["date_exigibilite"], "montant": _d(a["montant_num"]),
                            "detail": [{"rubrique": x["rubrique"], "montant": _d(x["montant"])} for x in a["detail"]],
                            "reference": a["reference"]})
    for a in charger_ajustements(racine_data, copro):
        m = _d(a["montant"])
        if m > 0:
            res.append({"id": a["id"], "coproprietaire": a["coproprietaire"], "type": a["nature"],
                        "libelle": a["libelle"], "date": a["date"], "montant": m,
                        "detail": [{"rubrique": a["libelle"], "montant": m}], "reference": None})
    return sorted(res, key=lambda x: (x["date"], x["id"]))


# --------------------------------------------------------------------------- #
# 2. Rapprochement
# --------------------------------------------------------------------------- #
def charger_affectations(racine: Path, copro: str) -> dict:
    return _lire(banque.dossier_banque(racine, copro) / "affectations.json", {})


def affecter(fiche: dict, racine: Path, copro: str, op_id: str, statut: str, cid: str | None = None,
             jour: date | None = None) -> dict:
    """Décision du syndic sur une opération : 'confirme' (avec le copropriétaire) ou 'ignore'
    (opération sans rapport avec les charges), ou 'annule' pour revenir au rapprochement automatique."""
    if op_id not in {o["id"] for o in banque.charger_operations(racine, copro)}:
        raise RecouvrementInvalide("Opération inconnue")
    aff = charger_affectations(racine, copro)
    if statut == "annule":
        aff.pop(op_id, None)
    elif statut == "confirme":
        if cid not in {c["id"] for c in fiche["coproprietaires"]}:
            raise RecouvrementInvalide("Indiquer le copropriétaire qui a payé")
        aff[op_id] = {"statut": "confirme", "coproprietaire": cid, "le": (jour or date.today()).isoformat()}
    elif statut == "ignore":
        aff[op_id] = {"statut": "ignore", "le": (jour or date.today()).isoformat()}
    else:
        raise RecouvrementInvalide("Statut attendu : confirme, ignore ou annule")
    _ecrire(banque.dossier_banque(racine, copro) / "affectations.json", aff)
    return aff.get(op_id, {"statut": "automatique"})


def _motifs_copros(fiche: dict) -> dict:
    """Pour chaque copropriétaire : référence(s) de virement, mandat SEPA, mots du nom."""
    tmpl = fiche["appels_de_fonds"]["reference_virement"]
    ids = sorted((c["id"] for c in fiche["coproprietaires"]), key=len, reverse=True)
    morceaux = re.split(r"(\{[a-z_]+\})", tmpl)
    rx = ""
    for m in morceaux:
        if m == "{exercice}":
            rx += r"(?P<ex>\d{4})"
        elif m == "{trimestre}":
            rx += r"(?P<tr>[1-4])"
        elif m == "{coproprietaire}":
            rx += "(?P<cid>" + "|".join(re.escape(i) for i in ids) + r")(?![0-9])"
        elif m:
            mots = re.findall(r"[A-Z0-9]+", _norm(m))
            rx += SEP + SEP.join(re.escape(x) for x in mots) + SEP if mots else SEP
    par_cid = {}
    for c in fiche["coproprietaires"]:
        rum = ((c.get("mandat_sepa") or {}).get("rum"))
        par_cid[c["id"]] = {
            "rum": re.compile(_motif(rum) + r"(?![0-9])") if rum else None,
            "nom": [w for w in re.findall(r"[A-Z0-9]+", _norm(c["nom"])) if len(w) >= 3],
            "prelevement": c.get("paiement") == "prelevement" and bool(rum),
        }
    return {"reference": re.compile(rx), "copros": par_cid}


def _par_nom(motifs: dict, lib: str) -> list[str]:
    mots = set(re.findall(r"[A-Z0-9]+", lib))
    return [cid for cid, m in motifs["copros"].items() if m["nom"] and all(w in mots for w in m["nom"])]


def rapprocher(fiche: dict, operations: list[dict], dues: list[dict], affectations: dict) -> list[dict]:
    """Pour chaque opération : à qui elle se rattache, comment, et si c'est sûr.
    statut : automatique | confirme | propose | non_rapproche | ignore | depense."""
    motifs = _motifs_copros(fiche)
    r = regles(fiche)
    montants_dus: dict[Decimal, set] = {}
    for e in dues:
        montants_dus.setdefault(e["montant"], set()).add(e["coproprietaire"])
    res = []
    for o in operations:
        m = _d(o["montant"])
        lib = _norm(o["libelle"])
        base = {"operation": o["id"], "date": o["date"], "libelle": o["libelle"], "montant": m}
        dec = affectations.get(o["id"])
        if dec:
            if dec["statut"] == "ignore":
                res.append({**base, "statut": "ignore", "parts": [], "methode": "décision du syndic"})
            else:
                res.append({**base, "statut": "confirme", "parts": [{"coproprietaire": dec["coproprietaire"], "montant": m}],
                            "methode": "confirmé par le syndic"})
            continue
        ref = motifs["reference"].search(lib)
        rum = [cid for cid, x in motifs["copros"].items() if x["rum"] and x["rum"].search(lib)]
        if m < 0:
            if RE_REJET.search(lib):
                qui = rum or ([ref.group("cid")] if ref else [])
                if len(qui) == 1:
                    res.append({**base, "statut": "automatique", "parts": [{"coproprietaire": qui[0], "montant": m}],
                                "methode": "rejet de prélèvement (mandat ou référence)"})
                    continue
                noms = _par_nom(motifs, lib)
                if len(noms) == 1:
                    res.append({**base, "statut": "propose", "parts": [{"coproprietaire": noms[0], "montant": m}],
                                "methode": "rejet de paiement, nom dans le libellé"})
                    continue
            res.append({**base, "statut": "depense", "parts": [], "methode": ""})
            continue
        if ref:
            res.append({**base, "statut": "automatique", "parts": [{"coproprietaire": ref.group("cid"), "montant": m}],
                        "methode": f"référence de l'avis {ref.group('ex')} T{ref.group('tr')}"})
            continue
        if len(rum) == 1:
            res.append({**base, "statut": "automatique", "parts": [{"coproprietaire": rum[0], "montant": m}],
                        "methode": "référence du mandat de prélèvement"})
            continue
        if RE_REMISE_PRLV.search(lib):
            jour = date.fromisoformat(o["date"])
            fen = timedelta(days=r["fenetre_prelevement_jours"])
            dus = [e for e in dues if motifs["copros"].get(e["coproprietaire"], {}).get("prelevement")
                   and e["type"] == "appel" and abs(date.fromisoformat(e["date"]) - jour) <= fen]
            if dus and sum((e["montant"] for e in dus), Decimal("0")) == m:
                res.append({**base, "statut": "automatique",
                            "parts": [{"coproprietaire": e["coproprietaire"], "montant": e["montant"]} for e in dus],
                            "methode": f"remise de {len(dus)} prélèvement(s) SEPA, total exact"})
                continue
        noms = _par_nom(motifs, lib)
        if len(noms) == 1:
            ok = noms[0] in montants_dus.get(m, set())
            res.append({**base, "statut": "propose", "parts": [{"coproprietaire": noms[0], "montant": m}],
                        "methode": "nom et montant d'un appel" if ok else "nom dans le libellé (montant différent)"})
            continue
        candidats = montants_dus.get(m, set())
        if len(candidats) == 1:
            res.append({**base, "statut": "propose", "parts": [{"coproprietaire": next(iter(candidats)), "montant": m}],
                        "methode": "montant d'un seul copropriétaire (chèque, virement sans référence ?)"})
            continue
        res.append({**base, "statut": "non_rapproche", "parts": [],
                    "methode": f"{len(candidats)} copropriétaires doivent ce montant" if candidats else "aucune piste"})
    return res


# --------------------------------------------------------------------------- #
# 3. Comptes des copropriétaires
# --------------------------------------------------------------------------- #
def comptes(fiche: dict, dues: list[dict], rapprochement: list[dict], ajustements: list[dict], au: date) -> dict:
    """Solde de chaque copropriétaire à une date, avec ses échéances impayées (imputation
    des paiements sur les échéances les plus anciennes)."""
    noms = {c["id"]: c["nom"] for c in fiche["coproprietaires"]}
    res = {}
    for cid in noms:
        mes_dues = [e for e in dues if e["coproprietaire"] == cid]
        paiements = [{"date": r["date"], "montant": p["montant"], "operation": r["operation"], "libelle": r["libelle"],
                      "methode": r["methode"]}
                     for r in rapprochement if r["statut"] in ("automatique", "confirme") and r["date"] <= au.isoformat()
                     for p in r["parts"] if p["coproprietaire"] == cid]
        avoirs = [_d(-_d(a["montant"])) for a in ajustements if a["coproprietaire"] == cid and _d(a["montant"]) < 0
                  and a["date"] <= au.isoformat()]
        en_attente = [{"operation": r["operation"], "date": r["date"], "libelle": r["libelle"], "montant": p["montant"],
                       "methode": r["methode"]}
                      for r in rapprochement if r["statut"] == "propose" for p in r["parts"] if p["coproprietaire"] == cid]
        verse = sum((p["montant"] for p in paiements), Decimal("0")) + sum(avoirs, Decimal("0"))
        reste_a_imputer = verse
        lignes = []
        for e in mes_dues:
            impute = min(max(reste_a_imputer, Decimal("0")), e["montant"])
            reste_a_imputer -= impute
            exigible = e["date"] <= au.isoformat()
            lignes.append({"echeance": e["id"], "libelle": e["libelle"], "date": e["date"], "montant": e["montant"],
                           "paye": impute, "reste": e["montant"] - impute, "exigible": exigible,
                           "retard_jours": (au - date.fromisoformat(e["date"])).days if exigible and e["montant"] > impute else 0,
                           "detail": e["detail"]})
        impayes = [l for l in lignes if l["exigible"] and l["reste"] > 0]
        du_exigible = sum((l["montant"] for l in lignes if l["exigible"]), Decimal("0"))
        res[cid] = {"coproprietaire": cid, "nom": noms[cid], "du_exigible": du_exigible, "verse": verse,
                    "solde": du_exigible - verse,          # > 0 : doit ; < 0 : d'avance
                    "impaye": sum((l["reste"] for l in impayes), Decimal("0")),
                    "retard_max_jours": max((l["retard_jours"] for l in impayes), default=0),
                    "echeances": lignes, "impayes": impayes, "paiements": paiements, "en_attente": en_attente}
    return res


def etat(fiche: dict, racine_appels: Path, racine_data: Path, copro: str, au: date | None = None) -> dict:
    """Vue complète : rapprochement, comptes, relances à préparer."""
    jour = au or date.today()
    ops = banque.charger_operations(racine_data, copro)
    dues = echeances(racine_appels, racine_data, copro)
    rap = rapprocher(fiche, ops, dues, charger_affectations(racine_data, copro))
    aj = charger_ajustements(racine_data, copro)
    cpt = comptes(fiche, dues, rap, aj, jour)
    reg = charger_relances(racine_data, copro)
    propositions = a_relancer(fiche, cpt, reg, jour)
    debiteurs = [c for c in cpt.values() if c["impaye"] > 0]
    return {
        "au": jour.isoformat(),
        "releve": {"nb_operations": len(ops), "du": ops[0]["date"] if ops else None, "au": ops[-1]["date"] if ops else None},
        "rapprochement": {s: sum(1 for r in rap if r["statut"] == s)
                          for s in ("automatique", "confirme", "propose", "non_rapproche", "ignore", "depense")},
        "a_confirmer": [r for r in rap if r["statut"] == "propose"],
        "non_rapproches": [r for r in rap if r["statut"] == "non_rapproche"],
        "total_impayes": sum((c["impaye"] for c in debiteurs), Decimal("0")),
        # part des impayés de copropriétaires dont un paiement probable attend confirmation
        "dont_a_confirmer": sum((c["impaye"] for c in debiteurs if c["en_attente"]), Decimal("0")),
        "nb_debiteurs": len(debiteurs),
        # Ces deux chiffres alimentent la fiche synthétique (section comptes_approuves)
        "nb_debiteurs_plus_300": sum(1 for c in debiteurs if c["impaye"] > 300 and not c["en_attente"]),
        "comptes": cpt, "relances": propositions, "operations": rap,
    }


# --------------------------------------------------------------------------- #
# 4. Relances
# --------------------------------------------------------------------------- #
def charger_relances(racine: Path, copro: str) -> list[dict]:
    return _lire(dossier_recouvrement(racine, copro) / "relances.json", [])


def _sauver_relances(racine: Path, copro: str, reg: list[dict]) -> None:
    _ecrire(dossier_recouvrement(racine, copro) / "relances.json", reg)


def date_limite_mise_en_demeure(rel: dict, fiche: dict) -> date | None:
    """30 jours à compter du lendemain de la première présentation (à défaut, de l'envoi)."""
    depart = rel.get("presentee_le") or rel.get("envoyee_le")
    if not depart:
        return None
    return date.fromisoformat(depart) + timedelta(days=regles(fiche)["delai_mise_en_demeure_jours"])


def a_relancer(fiche: dict, cpt: dict, reg: list[dict], au: date) -> list[dict]:
    """Pour chaque copropriétaire en retard : où il en est, et la relance que Mitoyen propose."""
    r = regles(fiche)
    seuil_jours = {1: r["rappel_jours"], 2: r["relance_jours"], 3: r["mise_en_demeure_jours"]}
    res = []
    for cid, c in cpt.items():
        if c["impaye"] < _d(r["seuil"]):
            continue
        ouvertes = {l["echeance"] for l in c["impayes"]}
        # l'historique qui compte : les relances qui portaient sur des échéances encore impayées
        histo = [x for x in reg if x["coproprietaire"] == cid and x["statut"] != "annulee"
                 and ouvertes & {e["echeance"] for e in x["echeances"]}]
        envoyees = sorted((x for x in histo if x["statut"] == "envoyee"), key=lambda x: x["envoyee_le"])
        preparees = [x for x in histo if x["statut"] == "preparee"]
        dernier = envoyees[-1] if envoyees else None
        niveau = dernier["niveau"] if dernier else 0
        p = {"coproprietaire": cid, "nom": c["nom"], "impaye": c["impaye"], "retard_max_jours": c["retard_max_jours"],
             "derniere_relance": ({"id": dernier["id"], "niveau": LIBELLES[dernier["niveau"]],
                                   "envoyee_le": dernier["envoyee_le"]} if dernier else None)}
        if c["en_attente"]:
            res.append({**p, "etat": "bloque", "prochaine": None,
                        "message": "Un paiement possible attend votre confirmation : à vérifier avant toute relance."})
            continue
        if preparees:
            res.append({**p, "etat": "a_envoyer", "prochaine": None, "relance": preparees[-1]["id"],
                        "message": f"{LIBELLES[preparees[-1]['niveau']]} préparée, pas encore marquée envoyée."})
            continue
        if niveau == 3:
            limite = date_limite_mise_en_demeure(dernier, fiche)
            vises = {x["echeance"] for x in dernier["echeances"]}
            principal = sum((l["reste"] for l in c["impayes"] if l["echeance"] in vises), Decimal("0"))
            depart = date.fromisoformat(dernier.get("presentee_le") or dernier["envoyee_le"])
            it = interets(principal, depart, au)
            p["interets"] = {"montant": it["montant"], "depuis": depart.isoformat(), "taux_a_jour": it["taux_a_jour"],
                             "note": "Intérêts au taux légal sur les sommes visées par la mise en demeure (décret "
                                     "1967, art. 36), à titre indicatif" + ("" if it["taux_a_jour"] else
                                     " ; taux du semestre pas encore inscrit dans Mitoyen, dernier taux connu utilisé")}
            if limite and au > limite:
                res.append({**p, "etat": "delai_expire", "prochaine": None, "date_limite": limite.isoformat(),
                            "message": (f"Mise en demeure restée sans effet après le {limite:%d/%m/%Y}. Les provisions "
                                        "non échues de l'exercice sont devenues exigibles (loi 1965, art. 19-2) ; la "
                                        "suite est judiciaire. Avant d'agir, se faire conseiller (ADIL, avocat).")})
            else:
                res.append({**p, "etat": "mise_en_demeure_en_cours", "prochaine": None,
                            "date_limite": limite.isoformat() if limite else None,
                            "message": "Mise en demeure envoyée : délai de 30 jours en cours."
                                       + ("" if dernier.get("presentee_le") else
                                          " Indiquer la date de première présentation (avis de réception) pour un "
                                          "décompte exact.")})
            continue
        suivant = niveau + 1
        attente = []
        if c["retard_max_jours"] < seuil_jours[suivant]:
            attente.append(seuil_jours[suivant] - c["retard_max_jours"])
        if dernier:
            ecart = (au - date.fromisoformat(dernier["envoyee_le"])).days
            if ecart < r["intervalle_min_jours"]:
                attente.append(r["intervalle_min_jours"] - ecart)
        if attente:
            jours = max(attente)
            res.append({**p, "etat": "en_attente", "prochaine": NIVEAUX[suivant],
                        "possible_le": (au + timedelta(days=jours)).isoformat(),
                        "message": f"{LIBELLES[suivant]} possible dans {jours} jour{'s' if jours > 1 else ''}."})
        else:
            res.append({**p, "etat": "a_preparer", "prochaine": NIVEAUX[suivant],
                        "message": f"{LIBELLES[suivant]} à préparer."})
    ordre = {"delai_expire": 0, "a_preparer": 1, "a_envoyer": 2, "bloque": 3, "mise_en_demeure_en_cours": 4, "en_attente": 5}
    return sorted(res, key=lambda x: (ordre[x["etat"]], -x["impaye"]))


def taux_legal(jour: date) -> tuple[Decimal, bool]:
    """Taux « autres cas » du semestre, et False s'il a fallu reprendre le dernier taux connu."""
    cle = f"{jour.year}-S{1 if jour.month <= 6 else 2}"
    if cle in TAUX_LEGAL:
        return TAUX_LEGAL[cle], True
    connus = sorted(TAUX_LEGAL)
    avant = [k for k in connus if k <= cle]
    return TAUX_LEGAL[(avant or connus)[-1]], False


def interets(principal: Decimal, depuis: date, au: date) -> dict:
    """Intérêts simples au taux légal, semestre par semestre, de `depuis` (exclu) à `au` (inclus)."""
    total, a_jour, j = Decimal("0"), True, depuis
    detail = []
    while j < au:
        premier = j + timedelta(days=1)            # premier jour de la tranche
        fin_sem = date(premier.year, 6, 30) if premier.month <= 6 else date(premier.year, 12, 31)
        fin = min(fin_sem, au)
        jours = (fin - j).days
        taux, ok = taux_legal(premier)
        a_jour &= ok
        y = premier.year
        base_an = 366 if (y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)) else 365
        m = principal * taux / 100 * jours / base_an
        total += m
        detail.append({"du": (j + timedelta(days=1)).isoformat(), "au": fin.isoformat(), "jours": jours,
                       "taux": str(taux), "interets": str(m.quantize(CENT))})
        j = fin
    return {"montant": total.quantize(CENT), "detail": detail, "taux_a_jour": a_jour}


def provisions_a_echoir(fiche: dict, cid: str, apres: date) -> list[dict]:
    """Provisions de l'exercice en cours pas encore exigibles : ce que la mise en demeure
    rendrait exigible (art. 19-2). Calculées depuis le budget voté de la fiche."""
    from fiche_copro import appel_par_lot, rubriques
    exercice = fiche["budget_previsionnel"]["exercice"]
    par_lot = appel_par_lot(fiche)
    mes_lots = {l["numero"] for l in fiche["lots"] if l["proprietaire"] == cid}
    libelles = {x["id"]: x["libelle"] for x in rubriques(fiche)}
    res = []
    for i, mmjj in enumerate(fiche["appels_de_fonds"]["dates_exigibilite"], 1):
        mm, jj = (int(x) for x in mmjj.split("-"))
        d = date(exercice, mm, jj)
        if d <= apres:
            continue
        detail = [{"rubrique": libelles[rid], "montant": sum((m for (r2, n), m in par_lot.items()
                                                              if r2 == rid and n in mes_lots), Decimal("0"))}
                  for rid in libelles]
        detail = [x for x in detail if x["montant"] > 0]
        res.append({"libelle": f"Appel de fonds {exercice}, {_trimestre(i)} trimestre", "date": d.isoformat(),
                    "montant": sum((x["montant"] for x in detail), Decimal("0")), "detail": detail})
    return res


def preparer_relance(fiche: dict, racine_appels: Path, racine_data: Path, copro: str, cid: str,
                     sortie: Path, au: date | None = None, signataire: str | None = None) -> dict:
    """Prépare la lettre du niveau proposé pour ce copropriétaire et l'inscrit au registre
    (statut « préparée ») ; le syndic la relit, la signe, l'envoie, puis la marque envoyée."""
    jour = au or date.today()
    e = etat(fiche, racine_appels, racine_data, copro, jour)
    prop = next((p for p in e["relances"] if p["coproprietaire"] == cid), None)
    if not prop:
        raise RecouvrementInvalide("Aucun impayé à relancer pour ce copropriétaire")
    if prop["etat"] != "a_preparer":
        raise RecouvrementInvalide(prop["message"])
    niveau = {v: k for k, v in NIVEAUX.items()}[prop["prochaine"]]
    c = e["comptes"][cid]
    reg = charger_relances(racine_data, copro)
    rid = f"R{len(reg) + 1:04d}"
    a_echoir = provisions_a_echoir(fiche, cid, jour) if niveau == 3 else []
    rel = {"id": rid, "coproprietaire": cid, "niveau": niveau, "type": NIVEAUX[niveau], "preparee_le": jour.isoformat(),
           "montant": str(c["impaye"]),
           "echeances": [{"echeance": l["echeance"], "libelle": l["libelle"], "date": l["date"], "reste": str(l["reste"])}
                         for l in c["impayes"]],
           "statut": "preparee", "fichier": f"{rid}_{NIVEAUX[niveau]}_{cid}.pdf"}
    chemin = Path(sortie) / rel["fichier"]
    generer_lettre(fiche, rel, c, a_echoir, chemin, jour, signataire)
    _sauver_relances(racine_data, copro, reg + [rel])
    import papier
    k = papier.canal(fiche, cid, jour)
    copro_fiche = next(x for x in fiche["coproprietaires"] if x["id"] == cid)
    aidant = copro_fiche.get("aidant") or {}
    return {**rel, "chemin": str(chemin),
            "envoi": "recommandé (LRAR, recommandé électronique ou remise contre émargement)" if niveau == 3 else k["canal"],
            "copie_aidant": bool(aidant) and "relances" in (aidant.get("documents") or []),
            "a_faire": ("Relire, signer et envoyer en recommandé, puis indiquer la date d'envoi et, à réception de "
                        "l'avis, la date de première présentation." if niveau == 3 else
                        "Relire, signer et envoyer, puis indiquer la date d'envoi.")}


def marquer(fiche: dict, racine: Path, copro: str, rid: str, statut: str, envoyee_le: date | None = None,
            moyen: str | None = None, presentee_le: date | None = None) -> dict:
    reg = charger_relances(racine, copro)
    rel = next((x for x in reg if x["id"] == rid), None)
    if not rel:
        raise RecouvrementInvalide("Relance inconnue")
    if statut == "annulee":
        rel["statut"] = "annulee"
    elif statut == "envoyee":
        if not envoyee_le:
            raise RecouvrementInvalide("Indiquer la date d'envoi")
        if rel["niveau"] == 3 and moyen not in ("lrar", "lre", "remise_main", "commissaire_justice"):
            raise RecouvrementInvalide("Une mise en demeure s'envoie en recommandé : moyen lrar, lre, remise_main "
                                       "(contre émargement) ou commissaire_justice")
        if envoyee_le < date.fromisoformat(rel["preparee_le"]):
            raise RecouvrementInvalide("Date d'envoi antérieure à la préparation")
        if presentee_le and presentee_le < envoyee_le:
            raise RecouvrementInvalide("Date de présentation antérieure à l'envoi")
        rel.update({"statut": "envoyee", "envoyee_le": envoyee_le.isoformat(), "moyen": moyen or "email",
                    "presentee_le": presentee_le.isoformat() if presentee_le else rel.get("presentee_le")})
    else:
        raise RecouvrementInvalide("Statut attendu : envoyee ou annulee")
    _sauver_relances(racine, copro, reg)
    return rel


# --------------------------------------------------------------------------- #
# 5. Documents
# --------------------------------------------------------------------------- #
def _fr(iso: str) -> str:
    return date.fromisoformat(iso).strftime("%d/%m/%Y")


def generer_lettre(fiche: dict, rel: dict, compte: dict, a_echoir: list[dict], chemin: Path, jour: date,
                   signataire: str | None = None) -> Path:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    import papier
    from avis_appel import CanevasNumerote, essentiel, euros, fort, styles, tableau, tampon
    cid = rel["coproprietaire"]
    cp = next(x for x in fiche["coproprietaires"] if x["id"] == cid)
    grand = bool(cp.get("grands_caracteres"))
    S = styles(grand)
    c = fiche["copropriete"]
    adf = fiche["appels_de_fonds"]
    syndic = next(x for x in fiche["coproprietaires"] if x["id"] == fiche["gouvernance"]["syndic"]["coproprietaire_id"])
    niveau = rel["niveau"]
    total = _d(rel["montant"])
    delai = {1: 15, 2: 15, 3: 30}[niveau]

    adr = "<br/>".join(papier.adresse_postale(fiche, cid))
    exp = f"<b>{c['nom']}</b><br/>Syndic : {syndic['nom']}<br/>{c['adresse']}"
    entete = Table([[Paragraph(exp, S["txt"]), Paragraph(adr, S["txt"])]], colWidths=[94 * mm, 80 * mm])
    entete.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    h = [entete, Spacer(1, 5 * mm)]
    if niveau == 3:
        h.append(Paragraph("<b>Lettre recommandée avec avis de réception</b> (ou recommandé électronique)", S["txt"]))
    h += [Paragraph(f"Nice, le {jour:%d/%m/%Y}" if "Nice" in c["adresse"] else f"Le {jour:%d/%m/%Y}", S["droite"]),
          Spacer(1, 4 * mm)]
    titre = {1: "Rappel : charges de copropriété", 2: "Relance : charges de copropriété impayées",
             3: "Mise en demeure de payer"}[niveau]
    h += [Paragraph(titre, S["titre"]), Spacer(1, 3 * mm)]

    salut = "Madame, Monsieur,"
    ouverture = {
        1: ("Sauf erreur de ma part, je n'ai pas encore reçu le paiement des charges ci-dessous. Il s'agit "
            "peut-être d'un simple oubli : si vous avez réglé entre-temps, merci de ne pas tenir compte de ce "
            "courrier, ou de me le signaler pour que je vérifie."),
        2: ("Malgré mon précédent rappel, les charges ci-dessous restent impayées. Je vous remercie de les régler "
            f"dans les {delai} jours."),
        3: ("Malgré mes précédents courriers, les sommes ci-dessous restent impayées. Par la présente, je vous "
            f"mets en demeure de régler la somme de {fort(euros(total))} dans un délai de 30 jours à compter du "
            "lendemain de la première présentation de cette lettre."),
    }[niveau]
    h += [Paragraph(salut, S["txt"]), Spacer(1, 2 * mm), Paragraph(ouverture, S["txt"]), Spacer(1, 4 * mm)]

    if niveau < 3:
        h += [essentiel([("Montant à régler", euros(total), f"{len(rel['echeances'])} échéance(s)"),
                         ("À payer avant le", (jour + timedelta(days=delai)).strftime("%d/%m/%Y"), f"sous {delai} jours")],
                        grand=grand), Spacer(1, 5 * mm)]

    # Détail : nature et montant de chaque provision (indispensable pour la mise en demeure)
    par_ech = {l["echeance"]: l for l in compte["impayes"]}
    if niveau < 3:                      # rappel et relance : une ligne par échéance suffit
        lignes = [["Échéance", "Appel", "Montant", "Reste dû"]]
        for e in rel["echeances"]:
            l = par_ech.get(e["echeance"])
            lignes.append([_fr(e["date"]), Paragraph(e["libelle"], S["txt"]),
                           euros(l["montant"]) if l else "", euros(_d(e["reste"]))])
        lignes.append(["", "Total dû", "", euros(total)])
        largeurs = [26 * mm, 90 * mm, 29 * mm, 29 * mm]
    else:
        lignes, largeurs = _detail_med(rel, par_ech, S, total, euros, Paragraph, mm)
    t = tableau(lignes, largeurs, aligner_droite_depuis=2, grand=grand)
    h += [t, Spacer(1, 5 * mm)]
    cs = adf["compte_separe"]
    payer = (f"<b>Pour payer</b> : virement à {cs['titulaire']}, IBAN {cs['iban']}, en indiquant la référence "
             f"<b>{cid}</b> ; ou chèque à l'ordre du syndicat, remis au syndic.")
    h += [Paragraph(payer, S["txt"]), Spacer(1, 4 * mm)]

    if niveau == 1:
        h.append(Paragraph("Si vous traversez une difficulté passagère, parlons-en : nous pouvons convenir ensemble "
                           "d'un échéancier de paiement.", S["txt"]))
    elif niveau == 2:
        h.append(Paragraph(
            "Sans paiement ou contact de votre part dans ce délai, je devrai vous adresser une mise en demeure par "
            "lettre recommandée. Elle ferait courir des intérêts au taux légal (décret du 17 mars 1967, art. 36), "
            "les frais engagés ensuite pourraient être mis à votre charge (loi du 10 juillet 1965, art. 10-1), et, "
            "faute de paiement dans les 30 jours, les autres provisions de l'exercice deviendraient immédiatement "
            "exigibles (art. 19-2). Si vous rencontrez une difficulté, contactez-moi : un échéancier reste possible.",
            S["txt"]))
    else:
        txt = ("<b>Conséquences en l'absence de paiement dans ce délai</b><br/>"
               "• Loi du 10 juillet 1965, art. 19-2 : à défaut de paiement d'une provision à sa date d'exigibilité, et "
               "après mise en demeure restée infructueuse pendant 30 jours, les autres provisions non encore échues "
               "de l'exercice, ainsi que les sommes restant dues au titre des exercices précédents, deviennent "
               "immédiatement exigibles. Le syndicat peut alors en demander le paiement au président du tribunal "
               "judiciaire.<br/>"
               "• Décret du 17 mars 1967, art. 36 : sauf stipulation contraire du règlement de copropriété, les "
               "sommes dues portent intérêt au taux légal à compter de la présente mise en demeure.<br/>"
               "• Loi du 10 juillet 1965, art. 10-1 : les frais nécessaires exposés par le syndicat à compter de la "
               "présente mise en demeure pour le recouvrement de sa créance sont imputables à vous seul.")
        bloc = [Paragraph(txt, S["txt"])]
        if a_echoir:
            la = [["Échéance", "Provision qui deviendrait exigible", "Montant"]]
            for p in a_echoir:
                la.append([_fr(p["date"]), Paragraph(p["libelle"] + " : " + ", ".join(
                    f"{x['rubrique'].lower()} {euros(x['montant'])}" for x in p["detail"]), S["txt"]), euros(p["montant"])])
            la.append(["", "Total des provisions à échoir", euros(sum((p["montant"] for p in a_echoir), Decimal("0")))])
            bloc += [Spacer(1, 3 * mm), tableau(la, [26 * mm, 112 * mm, 36 * mm], aligner_droite_depuis=2, grand=grand)]
        h += [KeepTogether(bloc), Spacer(1, 4 * mm),
              Paragraph("Si vous rencontrez des difficultés, contactez-moi sans attendre : un échéancier de paiement "
                        "peut encore être convenu. Vous pouvez aussi vous faire conseiller gratuitement par l'ADIL "
                        "de votre département.", S["txt"])]
    qualite = "coopératif" if fiche["gouvernance"]["mode_gestion"] == "cooperatif" else "bénévole"
    # la formule de politesse et la signature restent toujours ensemble
    h += [Spacer(1, 3 * mm),
          KeepTogether([Paragraph("Je vous prie d'agréer, Madame, Monsieur, mes salutations distinguées.", S["txt"]),
                        Spacer(1, 4 * mm), Paragraph(f"{signataire or syndic['nom']}, syndic {qualite}", S["txt"]),
                        Spacer(1, 10 * mm), Paragraph("Signature", S["petit"])])]
    if papier.canal(fiche, cid, jour)["canal"] != "courrier":     # utile seulement à qui reçoit par mail
        h += [Spacer(1, 4 * mm), Paragraph(papier.MENTION_PAPIER, S["petit"])]
    titre_doc = {1: "Rappel", 2: "Relance", 3: "Mise en demeure"}[niveau]
    pied = f"{c['nom']} – {titre_doc.lower()} {rel['id']} – {cp['nom']}"
    chemin.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(str(chemin), pagesize=A4,
                            leftMargin=18 * mm, rightMargin=18 * mm, topMargin=20 * mm, bottomMargin=18 * mm,
                            title=f"{titre_doc} – {c['nom']}")
    doc.build(h, canvasmaker=CanevasNumerote, onFirstPage=tampon(False, pied), onLaterPages=tampon(False, pied))
    return chemin


def _detail_med(rel, par_ech, S, total, euros, Paragraph, mm):
    """Mise en demeure : nature et montant de chaque provision réclamée (Cass., avis du 12/12/2024)."""
    lignes = [["Échéance", "Nature", "Montant"]]
    for e in rel["echeances"]:
        l = par_ech.get(e["echeance"])
        lignes.append([_fr(e["date"]), Paragraph(f"<b>{e['libelle']}</b>", S["txt"]), ""])
        for x in (l["detail"] if l else []):
            lignes.append(["", Paragraph(x["rubrique"], S["txt"]), euros(x["montant"])])
        if l and l["paye"] > 0:
            lignes.append(["", Paragraph("Déjà réglé sur cette échéance", S["txt"]), euros(-l["paye"])])
        lignes.append(["", Paragraph("Reste dû", S["txt"]), euros(_d(e["reste"]))])
    lignes.append(["", "Total dû", euros(total)])
    return lignes, [26 * mm, 112 * mm, 36 * mm]


def generer_etat(fiche: dict, e: dict, chemin: Path) -> Path:
    """État des impayés pour le syndic et le conseil syndical."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    from avis_appel import S, CanevasNumerote, essentiel, euros, tableau, tampon
    c = fiche["copropriete"]
    jour = date.fromisoformat(e["au"])
    rap = e["rapprochement"]
    h = [Paragraph("État des impayés", S["titre"]),
         Paragraph(f"{c['nom']} · au {jour:%d/%m/%Y} · relevé bancaire importé jusqu'au "
                   f"{_fr(e['releve']['au']) if e['releve']['au'] else '—'}", S["sous"]), Spacer(1, 5 * mm),
         essentiel([("Impayés", euros(e["total_impayes"]), f"{e['nb_debiteurs']} copropriétaire(s)"
                    + (f", dont {euros(e['dont_a_confirmer'])} peut-être déjà payés" if e["dont_a_confirmer"] else "")),
                    ("Plus de 300 €", str(e["nb_debiteurs_plus_300"]), "hors paiements à confirmer (fiche synthétique)"),
                    ("À vérifier", str(len(e["a_confirmer"]) + len(e["non_rapproches"])),
                     f"{len(e['a_confirmer'])} à confirmer, {len(e['non_rapproches'])} sans piste")]),
         Spacer(1, 6 * mm)]
    etats = {"a_preparer": "à préparer", "a_envoyer": "à envoyer", "bloque": "paiement à confirmer",
             "mise_en_demeure_en_cours": "délai en cours", "delai_expire": "délai expiré", "en_attente": "attendre"}
    if e["relances"]:
        lignes = [["Copropriétaire", "Impayé", "Retard", "Dernière relance", "Prochaine étape"]]
        for p in e["relances"]:
            der = p["derniere_relance"]
            prochaine = (LIBELLES[{v: k for k, v in NIVEAUX.items()}[p["prochaine"]]] if p["prochaine"] else "—")
            lignes.append([Paragraph(p["nom"], S["txt"]), euros(p["impaye"]), f"{p['retard_max_jours']} j",
                           Paragraph(f"{der['niveau']} du {_fr(der['envoyee_le'])}" if der else "aucune", S["txt"]),
                           Paragraph(f"<b>{etats[p['etat']]}</b> · {prochaine}", S["txt"])])
        h += [Paragraph("Copropriétaires en retard", S["sous"]), Spacer(1, 2 * mm),
              tableau(lignes, [46 * mm, 26 * mm, 18 * mm, 40 * mm, 44 * mm], aligner_droite_depuis=1, lignes_total=0),
              Spacer(1, 6 * mm)]
    else:
        h += [Paragraph("Aucun impayé.", S["txt"]), Spacer(1, 6 * mm)]
    a_voir = e["a_confirmer"] + e["non_rapproches"]
    if a_voir:
        noms = {x["id"]: x["nom"] for x in fiche["coproprietaires"]}
        lv = [["Date", "Libellé du relevé", "Montant", "Piste"]]
        for r in a_voir:
            piste = (f"{noms[r['parts'][0]['coproprietaire']]} ? ({r['methode']})" if r["parts"] else r["methode"])
            lv.append([_fr(r["date"]), Paragraph(r["libelle"], S["petit"]), euros(r["montant"]), Paragraph(piste, S["petit"])])
        h += [Paragraph("Opérations à vérifier", S["sous"]), Spacer(1, 2 * mm),
              tableau(lv, [22 * mm, 74 * mm, 26 * mm, 52 * mm], aligner_droite_depuis=2, lignes_total=0, serre=True),
              Spacer(1, 6 * mm)]
    h += [Paragraph(f"Rapprochement du relevé : {rap['automatique']} opération(s) automatiques, {rap['confirme']} "
                    f"confirmée(s) par le syndic, {rap['propose']} à confirmer, {rap['non_rapproche']} sans piste, "
                    f"{rap['depense']} dépense(s).", S["petit"]), Spacer(1, 2 * mm),
          Paragraph("Document interne au syndic et au conseil syndical : il contient des données personnelles et "
                    "ne doit pas être affiché. " + AVERTISSEMENT, S["petit"])]
    pied = f"{c['nom']} – état des impayés – document interne"
    chemin.parent.mkdir(parents=True, exist_ok=True)
    SimpleDocTemplate(str(chemin), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=20 * mm,
                      bottomMargin=18 * mm, title="État des impayés").build(
        h, canvasmaker=CanevasNumerote, onFirstPage=tampon(False, pied), onLaterPages=tampon(False, pied))
    return chemin
