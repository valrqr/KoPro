"""
ag.py — L'assemblée générale sans papier.

Ce que prépare le module, du calendrier au procès-verbal :

    1. le CALENDRIER LÉGAL d'une AG : date limite de convocation, de réception des
       votes par correspondance, de notification du procès-verbal ;
    2. le FORMULAIRE DE VOTE PAR CORRESPONDANCE de chaque copropriétaire, sur la
       structure du modèle de l'arrêté du 2 juillet 2020, pré-rempli ;
    3. l'enregistrement des formulaires reçus (refus s'ils arrivent hors délai)
       et des MANDATS (plafond de délégations, interdiction pour le syndic) ;
    4. la FEUILLE DE PRÉSENCE : présents, représentés, en visio, par correspondance
       (avec la date de réception du formulaire), absents ;
    5. le DÉPOUILLEMENT : votes en séance + votes par correspondance, avec la règle
       des résolutions amendées et les majorités des articles 24, 25, 25-1 et 26 ;
    6. un BROUILLON DE PROCÈS-VERBAL : résultat de chaque résolution, noms et voix
       des opposants, des abstentionnistes et des défaillants.

Rappels légaux utilisés (loi du 10 juillet 1965, décret du 17 mars 1967) :
    - convocation notifiée au moins 21 jours avant la réunion (décret, art. 9) ;
    - formulaire de vote par correspondance joint à la convocation (décret, art. 9)
      et reçu par le syndic au plus tard 3 jours francs avant la réunion (art. 9 bis) ;
      envoyé par mail, il est présumé reçu à la date d'envoi ;
    - formulaire écarté si le copropriétaire est présent ou représenté (art. 14-1) ;
    - résolution amendée en séance : le vote « pour » par correspondance compte comme
      défaillant (loi, art. 17-1 A) ;
    - procès-verbal : noms et voix des opposants, abstentionnistes et défaillants
      (décret, art. 17) ; notifié dans le mois aux opposants et défaillants, qui ont
      alors 2 mois pour contester (loi, art. 42) ;
    - mandats : 3 délégations au plus par mandataire, sauf si ses voix et celles de
      ses mandants ne dépassent pas 10 % des voix ; le syndic ne peut pas en recevoir
      (loi, art. 22).

⚠️ Aide à la préparation. Le formulaire suit la structure du modèle officiel ; avant
un usage réel, le comparer à l'annexe de l'arrêté sur Légifrance. Le procès-verbal
signé par le bureau de l'AG reste la seule référence.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path

import yaml

import vote

MAJORITES = {"24": "Article 24 (majorité des voix exprimées)",
             "25": "Article 25 (majorité des voix de tous les copropriétaires)",
             "26": "Article 26 (double majorité)",
             "info": "Point d'information, sans vote"}
CHOIX = ("pour", "contre", "abstention")
MODES_PRESENCE = ("present", "visio", "represente")
DELAI_CONVOCATION_JOURS = 21
MARGE_ENVOI_JOURS = 7            # délai d'acheminement d'une lettre recommandée, par prudence
JOURS_FRANCS_FORMULAIRE = 3
MAX_DELEGATIONS = 3
PLAFOND_VOIX_MANDATS = 0.10
ID_AG = re.compile(r"^\d{4}-\d{2}-\d{2}(-[a-z0-9-]{1,30})?$")
AVERTISSEMENT = ("Document préparé par l'assistant du syndic, à partir des règles de la loi du 10 juillet 1965 "
                 "et du décret du 17 mars 1967. Il ne remplace ni le procès-verbal signé par le bureau de "
                 "l'assemblée, ni l'avis d'un juriste ou de l'ADIL.")


class AGInvalide(ValueError):
    pass


# --------------------------------------------------------------------------- #
# Calendrier légal
# --------------------------------------------------------------------------- #
def limite_jours_francs(reunion: date, jours: int = JOURS_FRANCS_FORMULAIRE) -> date:
    """Dernier jour de réception : il faut `jours` jours entiers entre la réception et la
    réunion, sans compter ni l'un ni l'autre. AG le samedi 14 → reçu au plus tard le mardi 10."""
    return reunion - timedelta(days=jours + 1)


def calendrier(fiche: dict, ag: dict, aujourd_hui: date | None = None) -> dict:
    jour = aujourd_hui or date.today()
    d = date.fromisoformat(ag["date"])
    # Le délai de 21 jours court à partir du lendemain de la première présentation de la
    # lettre : on retient la veille, par prudence, comme dernière date de notification.
    limite_notification = d - timedelta(days=DELAI_CONVOCATION_JOURS + 1)
    envoi_conseille = limite_notification - timedelta(days=MARGE_ENVOI_JOURS)
    limite_vpc = limite_jours_francs(d)
    pv = d + timedelta(days=30)
    etapes = [
        {"etape": "Envoi des convocations (conseillé)", "date": envoi_conseille,
         "detail": "Laisse une semaine d'acheminement aux lettres recommandées"},
        {"etape": "Notification des convocations (au plus tard)", "date": limite_notification,
         "detail": "Au moins 21 jours avant la réunion, formulaire de vote par correspondance joint"},
        {"etape": "Réception des votes par correspondance et des demandes de visio", "date": limite_vpc,
         "detail": "3 jours francs avant la réunion ; un formulaire envoyé par mail compte à sa date d'envoi"},
        {"etape": "Assemblée générale", "date": d, "detail": f"{ag.get('heure', '')} · {ag.get('lieu', '')}".strip(" ·")},
        {"etape": "Notification du procès-verbal aux opposants et défaillants", "date": pv,
         "detail": "Dans le mois qui suit l'AG ; ils ont ensuite 2 mois pour contester (loi, art. 42)"},
    ]
    for e in etapes:
        e["jours"] = (e["date"] - jour).days
        e["depasse"] = e["jours"] < 0 and e["etape"] != "Assemblée générale"
        e["date_fr"] = f"{e['date']:%d/%m/%Y}"
        e["date"] = e["date"].isoformat()
    alertes = []
    if (limite_notification - jour).days < 0 and (d - jour).days >= 0:
        alertes.append(f"Trop tard pour convoquer dans les délais : la notification devait intervenir au plus tard "
                       f"le {limite_notification:%d/%m/%Y}. Choisir une date d'AG plus lointaine.")
    # AG annuelle : dans les N mois suivant la clôture de l'exercice
    gouv = fiche["gouvernance"]
    mm, jj = (int(x) for x in gouv["exercice"]["fin"].split("-"))
    cloture = date(d.year, mm, jj)
    if cloture >= d:
        cloture = date(d.year - 1, mm, jj)
    from fiche_copro import ajouter_mois
    limite_annuelle = ajouter_mois(cloture, gouv.get("ag_annuelle_delai_mois", 6))
    if ag.get("annuelle", True) and d > limite_annuelle:
        alertes.append(f"L'AG annuelle doit se tenir avant le {limite_annuelle:%d/%m/%Y} "
                       f"(6 mois après la clôture du {cloture:%d/%m/%Y}).")
    return {"etapes": etapes, "limite_notification": limite_notification.isoformat(),
            "limite_correspondance": limite_vpc.isoformat(), "alertes": alertes}


# --------------------------------------------------------------------------- #
# Création et stockage
# --------------------------------------------------------------------------- #
def ag_dir(racine: Path, copro: str, ag_id: str) -> Path:
    if not ID_AG.match(ag_id):
        raise AGInvalide(f"Identifiant d'AG invalide : {ag_id}")
    return Path(racine) / copro / "ag" / ag_id


def charger_ag(racine: Path, copro: str, ag_id: str) -> dict:
    chemin = ag_dir(racine, copro, ag_id) / "ag.yaml"
    if not chemin.is_file():
        raise FileNotFoundError(ag_id)
    with open(chemin, encoding="utf-8") as f:
        return yaml.safe_load(f)


def sauver_ag(racine: Path, copro: str, ag: dict) -> None:
    chemin = ag_dir(racine, copro, ag["id"])
    chemin.mkdir(parents=True, exist_ok=True)
    with open(chemin / "ag.yaml", "w", encoding="utf-8") as f:
        yaml.safe_dump(ag, f, allow_unicode=True, sort_keys=False)


def lister_ag(racine: Path, copro: str) -> list[dict]:
    base = Path(racine) / copro / "ag"
    if not base.is_dir():
        return []
    res = []
    for p in sorted(base.glob("*/ag.yaml")):
        with open(p, encoding="utf-8") as f:
            a = yaml.safe_load(f)
        res.append({"id": a["id"], "date": a["date"], "titre": a["titre"], "nb_resolutions": len(a["resolutions"]),
                    "depouillee": bool(a.get("depouillement"))})
    return res


def _valider_resolutions(fiche: dict, resolutions: list[dict]) -> list[dict]:
    if not resolutions:
        raise AGInvalide("L'ordre du jour doit comporter au moins une résolution")
    res = []
    for i, r in enumerate(resolutions, 1):
        titre = (r.get("titre") or "").strip()
        if not titre:
            raise AGInvalide(f"Résolution {i} : titre obligatoire")
        maj = str(r.get("majorite", "24"))
        if maj not in MAJORITES:
            raise AGInvalide(f"Résolution {i} : majorité {maj} inconnue (24, 25, 26 ou info)")
        cle = r.get("cle", "generales")
        if cle not in fiche["cles_repartition"]:
            raise AGInvalide(f"Résolution {i} : clé de répartition inconnue {cle}")
        res.append({"num": i, "titre": titre[:200], "texte": (r.get("texte") or "").strip()[:4000],
                    "majorite": maj, "cle": cle, **({"dossier": r["dossier"]} if r.get("dossier") else {})})
    return res


def creer_ag(racine: Path, copro: str, fiche: dict, date_ag: date, heure: str, lieu: str,
             resolutions: list[dict], titre: str = "Assemblée générale ordinaire", annuelle: bool = True,
             visio: str | None = None, suffixe: str = "", aujourd_hui: date | None = None) -> dict:
    jour = aujourd_hui or date.today()
    if date_ag <= jour:
        raise AGInvalide("La date de l'AG doit être dans le futur")
    ag_id = date_ag.isoformat() + (f"-{suffixe}" if suffixe else "")
    if (ag_dir(racine, copro, ag_id) / "ag.yaml").is_file():
        raise AGInvalide(f"Une AG {ag_id} existe déjà")
    ag = {"id": ag_id, "titre": titre.strip()[:120], "annuelle": annuelle, "date": date_ag.isoformat(),
          "heure": heure.strip()[:20], "lieu": lieu.strip()[:200],
          "visio": (visio or "").strip()[:300] or None,
          "resolutions": _valider_resolutions(fiche, resolutions),
          "correspondance": {}, "presences": {}, "cree_le": jour.isoformat()}
    sauver_ag(racine, copro, ag)
    return ag


def ordre_du_jour_type(fiche: dict, plan_adopte: bool = False) -> list[dict]:
    """Résolutions habituelles d'une AG annuelle, avec les majorités usuelles.
    À adapter : le règlement de copropriété et la situation de la copro priment."""
    exercice = fiche["budget_previsionnel"]["exercice"]
    ft = fiche["fonds_travaux"]["cotisation_annuelle"]
    odj = [
        {"titre": f"Approbation des comptes de l'exercice {exercice - 1}", "majorite": "24"},
        {"titre": f"Approbation du budget prévisionnel de l'exercice {exercice + 1}", "majorite": "24"},
        {"titre": f"Montant de la cotisation annuelle au fonds de travaux ({ft} €)", "majorite": "25"},
        {"titre": "Désignation du syndic et approbation de son contrat", "majorite": "25"},
        {"titre": "Désignation des membres du conseil syndical", "majorite": "25"},
    ]
    if fiche.get("plan_travaux") and not plan_adopte:
        odj.append({"titre": "Présentation du projet de plan pluriannuel de travaux",
                    "texte": "Le projet est présenté avant son éventuelle adoption.",
                    "majorite": "info"})
    return odj


def resolutions_depuis_dossier(dossier: dict, majorite: str = "24") -> list[dict]:
    """Une résolution par devis d'un dossier de travaux (votées dans l'ordre, la
    première adoptée l'emporte)."""
    import travaux
    return [{"titre": f"{dossier['titre']} : devis {d['id']} de {d['entreprise']} ({travaux.montant_ttc(d)} € TTC)",
             "majorite": majorite, "cle": dossier["cle"], "dossier": dossier["id"]}
            for d in sorted(dossier["devis"], key=lambda d: travaux.montant_ttc(d))]


