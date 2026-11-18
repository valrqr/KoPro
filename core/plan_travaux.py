"""
plan_travaux.py — Le plan pluriannuel de travaux, vivant.

Le plan (article 14-2 de la loi du 10 juillet 1965) liste les travaux à prévoir
sur 10 ans, leur coût estimé et leur priorité. Chez un syndic classique, il finit
en PDF dans un tiroir. Ici, il sert à piloter :

    1. projection du fonds de travaux année par année : ce que le fonds paie,
       ce qu'il faudra appeler en plus, le solde restant ;
    2. cotisation minimale légale : 5 % du budget prévisionnel et, une fois le
       plan adopté en AG, 2,5 % du montant des travaux du plan ;
    3. cotisation lissée : le montant annuel constant qui finance tout le plan
       sans appel exceptionnel ;
    4. quote-part de chaque copropriétaire, année par année ;
    5. points de conformité (plan à actualiser, DPE collectif, retards) ;
    6. lien avec les dossiers de travaux : une ligne du plan qui arrive à échéance
       ouvre un dossier, et le circuit devis → vote prend le relais.

Le plan voté reste dans la fiche (config/). Son suivi (dossier ouvert, travaux
réalisés, report) est tenu par Mitoyen dans data/<copro>/plan_suivi.json.

Rappel : projection indicative, établie à partir des estimations du plan.
Ce n'est ni un avis juridique ni un audit technique.

Usage :
    python core/plan_travaux.py config/mimosas.yaml
    python core/plan_travaux.py config/mimosas.yaml --cotisation 3000 --indexation 3 --pdf plan.pdf
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import date
from decimal import ROUND_CEILING, Decimal
from pathlib import Path

from fiche_copro import CENT, TAUX_MIN_FONDS_TRAVAUX, ajouter_mois, charger_fiche, repartir, total_budget

CATEGORIES = {
    "securite": "Sécurité",
    "conservation": "Conservation du bâti",
    "energie": "Économies d'énergie",
    "confort": "Confort",
}
PRIORITES = {1: "Indispensable", 2: "Nécessaire", 3: "Souhaitable"}
STATUTS = {
    "prevu": "Prévu",
    "engage": "Dossier ouvert",
    "realise": "Réalisé",
    "reporte": "Reporté",
    "abandonne": "Abandonné",
}
NATURE_DOSSIER = {"securite": "reparation", "conservation": "reparation",
                  "energie": "amelioration", "confort": "amelioration"}

TAUX_MIN_PLAN = Decimal("0.025")          # 2,5 % du montant des travaux du plan adopté
SEUIL_SUSPENSION_PLAN = Decimal("0.5")    # fonds > 50 % des travaux du plan : suspension possible
DUREE_PLAN_ANS = 10                       # le plan est actualisé tous les 10 ans
PAS_LISSAGE = Decimal("10")               # cotisation lissée arrondie à la dizaine d'euros supérieure
AVERTISSEMENT = ("Projection indicative, établie à partir des estimations du plan de travaux, hors aides "
                 "(MaPrimeRénov' Copropriété…) et hors intérêts du compte. Les montants réels dépendront des "
                 "devis. Elle ne remplace pas l'avis d'un professionnel, d'un juriste ou de l'ADIL.")


class PlanInvalide(ValueError):
    pass


def _d(v) -> Decimal:
    return Decimal(str(v))


def _eur(m: Decimal) -> str:
    """12345.6 -> '12 345,60 €' (format français, pour les messages)."""
    return f"{Decimal(m):,.2f}".replace(",", "\u00a0").replace(".", ",") + "\u00a0€"


# --------------------------------------------------------------------------- #
# Lecture et validation du plan
# --------------------------------------------------------------------------- #
def plan_de(fiche: dict) -> dict | None:
    return fiche.get("plan_travaux")


def valider_plan(fiche: dict) -> list[str]:
    """Erreurs bloquantes du plan (liste vide si tout va bien ou si pas de plan)."""
    p = plan_de(fiche)
    if not p:
        return []
    err: list[str] = []
    cles = fiche["cles_repartition"]
    if p.get("statut") not in ("projet", "adopte"):
        err.append(f"Plan de travaux : statut « {p.get('statut')} » inconnu (projet | adopte)")
    if p.get("statut") == "adopte" and not isinstance(p.get("adopte_le"), date):
        err.append("Plan de travaux adopté : la date de l'AG (adopte_le) est obligatoire")
    if not isinstance(p.get("elabore_le"), date):
        err.append("Plan de travaux : la date d'élaboration (elabore_le) est obligatoire")
    debut = p.get("annee_debut")
    if not isinstance(debut, int):
        err.append("Plan de travaux : annee_debut doit être une année (ex. 2027)")
    vus: set[str] = set()
    for l in p.get("lignes") or []:
        lid = l.get("id", "?")
        if lid in vus:
            err.append(f"Plan de travaux : identifiant {lid} en double")
        vus.add(lid)
        if l.get("cle") not in cles:
            err.append(f"Plan de travaux {lid} : clé inconnue {l.get('cle')}")
        if l.get("categorie") not in CATEGORIES:
            err.append(f"Plan de travaux {lid} : catégorie inconnue {l.get('categorie')}")
        if l.get("priorite") not in PRIORITES:
            err.append(f"Plan de travaux {lid} : priorité attendue 1, 2 ou 3")
        try:
            if _d(l.get("montant")) <= 0:
                raise ValueError
        except Exception:
            err.append(f"Plan de travaux {lid} : montant invalide {l.get('montant')!r}")
        an = l.get("annee")
        if not isinstance(an, int) or (isinstance(debut, int) and not debut <= an < debut + DUREE_PLAN_ANS):
            err.append(f"Plan de travaux {lid} : année {an!r} hors de la période du plan "
                       f"({debut}–{debut + DUREE_PLAN_ANS - 1 if isinstance(debut, int) else '?'})")
    solde = (fiche.get("fonds_travaux") or {}).get("solde")
    if solde is not None:
        if not isinstance(solde.get("au"), date):
            err.append("Fonds de travaux : la date du solde (solde.au) est obligatoire")
        try:
            if _d(solde.get("montant")) < 0:
                raise ValueError
        except Exception:
            err.append(f"Fonds de travaux : solde invalide {solde.get('montant')!r}")
    return err


# --------------------------------------------------------------------------- #
# Suivi (data/<copro>/plan_suivi.json)
# --------------------------------------------------------------------------- #
def chemin_suivi(racine: Path, copro: str) -> Path:
    return Path(racine) / copro / "plan_suivi.json"


def charger_suivi(racine: Path, copro: str) -> dict:
    chemin = chemin_suivi(racine, copro)
    if not chemin.is_file():
        return {}
    return json.loads(chemin.read_text(encoding="utf-8"))


def sauver_suivi(racine: Path, copro: str, suivi: dict) -> None:
    chemin = chemin_suivi(racine, copro)
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text(json.dumps(suivi, ensure_ascii=False, indent=2), encoding="utf-8")


def lignes(fiche: dict, suivi: dict | None = None) -> list[dict]:
    """Les lignes du plan, enrichies de leur suivi. annee_prevue = année du plan,
    annee = année retenue (différente si la ligne a été reportée)."""
    p = plan_de(fiche) or {}
    suivi = suivi or {}
    res = []
    for l in p.get("lignes") or []:
        s = suivi.get(l["id"], {})
        res.append({
            "id": l["id"], "intitule": l["intitule"], "categorie": l["categorie"],
            "categorie_libelle": CATEGORIES[l["categorie"]], "priorite": l["priorite"],
            "priorite_libelle": PRIORITES[l["priorite"]], "cle": l["cle"],
            "montant": _d(l["montant"]).quantize(CENT), "annee_prevue": l["annee"],
            "annee": s.get("annee", l["annee"]), "statut": s.get("statut", "prevu"),
            "statut_libelle": STATUTS[s.get("statut", "prevu")], "dossier": s.get("dossier"),
            "montant_reel": _d(s["montant_reel"]).quantize(CENT) if s.get("montant_reel") is not None else None,
            "note": s.get("note", ""), "maj_le": s.get("maj_le"),
        })
    return res


def _ligne(fiche: dict, ligne_id: str, suivi: dict | None = None) -> dict:
    for l in lignes(fiche, suivi):
        if l["id"] == ligne_id:
            return l
    raise PlanInvalide(f"Aucune ligne {ligne_id} dans le plan de travaux")


def mettre_a_jour_ligne(racine: Path, copro: str, fiche: dict, ligne_id: str, statut: str,
                        annee: int | None = None, montant_reel=None, note: str = "",
                        aujourd_hui: date | None = None) -> dict:
    """Le syndic signale l'avancement d'une ligne : reportée, réalisée, abandonnée…"""
    if statut not in STATUTS:
        raise PlanInvalide(f"Statut inconnu : {statut} (attendu : {', '.join(STATUTS)})")
    suivi = charger_suivi(racine, copro)
    ligne = _ligne(fiche, ligne_id, suivi)
    entree = dict(suivi.get(ligne_id, {}))
    entree["statut"] = statut
    if statut == "reporte":
        if not isinstance(annee, int) or annee <= ligne["annee"]:
            raise PlanInvalide("Un report demande une nouvelle année, postérieure à l'année actuelle de la ligne")
        entree["annee"] = annee
    elif annee is not None:
        entree["annee"] = annee
    if statut == "realise":
        if montant_reel is None:
            raise PlanInvalide("Pour une ligne réalisée, indiquer le montant réellement payé (montant_reel)")
        try:
            if _d(montant_reel) <= 0:
                raise ValueError
        except Exception:
            raise PlanInvalide(f"Montant réel invalide : {montant_reel!r}")
        entree["montant_reel"] = str(_d(montant_reel).quantize(CENT))
    if note:
        entree["note"] = note.strip()[:500]
    entree["maj_le"] = (aujourd_hui or date.today()).isoformat()
    suivi[ligne_id] = entree
    sauver_suivi(racine, copro, suivi)
    return _ligne(fiche, ligne_id, suivi)


def ouvrir_dossier(racine: Path, copro: str, fiche: dict, ligne_id: str,
                   aujourd_hui: date | None = None) -> dict:
    """Une ligne du plan arrive à échéance : on ouvre le dossier de travaux
    (devis, comparatif, vote) et on note le lien des deux côtés."""
    import travaux

    suivi = charger_suivi(racine, copro)
    l = _ligne(fiche, ligne_id, suivi)
    if l["statut"] in ("engage", "realise") and l["dossier"]:
        raise PlanInvalide(f"La ligne {ligne_id} a déjà un dossier : {l['dossier']}")
    if l["statut"] == "abandonne":
        raise PlanInvalide(f"La ligne {ligne_id} a été abandonnée")
    description = (f"Ligne {l['id']} du plan pluriannuel de travaux · {l['categorie_libelle']} · "
                   f"priorité {l['priorite']} ({l['priorite_libelle'].lower()}) · prévue en {l['annee']} · "
                   f"estimation du plan : {_eur(l['montant'])} TTC.")
    dossier = travaux.creer_dossier(racine, copro, fiche, l["intitule"], description,
                                    NATURE_DOSSIER[l["categorie"]], False, l["cle"], aujourd_hui)
    dossier["plan_ligne"] = l["id"]
    dossier["estimation_plan"] = str(l["montant"])
    travaux.sauver_dossier(travaux.dossier_dir(racine, copro, dossier["id"]), dossier)
    entree = dict(suivi.get(ligne_id, {}))
    entree.update({"statut": "engage", "dossier": dossier["id"],
                   "maj_le": (aujourd_hui or date.today()).isoformat()})
    suivi[ligne_id] = entree
    sauver_suivi(racine, copro, suivi)
    return dossier


# --------------------------------------------------------------------------- #
# Cotisation minimale légale
# --------------------------------------------------------------------------- #
def montant_plan(fiche: dict) -> Decimal:
    """Montant des travaux prévus dans le plan (base des 2,5 %)."""
    p = plan_de(fiche) or {}
    return sum((_d(l["montant"]) for l in p.get("lignes") or []), Decimal("0")).quantize(CENT)


def cotisation_minimale(fiche: dict) -> dict:
    budget = total_budget(fiche)
    min_budget = (budget * TAUX_MIN_FONDS_TRAVAUX).quantize(CENT)
    total = montant_plan(fiche)
    min_plan = (total * TAUX_MIN_PLAN).quantize(CENT)
    adopte = (plan_de(fiche) or {}).get("statut") == "adopte"
    applicable = max(min_budget, min_plan) if adopte else min_budget
    si_adopte = max(min_budget, min_plan)
    actuelle = _d(fiche["fonds_travaux"]["cotisation_annuelle"]).quantize(CENT)
    return {
        "cotisation_actuelle": actuelle, "budget_previsionnel": budget,
        "minimum_budget": min_budget, "montant_plan": total, "minimum_plan": min_plan,
        "plan_adopte": adopte, "minimum_applicable": applicable, "minimum_si_adopte": si_adopte,
        "conforme": actuelle >= applicable, "conforme_si_adopte": actuelle >= si_adopte,
        "regle": ("Au moins 5 % du budget prévisionnel et 2,5 % des travaux du plan adopté"
                  if adopte else "Au moins 5 % du budget prévisionnel (pas encore de plan adopté)"),
    }


# --------------------------------------------------------------------------- #
# Projection du fonds de travaux
# --------------------------------------------------------------------------- #
def _solde_initial(fiche: dict) -> tuple[Decimal, date | None]:
    s = (fiche.get("fonds_travaux") or {}).get("solde")
    if not s:
        return Decimal("0"), None
    return _d(s["montant"]).quantize(CENT), s["au"]


def _indexer(montant: Decimal, annee: int, annee_ref: int, taux: Decimal) -> Decimal:
    if not taux or annee <= annee_ref:
        return montant.quantize(CENT)
    return (montant * (1 + taux) ** (annee - annee_ref)).quantize(CENT)


def _travaux_par_annee(fiche: dict, suivi: dict | None, debut: int, duree: int,
                       indexation: Decimal) -> dict[int, list[dict]]:
    p = plan_de(fiche) or {}
    annee_ref = p["elabore_le"].year if isinstance(p.get("elabore_le"), date) else debut
    par_annee: dict[int, list[dict]] = defaultdict(list)
    for l in lignes(fiche, suivi):
        if l["statut"] in ("realise", "abandonne"):
            continue
        en_retard = l["annee"] < debut
        an = max(l["annee"], debut)
        if an >= debut + duree:
            continue
        par_annee[an].append({
            "id": l["id"], "intitule": l["intitule"], "cle": l["cle"], "priorite": l["priorite"],
            "statut": l["statut"], "en_retard": en_retard,
            "montant": _indexer(l["montant"], an, annee_ref, indexation),
        })
    for an in par_annee:
        par_annee[an].sort(key=lambda t: (t["priorite"], t["id"]))
    return par_annee


def projection(fiche: dict, suivi: dict | None = None, cotisation=None, indexation=0,
               annee_debut: int | None = None, duree: int = DUREE_PLAN_ANS) -> dict:
    """Année par année : le fonds reçoit la cotisation, paie les travaux de l'année
    par ordre de priorité, et ce qu'il ne couvre pas est à appeler en plus, selon la
    clé de chaque ligne."""
    p = plan_de(fiche)
    if not p:
        raise PlanInvalide("Cette fiche ne contient pas de plan de travaux (section plan_travaux)")
    taux = _d(indexation or 0) / 100     # indexation en %, ex. 3 pour 3 % par an
    c = (_d(cotisation) if cotisation is not None else _d(fiche["fonds_travaux"]["cotisation_annuelle"])).quantize(CENT)
    if c < 0:
        raise PlanInvalide("La cotisation ne peut pas être négative")
    solde0, solde_au = _solde_initial(fiche)
    debut = annee_debut or p["annee_debut"]
    notes = []
    if solde_au and solde_au.year + 1 != debut:
        notes.append(f"Le solde du fonds est connu au {solde_au:%d/%m/%Y} : la projection le prend comme "
                     f"solde au 1er janvier {debut}.")
    if not solde_au:
        notes.append("Solde du fonds de travaux non renseigné dans la fiche : la projection part de 0 €.")
    if taux:
        notes.append(f"Montants indexés de {str(taux * 100).rstrip('0').rstrip('.').replace('.', ',')} % par an "
                     f"à partir de l'année d'élaboration du plan.")

    par_annee = _travaux_par_annee(fiche, suivi, debut, duree, taux)
    solde = solde0
    annees = []
    for an in range(debut, debut + duree):
        disponible = solde + c
        detail = []
        for t in par_annee.get(an, []):
            pris = min(disponible, t["montant"])
            disponible -= pris
            detail.append({**t, "pris_sur_fonds": pris, "appel_complementaire": t["montant"] - pris})
        annees.append({
            "annee": an, "solde_debut": solde, "cotisation": c,
            "travaux": detail,
            "montant_travaux": sum((t["montant"] for t in detail), Decimal("0")),
            "pris_sur_fonds": sum((t["pris_sur_fonds"] for t in detail), Decimal("0")),
            "appel_complementaire": sum((t["appel_complementaire"] for t in detail), Decimal("0")),
            "solde_fin": disponible,
        })
        solde = disponible

    appels = [a for a in annees if a["appel_complementaire"] > 0]
    pire = max(annees, key=lambda a: a["appel_complementaire"]) if appels else None
    return {
        "annee_debut": debut, "annee_fin": debut + duree - 1, "duree": duree,
        "cotisation": c, "indexation_pct": taux * 100, "solde_initial": solde0,
        "annees": annees,
        "totaux": {
            "travaux": sum((a["montant_travaux"] for a in annees), Decimal("0")),
            "cotisations": c * duree,
            "pris_sur_fonds": sum((a["pris_sur_fonds"] for a in annees), Decimal("0")),
            "appels_complementaires": sum((a["appel_complementaire"] for a in annees), Decimal("0")),
            "solde_final": annees[-1]["solde_fin"],
            "annees_avec_appel": [a["annee"] for a in appels],
            "annee_plus_lourde": pire["annee"] if pire else None,
        },
        "notes": notes, "avertissement": AVERTISSEMENT,
    }


def cotisation_lissee(fiche: dict, suivi: dict | None = None, indexation=0,
                      annee_debut: int | None = None, duree: int = DUREE_PLAN_ANS) -> dict:
    """La plus petite cotisation annuelle constante qui finance tout le plan sans
    appel complémentaire : pour chaque année k, solde initial + k × cotisation doit
    couvrir les travaux cumulés jusqu'à k."""
    p = plan_de(fiche)
    if not p:
        raise PlanInvalide("Cette fiche ne contient pas de plan de travaux (section plan_travaux)")
    taux = _d(indexation or 0) / 100
    debut = annee_debut or p["annee_debut"]
    solde0, _ = _solde_initial(fiche)
    par_annee = _travaux_par_annee(fiche, suivi, debut, duree, taux)
    cumul, besoin = Decimal("0"), Decimal("0")
    annee_tendue = None
    for k, an in enumerate(range(debut, debut + duree), start=1):
        cumul += sum((t["montant"] for t in par_annee.get(an, [])), Decimal("0"))
        b = (cumul - solde0) / k
        if b > besoin:
            besoin, annee_tendue = b, an
    sans_appel = (besoin / PAS_LISSAGE).to_integral_value(rounding=ROUND_CEILING) * PAS_LISSAGE
    cmin = cotisation_minimale(fiche)
    return {
        "cotisation_sans_appel": sans_appel.quantize(CENT),
        "annee_determinante": annee_tendue,
        "minimum_applicable": cmin["minimum_applicable"],
        "minimum_si_adopte": cmin["minimum_si_adopte"],
        "cotisation_lissee": max(sans_appel, cmin["minimum_si_adopte"]).quantize(CENT),
        "explication": (f"À ce niveau, le fonds couvre "
                        f"tous les travaux du plan jusqu'en {debut + duree - 1} sans appel complémentaire, "
                        f"et respecte le minimum légal applicable une fois le plan adopté."
                        if annee_tendue else
                        "Le solde actuel du fonds couvre déjà tous les travaux prévus : seul le minimum légal s'impose."),
    }


# --------------------------------------------------------------------------- #
# Quote-part de chaque copropriétaire
# --------------------------------------------------------------------------- #
def _poids_par_coproprietaire(fiche: dict, cle: str) -> dict[str, int]:
    poids: dict[str, int] = defaultdict(int)
    for l in fiche["lots"]:
        poids[l["proprietaire"]] += l["tantiemes"].get(cle, 0)
    return {cid: v for cid, v in poids.items() if v > 0}


def quotes_parts(fiche: dict, proj: dict) -> list[dict]:
    """Par copropriétaire et par année : sa part de la cotisation au fonds et sa part
    des appels complémentaires (selon la clé de chaque ligne)."""
    cle_fonds = fiche["fonds_travaux"]["cle"]
    poids_fonds = _poids_par_coproprietaire(fiche, cle_fonds)
    noms = {c["id"]: c["nom"] for c in fiche["coproprietaires"]}
    res: dict[str, dict] = {cid: {"id": cid, "nom": noms[cid], "annees": {}, "total_cotisations": Decimal("0"),
                                  "total_appels": Decimal("0")} for cid in sorted(noms)}
    for a in proj["annees"]:
        parts_c = repartir(a["cotisation"], poids_fonds) if a["cotisation"] > 0 else {}
        appels_par_cle: dict[str, Decimal] = defaultdict(Decimal)
        for t in a["travaux"]:
            appels_par_cle[t["cle"]] += t["appel_complementaire"]
        parts_a: dict[str, Decimal] = defaultdict(Decimal)
        for cle, montant in appels_par_cle.items():
            if montant > 0:
                for cid, m in repartir(montant, _poids_par_coproprietaire(fiche, cle)).items():
                    parts_a[cid] += m
        for cid, r in res.items():
            cot, app = parts_c.get(cid, Decimal("0")), parts_a.get(cid, Decimal("0"))
            r["annees"][a["annee"]] = {"cotisation": cot, "appels": app, "total": cot + app}
            r["total_cotisations"] += cot
            r["total_appels"] += app
    for r in res.values():
        r["total"] = r["total_cotisations"] + r["total_appels"]
        an_max = max(r["annees"], key=lambda an: r["annees"][an]["total"])
        r["annee_plus_lourde"] = {"annee": an_max, "montant": r["annees"][an_max]["total"]}
    return list(res.values())