# --------------------------------------------------------------------------- #
# Votes par correspondance et mandats
# --------------------------------------------------------------------------- #
def _coproprietaire(fiche: dict, cid: str) -> dict:
    c = next((c for c in fiche["coproprietaires"] if c["id"] == cid), None)
    if not c:
        raise AGInvalide(f"Copropriétaire inconnu : {cid}")
    return c


def enregistrer_correspondance(fiche: dict, ag: dict, cid: str, votes: dict, recu_le: date,
                               mode: str = "courrier") -> dict:
    """Formulaire reçu. votes : {numéro de résolution: pour|contre|abstention|None}.
    Une case vide reste vide : la loi ne dit pas comment la traiter, le syndic tranchera."""
    _coproprietaire(fiche, cid)
    limite = date.fromisoformat(calendrier(fiche, ag)["limite_correspondance"])
    if recu_le > limite:
        raise AGInvalide(f"Formulaire reçu le {recu_le:%d/%m/%Y}, après la limite du {limite:%d/%m/%Y} "
                         f"(3 jours francs avant l'AG) : il ne peut pas être pris en compte")
    if mode not in ("courrier", "email", "main_propre"):
        raise AGInvalide(f"Mode de réception inconnu : {mode}")
    nums = {r["num"] for r in ag["resolutions"]}
    propres = {}
    for k, v in (votes or {}).items():
        n = int(k)
        if n not in nums:
            raise AGInvalide(f"Résolution {n} absente de l'ordre du jour")
        if v not in (*CHOIX, None, ""):
            raise AGInvalide(f"Résolution {n} : choix invalide « {v} »")
        propres[n] = v or None
    ag["correspondance"][cid] = {"recu_le": recu_le.isoformat(), "mode": mode,
                                 "votes": {n: propres.get(n) for n in sorted(nums)}}
    return ag["correspondance"][cid]