# --------------------------------------------------------------------------- #
# Conformité et échéancier
# --------------------------------------------------------------------------- #
def _point(sujet: str, etat: str, message: str) -> dict:
    return {"sujet": sujet, "etat": etat, "message": message}


def conformite(fiche: dict, aujourd_hui: date, suivi: dict | None = None) -> list[dict]:
    """etat : ok | attention | manquant | info"""
    pts: list[dict] = []
    p = plan_de(fiche)
    nb_lots = fiche["copropriete"].get("nb_lots_principaux")

    # 1. Le plan lui-même
    if not p:
        pts.append(_point("Plan de travaux", "manquant",
                          "Aucun plan de travaux dans la fiche. Le projet de plan pluriannuel est obligatoire depuis "
                          "le 1er janvier 2025 pour les copropriétés de 50 lots ou moins dont l'immeuble a plus de "
                          "15 ans. Il doit être commandé à un professionnel."))
    else:
        fin = ajouter_mois(p["elabore_le"], 12 * DUREE_PLAN_ANS)
        if fin <= aujourd_hui:
            pts.append(_point("Plan de travaux", "attention",
                              f"Établi le {p['elabore_le']:%d/%m/%Y} : il doit être actualisé tous les 10 ans "
                              f"(depuis le {fin:%d/%m/%Y})."))
        elif p["statut"] == "projet":
            pts.append(_point("Plan de travaux", "attention",
                              f"Projet établi le {p['elabore_le']:%d/%m/%Y} par {p.get('elabore_par', 'un professionnel')}, "
                              f"pas encore adopté. Le syndic doit le présenter à la prochaine AG ; s'il est adopté, "
                              f"la cotisation minimale au fonds de travaux tiendra compte de ses travaux."))
        else:
            pts.append(_point("Plan de travaux", "ok",
                              f"Adopté en AG le {p['adopte_le']:%d/%m/%Y}, à actualiser avant le {fin:%d/%m/%Y}."))

    # 2. Cotisation au fonds de travaux
    cmin = cotisation_minimale(fiche)
    if not cmin["conforme"]:
        pts.append(_point("Cotisation au fonds de travaux", "manquant",
                          f"{_eur(cmin['cotisation_actuelle'])} par an, sous le minimum légal de "
                          f"{_eur(cmin['minimum_applicable'])} ({cmin['regle'].lower()})."))
    elif p and not cmin["plan_adopte"] and not cmin["conforme_si_adopte"]:
        pts.append(_point("Cotisation au fonds de travaux", "attention",
                          f"{_eur(cmin['cotisation_actuelle'])} par an : conforme aujourd'hui (minimum "
                          f"{_eur(cmin['minimum_budget'])}), mais si le plan est adopté le minimum passera à "
                          f"{_eur(cmin['minimum_si_adopte'])} (2,5 % de {_eur(cmin['montant_plan'])} de travaux)."))
    else:
        pts.append(_point("Cotisation au fonds de travaux", "ok",
                          f"{_eur(cmin['cotisation_actuelle'])} par an, minimum légal {_eur(cmin['minimum_applicable'])}."))

    # 3. Suspension possible des cotisations
    solde0, solde_au = _solde_initial(fiche)
    if p and cmin["plan_adopte"] and solde0 > cmin["montant_plan"] * SEUIL_SUSPENSION_PLAN:
        pts.append(_point("Suspension des cotisations", "info",
                          f"Le fonds ({_eur(solde0)}) dépasse la moitié des travaux du plan : l'AG peut se prononcer "
                          f"sur la suspension des cotisations."))
    elif solde0 > cmin["budget_previsionnel"]:
        pts.append(_point("Suspension des cotisations", "info",
                          f"Le fonds ({_eur(solde0)}) dépasse le budget prévisionnel : l'AG peut se prononcer sur la "
                          f"suspension des cotisations."))

    # 4. DPE collectif
    dpe = next((d for d in fiche.get("diagnostics", []) if d["id"] == "dpe_collectif"), None)
    if nb_lots is not None and nb_lots <= 50 and (not dpe or not dpe.get("realise_le")):
        pts.append(_point("DPE collectif", "manquant",
                          "Pas de DPE collectif renseigné. Il est obligatoire depuis le 1er janvier 2026 pour les "
                          "copropriétés de 50 lots ou moins dont le permis de construire est antérieur à 2013. "
                          "Il sert aussi de base au plan de travaux."))

    # 5. Lignes en retard et couverture financière
    if p:
        for l in lignes(fiche, suivi):
            if l["statut"] in ("prevu", "reporte") and l["annee"] < aujourd_hui.year:
                pts.append(_point(f"Travaux {l['id']}", "attention",
                                  f"« {l['intitule']} » était prévu en {l['annee']} et n'a pas de dossier ouvert : "
                                  f"ouvrir le dossier ou reporter la ligne."))
        proj = projection(fiche, suivi)
        tot = proj["totaux"]
        if tot["appels_complementaires"] > 0:
            ans = ", ".join(str(a) for a in tot["annees_avec_appel"])
            pts.append(_point("Financement du plan", "attention",
                              f"Avec la cotisation actuelle, {_eur(tot['appels_complementaires'])} seront à appeler en "
                              f"plus du fonds sur la durée du plan (années {ans})."))
        else:
            pts.append(_point("Financement du plan", "ok",
                              "Avec la cotisation actuelle, le fonds couvre tous les travaux du plan."))
    ordre = {"manquant": 0, "attention": 1, "info": 2, "ok": 3}
    return sorted(pts, key=lambda x: ordre[x["etat"]])