def enregistrer_presences(fiche: dict, ag: dict, presences: dict) -> list[str]:
    """presences : {cid: {"mode": present|visio|represente, "mandataire": "CP07" ou "Nom Prénom"}}.
    Renvoie les remarques (formulaires écartés). Lève AGInvalide si un mandat est irrégulier."""
    ids = {c["id"] for c in fiche["coproprietaires"]}
    for cid, p in presences.items():
        if cid not in ids:
            raise AGInvalide(f"Copropriétaire inconnu : {cid}")
        if p.get("mode") not in MODES_PRESENCE:
            raise AGInvalide(f"{cid} : mode de présence inconnu {p.get('mode')}")
        if p["mode"] == "represente":
            m = (p.get("mandataire") or "").strip()
            if not m:
                raise AGInvalide(f"{cid} : le mandataire est obligatoire pour un copropriétaire représenté")
            if m == cid:
                raise AGInvalide(f"{cid} ne peut pas être son propre mandataire")
    erreurs = verifier_mandats(fiche, presences)
    if erreurs:
        raise AGInvalide(" ; ".join(erreurs))
    ag["presences"] = {cid: {"mode": p["mode"], **({"mandataire": p["mandataire"].strip()}
                                                    if p["mode"] == "represente" else {})}
                       for cid, p in presences.items()}
    return [f"Le formulaire de vote par correspondance de {cid} est écarté : il est présent ou représenté "
            f"(décret, art. 14-1)" for cid in ag["presences"] if cid in ag.get("correspondance", {})]


def verifier_mandats(fiche: dict, presences: dict) -> list[str]:
    syndic = fiche["gouvernance"]["syndic"]["coproprietaire_id"]
    voix = vote.electorat(fiche, "generales")
    total = sum(voix.values())
    par_mandataire: dict[str, list[str]] = {}
    for cid, p in presences.items():
        if p.get("mode") == "represente":
            par_mandataire.setdefault(p["mandataire"].strip(), []).append(cid)
    err = []
    for m, mandants in par_mandataire.items():
        if m == syndic:
            err.append(f"Le syndic ({m}) ne peut pas recevoir de mandat (loi, art. 22)")
        if len(mandants) > MAX_DELEGATIONS:
            cumul = voix.get(m, 0) + sum(voix.get(c, 0) for c in mandants)
            if cumul > total * PLAFOND_VOIX_MANDATS:
                err.append(f"{m} reçoit {len(mandants)} mandats ({', '.join(sorted(mandants))}) pour "
                           f"{cumul} voix sur {total} : au-delà de 3 mandats, le total ne doit pas dépasser "
                           f"10 % des voix (loi, art. 22)")
    return err


# --------------------------------------------------------------------------- #
# Feuille de présence
# --------------------------------------------------------------------------- #
def feuille_presence(fiche: dict, ag: dict) -> dict:
    voix = vote.electorat(fiche, "generales")
    lots = {}
    for l in fiche["lots"]:
        lots.setdefault(l["proprietaire"], []).append(l["numero"])
    noms = {c["id"]: c["nom"] for c in fiche["coproprietaires"]}
    lignes = []
    for c in sorted(fiche["coproprietaires"], key=lambda c: c["nom"]):
        cid = c["id"]
        p = ag.get("presences", {}).get(cid)
        vpc = ag.get("correspondance", {}).get(cid)
        if p:
            mode = p["mode"]
            libelle = {"present": "Présent", "visio": "À distance (visio)",
                       "represente": "Représenté par " + (f"{noms[p['mandataire']]} ({p['mandataire']})"
                                                   if p.get("mandataire") in noms else str(p.get("mandataire")))}[mode]
        elif vpc:
            mode, libelle = "correspondance", f"Par correspondance (reçu le {date.fromisoformat(vpc['recu_le']):%d/%m/%Y})"
        else:
            mode, libelle = "absent", "Absent"
        lignes.append({"id": cid, "nom": c["nom"], "lots": lots.get(cid, []), "voix": voix.get(cid, 0),
                       "mode": mode, "libelle": libelle, "emargement": mode in ("present", "represente")})
    tot = lambda modes: sum(l["voix"] for l in lignes if l["mode"] in modes)
    return {"lignes": lignes, "total_voix": sum(voix.values()),
            "voix_presentes_representees": tot(("present", "visio", "represente")),
            "voix_correspondance": tot(("correspondance",)), "voix_absentes": tot(("absent",)),
            "nb_presents_representes": sum(1 for l in lignes if l["mode"] in ("present", "visio", "represente")),
            "nb_correspondance": sum(1 for l in lignes if l["mode"] == "correspondance")}