def alertes_plan(fiche: dict, aujourd_hui: date, suivi: dict | None = None) -> list[dict]:
    """Pour l'échéancier : les travaux du plan prévus cette année ou l'an prochain,
    sans dossier ouvert. Il faut du temps pour les devis et le vote en AG."""
    from fiche_copro import _alerte

    if not plan_de(fiche):
        return []
    res = []
    for l in lignes(fiche, suivi):
        if l["statut"] not in ("prevu", "reporte") or l["annee"] > aujourd_hui.year + 1:
            continue
        debut_annee = date(l["annee"], 1, 1)
        if l["annee"] < aujourd_hui.year:
            msg = f"Prévu en {l['annee']}, toujours sans dossier : ouvrir le dossier ou reporter la ligne"
        else:
            msg = (f"Prévu en {l['annee']} au plan ({_eur(l['montant'])} estimés) : ouvrir le dossier pour demander "
                   f"les devis et l'inscrire à l'ordre du jour de l'AG")
        res.append(_alerte("plan", f"Plan de travaux {l['id']} · {l['intitule']}", msg, debut_annee, aujourd_hui,
                           niveau="a_prevoir"))
    return res


# --------------------------------------------------------------------------- #
# PDF
# --------------------------------------------------------------------------- #
def _e(m: Decimal) -> str:
    """Montant arrondi à l'euro, pour les tableaux denses : 12345.67 -> '12 346 €'."""
    return f"{m.quantize(Decimal('1')):,}".replace(",", "\u00a0") + "\u00a0€"   # espaces insécables