# --------------------------------------------------------------------------- #
# Dépouillement
# --------------------------------------------------------------------------- #
def depouiller(fiche: dict, ag: dict, votes_seance: dict, amendees: list[int] | None = None) -> dict:
    """votes_seance : {numéro: {"contre": [cid…], "abstention": [cid…], "second_vote_24": {...}}}.
    En séance, le président demande qui vote contre et qui s'abstient : les autres
    participants (présents, visio, représentés) votent pour. Les mandants sont désignés
    par leur propre identifiant."""
    amendees = set(int(n) for n in (amendees or []))
    presences = ag.get("presences", {})
    en_seance = set(presences)
    vpc = {cid: v for cid, v in ag.get("correspondance", {}).items() if cid not in en_seance}
    noms = {c["id"]: c["nom"] for c in fiche["coproprietaires"]}
    resultats, retenu_par_dossier = [], {}

    def bulletins_pour(r: dict, saisie: dict, voix: dict) -> tuple[dict, list, list]:
        contre = set(saisie.get("contre") or [])
        abst = set(saisie.get("abstention") or [])
        inconnus = (contre | abst) - en_seance
        if inconnus:
            raise AGInvalide(f"Résolution {r['num']} : {', '.join(sorted(inconnus))} ne participe pas à la séance")
        if contre & abst:
            raise AGInvalide(f"Résolution {r['num']} : {', '.join(sorted(contre & abst))} à la fois contre et abstention")
        b = {}
        for cid in en_seance:
            if cid in voix:
                b[cid] = "contre" if cid in contre else "abstention" if cid in abst else "pour"
        defaillants, sans_choix = [], []
        for cid, f in vpc.items():
            if cid not in voix:
                continue
            choix = f["votes"].get(r["num"]) if isinstance(f["votes"], dict) else None
            if choix is None:
                choix = f["votes"].get(str(r["num"])) if isinstance(f["votes"], dict) else None
            if not choix:
                sans_choix.append(cid)
            elif r["num"] in amendees and choix == "pour":
                defaillants.append(cid)          # loi, art. 17-1 A
            else:
                b[cid] = choix
        return b, defaillants, sans_choix

    for r in ag["resolutions"]:
        if r["majorite"] == "info":
            resultats.append({**{k: r[k] for k in ("num", "titre", "majorite", "cle")}, "information": True,
                              "amendee": False, "decompte": None, "adoptee": None, "opposants": [],
                              "abstentionnistes": [], "defaillants_17_1_A": [], "formulaires_sans_choix": []})
            continue
        voix = vote.electorat(fiche, r["cle"])
        saisie = (votes_seance or {}).get(r["num"]) or (votes_seance or {}).get(str(r["num"])) or {}
        b, defaillants, sans_choix = bulletins_pour(r, saisie, voix)
        d = vote.decompter(b, voix, r["majorite"])
        res = {**{k: r[k] for k in ("num", "titre", "majorite", "cle")}, "amendee": r["num"] in amendees,
               "decompte": d, "adoptee": d["adoptee"],
               "opposants": [{"id": c, "nom": noms[c], "voix": voix[c]} for c, ch in sorted(b.items()) if ch == "contre"],
               "abstentionnistes": [{"id": c, "nom": noms[c], "voix": voix[c]} for c, ch in sorted(b.items()) if ch == "abstention"],
               "defaillants_17_1_A": [{"id": c, "nom": noms[c], "voix": voix[c]} for c in sorted(defaillants)],
               "formulaires_sans_choix": [{"id": c, "nom": noms[c], "voix": voix[c]} for c in sorted(sans_choix)]}
        if r["majorite"] == "25" and not d["adoptee"] and d.get("passerelle_25_1"):
            second = saisie.get("second_vote_24")
            if second is not None:
                b2, def2, _ = bulletins_pour(r, second, voix)
                d2 = vote.decompter(b2, voix, "24")
                res["second_vote_24"] = {"decompte": d2, "adoptee": d2["adoptee"],
                                         "note": "Second vote immédiat à la majorité de l'article 24 (art. 25-1). "
                                                 "Les votes par correspondance y sont repris tels quels : à vérifier."}
                res["adoptee"] = d2["adoptee"]
            else:
                res["note"] = ("La résolution a recueilli au moins un tiers des voix : l'AG peut procéder "
                               "immédiatement à un second vote à la majorité de l'article 24 (art. 25-1).")
        if r["majorite"] == "26" and not d["adoptee"] and d.get("passerelle_26_1"):
            second = saisie.get("second_vote_25")
            if second is not None:
                b2, _, _ = bulletins_pour(r, second, voix)
                d2 = vote.decompter(b2, voix, "25")
                res["second_vote_25"] = {"decompte": d2, "adoptee": d2["adoptee"],
                                         "note": "Second vote immédiat à la majorité de l'article 25 (art. 26-1). "
                                                 "Les votes par correspondance y sont repris tels quels : à vérifier."}
                res["adoptee"] = d2["adoptee"]
            else:
                res["note"] = ("La moitié des votants, représentant au moins un tiers des voix, a approuvé : l'AG "
                               "peut procéder immédiatement à un second vote à la majorité de l'article 25 (art. 26-1).")
        if r.get("dossier"):
            if res["adoptee"] and r["dossier"] not in retenu_par_dossier:
                retenu_par_dossier[r["dossier"]] = r["num"]
                res["retenue"] = True
            elif res["adoptee"]:
                res["retenue"] = False
                res["note"] = "Adoptée, mais une résolution précédente du même dossier l'a déjà emporté."
        resultats.append(res)

    a_notifier = {}
    for res in resultats:
        for p in res["opposants"] + res["defaillants_17_1_A"]:
            a_notifier[p["id"]] = p["nom"]
    for l in feuille_presence(fiche, ag)["lignes"]:
        if l["mode"] == "absent":
            a_notifier[l["id"]] = l["nom"]
    return {"resolutions": resultats, "amendees": sorted(amendees), "feuille": feuille_presence(fiche, ag),
            "a_notifier": [{"id": k, "nom": v} for k, v in sorted(a_notifier.items(), key=lambda x: x[1])],
            "formulaires_ecartes": sorted(set(ag.get("correspondance", {})) & en_seance),
            "avertissement": AVERTISSEMENT}


# --------------------------------------------------------------------------- #
# PDF
# --------------------------------------------------------------------------- #
def _styles():
    from avis_appel import ACCENT, FOND, GRIS, S, euros, tableau, tampon
    return ACCENT, FOND, GRIS, S, tableau, tampon


def _canevas():
    from avis_appel import CanevasNumerote
    return CanevasNumerote


def _case():
    """Case à cocher dessinée (la police intégrée n'a pas le caractère ☐)."""
    from reportlab.graphics.shapes import Drawing, Rect
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    d = Drawing(4.5 * mm, 4.5 * mm)
    d.add(Rect(0, 0, 4.5 * mm, 4.5 * mm, strokeColor=colors.HexColor("#1E1E1E"), strokeWidth=0.8,
               fillColor=colors.white))
    return d


def _e(t) -> str:
    return str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def generer_formulaire(fiche: dict, ag: dict, cid: str, chemin: Path) -> Path:
    """Formulaire de vote par correspondance pré-rempli pour un copropriétaire."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    ACCENT, FOND, GRIS, S, tableau, tampon = _styles()
    c = _coproprietaire(fiche, cid)
    ident = fiche["copropriete"]
    sid = fiche["gouvernance"]["syndic"]["coproprietaire_id"]
    syndic = _coproprietaire(fiche, sid)
    d = date.fromisoformat(ag["date"])
    limite = date.fromisoformat(calendrier(fiche, ag)["limite_correspondance"])
    voix = vote.electorat(fiche, "generales").get(cid, 0)
    base = fiche["cles_repartition"]["generales"]["base"]

    doc = SimpleDocTemplate(str(chemin), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=20 * mm, bottomMargin=18 * mm, title="Formulaire de vote par correspondance")
    h = [Paragraph("Formulaire de vote par correspondance", S["titre"]),
         Paragraph("Assemblée générale des copropriétaires · modèle de l'arrêté du 2 juillet 2020", S["sous"]),
         Spacer(1, 5 * mm)]
    infos = [
        ["Immeuble", Paragraph(f"{_e(ident['nom'])}, {_e(ident['adresse'])}", S["txt"])],
        ["Assemblée générale", Paragraph(f"{_e(ag['titre'])} du {d:%d/%m/%Y} à {_e(ag['heure'])}, {_e(ag['lieu'])}", S["txt"])],
        ["À retourner au syndic", Paragraph(f"{_e(syndic['nom'])}, syndic · {_e(ident['adresse'])} · "
                                            f"{_e(syndic.get('email') or '')}", S["txt"])],
        ["Date limite de réception", Paragraph(f"<b>{limite:%d/%m/%Y}</b> (trois jours francs avant la réunion). "
                                               f"Envoyé par mail à l'adresse ci-dessus, le formulaire est présumé "
                                               f"reçu à sa date d'envoi.", S["txt"])],
        ["Votant", Paragraph(f"<b>{_e(c['nom'])}</b> · {voix} voix sur {base} (charges générales)<br/>"
                             f"Copropriétaire, associé ou mandataire commun (indivision, démembrement)<br/>"
                             f"Domicile : ............................................................................", S["txt"])],
    ]
    t = Table(infos, colWidths=[42 * mm, 132 * mm])
    t.setStyle(TableStyle([("FONT", (0, 0), (0, -1), "Helvetica-Bold", 8.5), ("TEXTCOLOR", (0, 0), (0, -1), GRIS),
                           ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, -1), 0.3, FOND),
                           ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    h += [t, Spacer(1, 5 * mm),
          Paragraph("Je déclare avoir pris connaissance de l'ordre du jour et des documents joints à la convocation, "
                    "et je vote sur chacune des questions dans le tableau ci-dessous, en cochant une seule case par "
                    "question. Si le tableau occupe plusieurs pages, je paraphe les pages intermédiaires et je signe "
                    "la dernière.", S["txt"]),
          Spacer(1, 4 * mm)]

    lignes = [["Question", "Identification de l'objet", "Pour", "Contre", "Abstention"]]
    for r in ag["resolutions"]:
        cle = ("" if r["cle"] == "generales"
               else f"<br/><font size='8' color='#5C5C5C'>Vote des seuls copropriétaires concernés "
                    f"({_e(fiche['cles_repartition'][r['cle']]['libelle'].lower())})</font>")
        if r["majorite"] == "info":
            lignes.append([f"Point n° {r['num']}", Paragraph(f"{_e(r['titre'])}<br/><font size='8' color='#5C5C5C'>"
                                                              f"{MAJORITES['info']}</font>", S["txt"]), "", "—", ""])
            continue
        lignes.append([f"Résolution n° {r['num']}",
                       Paragraph(f"{_e(r['titre'])}<br/><font size='8' color='#5C5C5C'>{MAJORITES[r['majorite']]}</font>{cle}",
                                 S["txt"]), _case(), _case(), _case()])
    tv = tableau(lignes, [29 * mm, 97 * mm, 16 * mm, 16 * mm, 16 * mm], aligner_droite_depuis=5, lignes_total=0)
    tv.setStyle(TableStyle([("ALIGN", (2, 0), (-1, -1), "CENTER")]))
    h += [tv, Spacer(1, 8 * mm),
          KeepTogether([Paragraph("Fait à ............................................, le ...... / ...... / ............",
                                  S["txt"]), Spacer(1, 3 * mm),
                        Paragraph("Signature du copropriétaire, de l'associé ou du mandataire commun :", S["txt"]),
                        Spacer(1, 18 * mm)])]

    rappel = ("<b>Rappel des règles applicables</b><br/>"
              "• Loi du 10 juillet 1965, art. 17-1 A : les copropriétaires peuvent voter par correspondance avant "
              "l'assemblée, au moyen de ce formulaire. Si une résolution est amendée pendant la séance, le vote "
              "« pour » exprimé par correspondance sur cette résolution compte comme celui d'un copropriétaire "
              "défaillant.<br/>"
              "• Décret du 17 mars 1967, art. 9 : le formulaire est joint à la convocation.<br/>"
              "• Art. 9 bis : il doit être reçu par le syndic au plus tard trois jours francs avant la réunion. "
              "Envoyé par mail à l'adresse indiquée par le syndic, il est présumé reçu à sa date d'envoi.<br/>"
              "• Art. 14 : la feuille de présence mentionne les votants par correspondance et la date de réception "
              "de leur formulaire.<br/>"
              "• Art. 14-1 : le formulaire n'est pas pris en compte si le copropriétaire, ou son mandataire, est "
              "présent à l'assemblée, quelle que soit la date du formulaire.<br/>"
              "• Art. 17 : le procès-verbal mentionne les noms et le nombre de voix des opposants, des "
              "abstentionnistes et des copropriétaires assimilés à des défaillants.<br/><br/>"
              + __import__("papier").MENTION_PAPIER)
    encadre = Table([[Paragraph(rappel, S["petit"])]], colWidths=[174 * mm])
    encadre.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), FOND), ("LEFTPADDING", (0, 0), (-1, -1), 8),
                                 ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    h += [Spacer(1, 2 * mm), encadre]
    pied = f"{ident['nom']} – AG du {d:%d/%m/%Y} – {c['nom']}"
    doc.build(h, canvasmaker=_canevas(), onFirstPage=tampon(False, pied), onLaterPages=tampon(False, pied))
    return chemin


def generer_feuille_presence(fiche: dict, ag: dict, chemin: Path) -> Path:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, TableStyle

    ACCENT, FOND, GRIS, S, tableau, tampon = _styles()
    f = feuille_presence(fiche, ag)
    ident = fiche["copropriete"]
    d = date.fromisoformat(ag["date"])
    doc = SimpleDocTemplate(str(chemin), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=20 * mm, bottomMargin=18 * mm, title="Feuille de présence")
    h = [Paragraph("Feuille de présence", S["titre"]),
         Paragraph(f"{_e(ident['nom'])} · {_e(ag['titre'])} du {d:%d/%m/%Y} à {_e(ag['heure'])}, {_e(ag['lieu'])}", S["sous"]),
         Spacer(1, 5 * mm)]
    t = [["Copropriétaire", "Lots", "Voix", "Présence", "Émargement"]]
    for l in f["lignes"]:
        t.append([Paragraph(_e(l["nom"]), S["txt"]), Paragraph(", ".join(str(n) for n in l["lots"]), S["txt"]),
                  str(l["voix"]), Paragraph(_e(l["libelle"]), S["txt"]), "" if l["emargement"] else "—"])
    t.append(["Total", "", str(f["total_voix"]), "", ""])
    tp = tableau(t, [46 * mm, 22 * mm, 16 * mm, 60 * mm, 30 * mm], aligner_droite_depuis=2)
    tp.setStyle(TableStyle([("ALIGN", (3, 0), (-1, -1), "LEFT")]))
    h.append(tp)
    h += [Spacer(1, 4 * mm),
          Paragraph(f"Présents, à distance ou représentés : {f['nb_presents_representes']} copropriétaires, "
                    f"{f['voix_presentes_representees']} voix · Par correspondance : {f['nb_correspondance']}, "
                    f"{f['voix_correspondance']} voix · Absents : {f['voix_absentes']} voix · "
                    f"Total : {f['total_voix']} voix.", S["txt"]),
          Spacer(1, 3 * mm),
          Paragraph("L'émargement n'est pas requis pour les participants à distance. Le mandataire signe pour "
                    "son mandant. Feuille certifiée exacte par le président de séance.", S["petit"]),
          Spacer(1, 10 * mm), Paragraph("Le président de séance : ..........................................", S["txt"])]
    pied = f"{ident['nom']} – feuille de présence de l'AG du {d:%d/%m/%Y}"
    doc.build(h, canvasmaker=_canevas(), onFirstPage=tampon(False, pied), onLaterPages=tampon(False, pied))
    return chemin


def generer_pv(fiche: dict, ag: dict, dep: dict, chemin: Path) -> Path:
    """Brouillon de procès-verbal (filigrane PROJET) : à relire, compléter et faire signer."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    ACCENT, FOND, GRIS, S, tableau, tampon = _styles()
    ident = fiche["copropriete"]
    d = date.fromisoformat(ag["date"])
    f = dep["feuille"]
    doc = SimpleDocTemplate(str(chemin), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=20 * mm, bottomMargin=18 * mm, title="Procès-verbal – projet")
    h = [Paragraph("Procès-verbal de l'assemblée générale", S["titre"]),
         Paragraph(f"{_e(ident['nom'])}, {_e(ident['adresse'])} · {_e(ag['titre'])} du {d:%d/%m/%Y} à "
                   f"{_e(ag['heure'])}, {_e(ag['lieu'])}", S["sous"]),
         Spacer(1, 4 * mm),
         Paragraph(f"Présents, à distance ou représentés : {f['nb_presents_representes']} copropriétaires totalisant "
                   f"{f['voix_presentes_representees']} voix. Votes par correspondance : {f['nb_correspondance']} "
                   f"copropriétaires totalisant {f['voix_correspondance']} voix. Total des voix du syndicat : "
                   f"{f['total_voix']}.", S["txt"]),
         Spacer(1, 2 * mm),
         Paragraph("Bureau de séance — Président : .............................. · Secrétaire : "
                   ".............................. · Scrutateur(s) : ..............................", S["txt"]),
         Spacer(1, 5 * mm)]
    liste = lambda ps: ", ".join(f"{_e(p['nom'])} ({p['voix']})" for p in ps) or "aucun"
    for r in dep["resolutions"]:
        if r.get("information"):
            src = next((x for x in ag["resolutions"] if x["num"] == r["num"]), {})
            corps = (f"<b>Point n° {r['num']} — {_e(r['titre'])}</b><br/>{MAJORITES['info']}"
                     + (f"<br/>{_e(src['texte'])}" if src.get("texte") else "")
                     + "<br/>Résumé des échanges : ..............................................................")
            bloc = Table([[Paragraph(corps, S["txt"])]], colWidths=[174 * mm])
            bloc.setStyle(TableStyle([("LINEBEFORE", (0, 0), (0, -1), 2.5, GRIS), ("LEFTPADDING", (0, 0), (-1, -1), 8),
                                      ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
            h += [KeepTogether([bloc]), Spacer(1, 3 * mm)]
            continue
        dc = r["decompte"]
        statut = "ADOPTÉE" if r["adoptee"] else "REJETÉE"
        if r.get("retenue") is False:
            statut += " (sans effet : une résolution précédente du dossier l'a emporté)"
        corps = (f"<b>Résolution n° {r['num']} — {_e(r['titre'])}</b>"
                 + (" <i>(amendée en séance)</i>" if r["amendee"] else "")
                 + f"<br/>{MAJORITES[r['majorite']]}"
                 + ("" if r["cle"] == "generales"
                    else f" · vote des copropriétaires concernés ({_e(fiche['cles_repartition'][r['cle']]['libelle'].lower())})")
                 + f"<br/>Pour : {dc['pour']} voix · Contre : {dc['contre']} voix · Abstention : {dc['abstention']} voix "
                 f"· sur {dc['total_voix']} voix"
                 + f"<br/>Ont voté contre : {liste(r['opposants'])}"
                 + f"<br/>Se sont abstenus : {liste(r['abstentionnistes'])}"
                 + (f"<br/>Assimilés à des défaillants (vote « pour » par correspondance sur une résolution amendée) : "
                    f"{liste(r['defaillants_17_1_A'])}" if r["defaillants_17_1_A"] else "")
                 + (f"<br/><font color='#C2410C'>Formulaires sans choix pour cette résolution, non comptés : "
                    f"{liste(r['formulaires_sans_choix'])} — à trancher par le bureau</font>"
                    if r["formulaires_sans_choix"] else "")
                 + (f"<br/>Second vote à la majorité de l'article 24 : pour {r['second_vote_24']['decompte']['pour']}, "
                    f"contre {r['second_vote_24']['decompte']['contre']} voix" if r.get("second_vote_24") else "")
                 + (f"<br/>Second vote à la majorité de l'article 25 : pour {r['second_vote_25']['decompte']['pour']} "
                    f"voix sur {r['second_vote_25']['decompte']['total_voix']}" if r.get("second_vote_25") else "")
                 + (f"<br/><i>{_e(r['note'])}</i>" if r.get("note") else "")
                 + f"<br/><b>{statut}</b>")
        bloc = Table([[Paragraph(corps, S["txt"])]], colWidths=[174 * mm])
        bloc.setStyle(TableStyle([("LINEBEFORE", (0, 0), (0, -1), 2.5, ACCENT if r["adoptee"] else GRIS),
                                  ("LEFTPADDING", (0, 0), (-1, -1), 8), ("TOPPADDING", (0, 0), (-1, -1), 4),
                                  ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
        h += [KeepTogether([bloc]), Spacer(1, 3 * mm)]
    h += [Spacer(1, 4 * mm),
          Paragraph("Signatures — Le président : .................... Le secrétaire : .................... "
                    "Le(s) scrutateur(s) : ....................", S["txt"]),
          Spacer(1, 6 * mm),
          Paragraph("<b>Après la séance</b> : notifier le procès-verbal dans le mois aux copropriétaires opposants, "
                    "défaillants et absents (" + ", ".join(_e(p["nom"]) for p in dep["a_notifier"]) + "). La "
                    "notification reproduit l'article 42, alinéa 2, de la loi du 10 juillet 1965 : ils disposent de "
                    "deux mois pour contester les décisions.", S["txt"]),
          Spacer(1, 4 * mm), Paragraph(AVERTISSEMENT, S["petit"])]
    pied = f"{ident['nom']} – projet de procès-verbal de l'AG du {d:%d/%m/%Y}"
    doc.build(h, canvasmaker=_canevas(), onFirstPage=tampon(True, pied), onLaterPages=tampon(True, pied))
    return chemin