def en_bref(fiche: dict, proj: dict, lissage: dict, cmin: dict) -> str:
    tot = proj["totaux"]
    phrases = [f"Le plan prévoit {_e(tot['travaux'])} de travaux de {proj['annee_debut']} à {proj['annee_fin']}."]
    if tot["appels_complementaires"] > 0:
        phrases.append(f"Avec une cotisation de {_e(proj['cotisation'])} par an et un fonds de "
                       f"{_e(proj['solde_initial'])} au départ, il faudra appeler {_e(tot['appels_complementaires'])} "
                       f"en plus, surtout en {tot['annee_plus_lourde']}.")
    else:
        phrases.append(f"Avec une cotisation de {_e(proj['cotisation'])} par an, le fonds couvre tous ces travaux.")
    if lissage["annee_determinante"]:
        phrases.append(f"Une cotisation lissée de {_e(lissage['cotisation_lissee'])} par an financerait tout le "
                       f"plan sans appel exceptionnel.")
    if not cmin["plan_adopte"] and cmin["minimum_si_adopte"] > cmin["minimum_budget"]:
        phrases.append(f"Si le plan est adopté en AG, la cotisation minimale légale passera de "
                       f"{_e(cmin['minimum_budget'])} à {_e(cmin['minimum_si_adopte'])} par an.")
    return " ".join(phrases)


def generer_pdf(fiche: dict, chemin: Path, suivi: dict | None = None, cotisation=None, indexation=0,
                aujourd_hui: date | None = None) -> Path:
    from reportlab.graphics.charts.barcharts import VerticalBarChart
    from reportlab.graphics.charts.legends import Legend
    from reportlab.graphics.shapes import Drawing, String
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    from avis_appel import ACCENT, FOND, GRIS, S, tableau, tampon

    jour = aujourd_hui or date.today()
    p = plan_de(fiche)
    proj = projection(fiche, suivi, cotisation, indexation)
    lis = cotisation_lissee(fiche, suivi, indexation)
    cmin = cotisation_minimale(fiche)
    conf = conformite(fiche, jour, suivi)
    qp = quotes_parts(fiche, proj)
    ident = fiche["copropriete"]
    ROUGE = colors.HexColor("#C2410C")

    doc = SimpleDocTemplate(str(chemin), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=16 * mm, bottomMargin=20 * mm,
                            title=f"Plan de travaux – {ident['nom']}")
    statut = (f"adopté en AG le {p['adopte_le']:%d/%m/%Y}" if p["statut"] == "adopte" else "projet, pas encore adopté")
    h = [Paragraph("Plan de travaux · projection sur 10 ans", S["titre"]),
         Paragraph(f"{ident['nom']} · plan établi le {p['elabore_le']:%d/%m/%Y} par {p.get('elabore_par', '—')} "
                   f"({statut}) · projection du {jour:%d/%m/%Y}", S["sous"]),
         Spacer(1, 5 * mm)]

    bref = Table([[Paragraph(f"<b>En bref</b><br/>{en_bref(fiche, proj, lis, cmin)}", S["txt"])]], colWidths=[174 * mm])
    bref.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.8, ACCENT),
                              ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                              ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]))
    h += [bref, Spacer(1, 6 * mm)]

    # Graphique : travaux de chaque année, part payée par le fonds / part à appeler en plus
    largeur, hauteur = 174 * mm, 70 * mm
    dessin = Drawing(largeur, hauteur)
    g = VerticalBarChart()
    g.x, g.y, g.width, g.height = 16 * mm, 14 * mm, largeur - 20 * mm, hauteur - 24 * mm
    g.data = [[float(a["pris_sur_fonds"]) for a in proj["annees"]],
              [float(a["appel_complementaire"]) for a in proj["annees"]]]
    g.categoryAxis.categoryNames = [str(a["annee"]) for a in proj["annees"]]
    g.categoryAxis.style = "stacked"
    g.categoryAxis.labels.fontSize = 7.5
    g.categoryAxis.labels.fontName = "Helvetica"
    g.valueAxis.labels.fontName = "Helvetica"
    g.categoryAxis.labels.fillColor = GRIS
    g.categoryAxis.strokeColor = GRIS
    g.valueAxis.valueMin = 0
    g.valueAxis.labels.fontSize = 7
    g.valueAxis.labels.fillColor = GRIS
    g.valueAxis.labelTextFormat = lambda v: f"{int(v / 1000)} k€" if v else "0"
    g.valueAxis.strokeColor = colors.white
    g.valueAxis.gridStrokeColor = colors.HexColor("#E4E7EB")
    g.valueAxis.visibleGrid = True
    g.bars[0].fillColor, g.bars[1].fillColor = ACCENT, ROUGE
    g.bars.strokeColor = None
    g.barWidth = 8
    dessin.add(g)
    leg = Legend()
    leg.x, leg.y = 18 * mm, hauteur - 3 * mm
    leg.fontSize, leg.fontName = 7.5, "Helvetica"
    leg.columnMaximum, leg.alignment, leg.dx, leg.dy, leg.dxTextSpace = 1, "right", 7, 7, 4
    leg.deltax = 55 * mm
    leg.colorNamePairs = [(ACCENT, "Payé par le fonds de travaux"), (ROUGE, "À appeler en plus")]
    dessin.add(leg)
    dessin.add(String(largeur - 4 * mm, hauteur - 3 * mm, f"Cotisation simulée : {_e(proj['cotisation'])} / an",
                      fontName="Helvetica", fontSize=7.5, fillColor=GRIS, textAnchor="end"))
    h += [Paragraph("Les travaux de chaque année et leur financement", S["sous"]), Spacer(1, 1 * mm), dessin,
          Spacer(1, 4 * mm)]

    # Tableau année par année
    t = [["Année", "Travaux", "Solde début", "Cotisation", "Pris sur le fonds", "À appeler en plus", "Solde fin"]]
    for a in proj["annees"]:
        t.append([str(a["annee"]), _e(a["montant_travaux"]) if a["montant_travaux"] else "—", _e(a["solde_debut"]),
                  _e(a["cotisation"]), _e(a["pris_sur_fonds"]) if a["pris_sur_fonds"] else "—",
                  _e(a["appel_complementaire"]) if a["appel_complementaire"] else "—", _e(a["solde_fin"])])
    tot = proj["totaux"]
    t.append(["Total", _e(tot["travaux"]), "", _e(tot["cotisations"]), _e(tot["pris_sur_fonds"]),
              _e(tot["appels_complementaires"]), _e(tot["solde_final"])])
    h.append(tableau(t, [16 * mm, 26 * mm, 26 * mm, 24 * mm, 28 * mm, 30 * mm, 24 * mm], aligner_droite_depuis=1))
    if proj["notes"]:
        h += [Spacer(1, 2 * mm), Paragraph("<br/>".join(proj["notes"]), S["petit"])]
    h.append(Spacer(1, 6 * mm))

    # Cotisation
    bloc = (f"<b>Cotisation au fonds de travaux</b><br/>"
            f"Actuelle : {_e(cmin['cotisation_actuelle'])} par an.<br/>"
            f"Minimum légal aujourd'hui : {_e(cmin['minimum_applicable'])} ({cmin['regle'].lower()}).<br/>"
            + (f"Minimum si le plan est adopté : {_e(cmin['minimum_si_adopte'])} "
               f"(2,5 % de {_e(cmin['montant_plan'])} de travaux).<br/>" if not cmin["plan_adopte"] else "")
            + f"Cotisation lissée : {_e(lis['cotisation_lissee'])} par an. {lis['explication']}")
    encadre = Table([[Paragraph(bloc, S["txt"])]], colWidths=[174 * mm])
    encadre.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), FOND),
                                 ("LINEBEFORE", (0, 0), (0, -1), 2.5, ACCENT),
                                 ("LEFTPADDING", (0, 0), (-1, -1), 10),
                                 ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]))
    h += [KeepTogether([encadre]), Spacer(1, 6 * mm)]

    # Lignes du plan
    cles = fiche["cles_repartition"]
    tl = [["", "Travaux", "Catégorie", "Prio.", "Année", "Suivi", "Estimation"]]
    for l in lignes(fiche, suivi):
        annee = str(l["annee"]) + (f" (au lieu de {l['annee_prevue']})" if l["annee"] != l["annee_prevue"] else "")
        cle = "" if l["cle"] == fiche["fonds_travaux"]["cle"] else f"<br/><font size='7' color='#616E7C'>clé {cles[l['cle']]['libelle'].lower()}</font>"
        tl.append([l["id"], Paragraph(l["intitule"] + cle, S["txt"]), Paragraph(l["categorie_libelle"], S["txt"]),
                   str(l["priorite"]), Paragraph(annee, S["txt"]),
                   Paragraph(l["statut_libelle"] + (f"<br/><font size='7'>{l['dossier']}</font>" if l["dossier"] else ""),
                             S["txt"]), _e(l["montant"])])
    tl.append(["", "Total", "", "", "", "", _e(cmin["montant_plan"])])
    h += [Paragraph("Les travaux du plan", S["sous"]), Spacer(1, 2 * mm),
          tableau(tl, [10 * mm, 60 * mm, 28 * mm, 11 * mm, 19 * mm, 25 * mm, 21 * mm], aligner_droite_depuis=6),
          Spacer(1, 6 * mm)]

    # Quote-part de chaque copropriétaire
    base = sum(_poids_par_coproprietaire(fiche, fiche["fonds_travaux"]["cle"]).values())
    tq = [["Copropriétaire", "Tantièmes", "Cotisation / an", "Appels en plus", "Année la plus lourde", "Total 10 ans"]]
    poids = _poids_par_coproprietaire(fiche, fiche["fonds_travaux"]["cle"])
    for q in qp:
        an = q["annee_plus_lourde"]
        cot_an = q["annees"][proj["annee_debut"]]["cotisation"]
        tq.append([q["nom"], f"{poids.get(q['id'], 0)} / {base}", _e(cot_an),
                   _e(q["total_appels"]) if q["total_appels"] else "—",
                   f"{an['annee']} : {_e(an['montant'])}", _e(q["total"])])
    tq.append(["Total", "", _e(proj["cotisation"]), _e(tot["appels_complementaires"]), "",
               _e(sum((q["total"] for q in qp), Decimal("0")))])
    h += [Paragraph("Ce que cela représente pour chaque copropriétaire", S["sous"]), Spacer(1, 2 * mm),
          tableau(tq, [44 * mm, 24 * mm, 24 * mm, 24 * mm, 34 * mm, 24 * mm], aligner_droite_depuis=1),
          Spacer(1, 6 * mm)]

    # Conformité
    sym = {"manquant": "<b>À régulariser :</b>", "attention": "<b>À surveiller :</b>", "info": "Info :", "ok": "En règle :"}
    lignes_conf = "<br/>".join(f"• {sym[c['etat']]} <b>{c['sujet']}</b>. {c['message']}" for c in conf)
    h += [KeepTogether([Paragraph("Points de conformité", S["sous"]), Spacer(1, 2 * mm),
                        Paragraph(lignes_conf, S["txt"])]),
          Spacer(1, 5 * mm), Paragraph(AVERTISSEMENT, S["petit"])]

    pied = f"{ident['nom']} – plan de travaux préparé par l'assistant du syndic"
    doc.build(h, onFirstPage=tampon(False, pied), onLaterPages=tampon(False, pied))
    return chemin


# --------------------------------------------------------------------------- #
# Ligne de commande
# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(description="Projection du plan pluriannuel de travaux.")
    ap.add_argument("fiche")
    ap.add_argument("--cotisation", help="Cotisation annuelle à simuler (défaut : celle de la fiche)")
    ap.add_argument("--indexation", default="0", help="Hausse annuelle des prix en %% (ex. 3)")
    ap.add_argument("--date", help="Date de référence AAAA-MM-JJ")
    ap.add_argument("--pdf", help="Chemin du PDF à générer")
    args = ap.parse_args()

    fiche = charger_fiche(args.fiche)
    erreurs = valider_plan(fiche)
    if erreurs:
        print("❌ Plan invalide :\n" + "\n".join(f"   - {e}" for e in erreurs))
        return 1
    jour = date.fromisoformat(args.date) if args.date else date.today()
    proj = projection(fiche, cotisation=args.cotisation, indexation=args.indexation)
    lis = cotisation_lissee(fiche, indexation=args.indexation)
    cmin = cotisation_minimale(fiche)

    print(f"\n=== Plan de travaux · {fiche['copropriete']['nom']} ===\n")
    print(en_bref(fiche, proj, lis, cmin) + "\n")
    print(f"   {'Année':<6}{'Travaux':>11}{'Fonds':>11}{'En plus':>11}{'Solde fin':>12}")
    for a in proj["annees"]:
        print(f"   {a['annee']:<6}{_e(a['montant_travaux']):>11}{_e(a['pris_sur_fonds']):>11}"
              f"{_e(a['appel_complementaire']):>11}{_e(a['solde_fin']):>12}")
    print("\n--- Conformité ---")
    for c in conformite(fiche, jour):
        print(f"   [{c['etat']}] {c['sujet']} : {c['message']}")
    if args.pdf:
        generer_pdf(fiche, Path(args.pdf), cotisation=args.cotisation, indexation=args.indexation, aujourd_hui=jour)
        print(f"\nPDF : {args.pdf}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
