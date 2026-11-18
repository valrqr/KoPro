"""
vote.py — Moteur de vote de Mitoyen : qui vote, avec combien de voix, et quelle résolution est adoptée.

Trois usages :
    1. le SONDAGE CONSULTATIF : avis des copropriétaires sur des devis, en tantièmes, sans valeur de décision
    2. le DÉPOUILLEMENT D'AG : une résolution par devis, votées dans l'ordre, la première adoptée l'emporte
    3. le VOTE DU CONSEIL SYNDICAL : une voix par membre (dans le cadre d'une délégation votée en AG)

Règles de majorité de la loi du 10 juillet 1965 :
    - art. 24  : majorité des voix exprimées (pour > contre), abstentions et absents exclus
    - art. 25  : majorité des voix de TOUS les copropriétaires
    - art. 25-1: si l'art. 25 échoue mais que la résolution recueille au moins 1/3 des voix de tous,
                 l'AG peut procéder immédiatement à un second vote à la majorité de l'art. 24
    - art. 26  : majorité des copropriétaires (en nombre) représentant au moins 2/3 des voix
    - art. 22  : un copropriétaire qui détient plus de la moitié des voix voit ses voix réduites
                 à la somme des voix des autres
    - charges spéciales : seuls les copropriétaires concernés votent, avec les tantièmes de cette clé

⚠️ Aide au calcul : le procès-verbal d'AG, signé, reste la seule référence.
"""

from __future__ import annotations

import hashlib
import secrets
from collections import defaultdict
from datetime import datetime, timedelta
from fractions import Fraction

CHOIX_RESOLUTION = ("pour", "contre", "abstention")
MAJORITES = ("24", "25", "26")


# --------------------------------------------------------------------------- #
# Électorat
# --------------------------------------------------------------------------- #
def electorat(fiche: dict, cle: str = "generales") -> dict[str, int]:
    """Voix de chaque copropriétaire pour une clé donnée (0 = non concerné, il ne vote pas).
    Applique la réduction de l'article 22."""
    voix: dict[str, int] = defaultdict(int)
    for lot in fiche["lots"]:
        voix[lot["proprietaire"]] += lot["tantiemes"][cle]
    voix = {cid: v for cid, v in voix.items() if v > 0}
    total = sum(voix.values())
    for cid, v in list(voix.items()):
        autres = total - v
        if v > autres:                      # plus de la moitié des voix : réduit (art. 22)
            voix[cid] = autres
    return dict(voix)


# --------------------------------------------------------------------------- #
# Une résolution
# --------------------------------------------------------------------------- #
def decompter(bulletins: dict[str, str], voix: dict[str, int], majorite: str = "24") -> dict:
    """bulletins : {copropriétaire: pour|contre|abstention}. Les absents ne figurent pas."""
    if majorite not in MAJORITES:
        raise ValueError(f"Majorité inconnue : {majorite}")
    inconnus = set(bulletins) - set(voix)
    if inconnus:
        raise ValueError(f"Votants hors électorat pour cette clé : {', '.join(sorted(inconnus))}")
    for cid, choix in bulletins.items():
        if choix not in CHOIX_RESOLUTION:
            raise ValueError(f"Choix invalide pour {cid} : {choix}")

    somme = lambda c: sum(voix[cid] for cid, ch in bulletins.items() if ch == c)
    pour, contre, abst = somme("pour"), somme("contre"), somme("abstention")
    total = sum(voix.values())
    nb_pour = sum(1 for ch in bulletins.values() if ch == "pour")
    nb_membres = len(voix)

    res = {"majorite": majorite, "pour": pour, "contre": contre, "abstention": abst,
           "absents": total - pour - contre - abst, "total_voix": total,
           "votants": len(bulletins), "nb_pour": nb_pour, "nb_coproprietaires": nb_membres,
           "passerelle_25_1": False}
    if majorite == "24":
        res["adoptee"] = pour > contre
        res["regle"] = f"Pour ({pour}) > Contre ({contre}) — abstentions et absents non comptés"
    elif majorite == "25":
        res["adoptee"] = pour * 2 > total
        res["regle"] = f"Pour ({pour}) > moitié de toutes les voix ({total} / 2 = {Fraction(total, 2)})"
        if not res["adoptee"] and pour * 3 >= total:
            res["passerelle_25_1"] = True
            res["regle"] += (" — non atteinte, mais au moins 1/3 des voix : un second vote immédiat "
                             "à la majorité de l'article 24 est possible (art. 25-1)")
    else:  # 26
        res["adoptee"] = nb_pour * 2 > nb_membres and pour * 3 >= total * 2
        res["regle"] = (f"{nb_pour} copropriétaires pour sur {nb_membres} (majorité requise) "
                        f"et {pour} voix pour sur {total} (2/3 requis)")
    return res


# --------------------------------------------------------------------------- #
# Plusieurs devis : une résolution par devis, votées l'une après l'autre
# --------------------------------------------------------------------------- #
def depouiller_ag(resolutions: list[dict], voix: dict[str, int], majorite: str = "24") -> dict:
    """resolutions : [{"devis": "D2", "bulletins": {...}, "second_vote_24": {...} | None}, ...] dans l'ordre
    de l'ordre du jour. La première adoptée l'emporte, les suivantes deviennent sans objet."""
    resultats, retenu = [], None
    for r in resolutions:
        if retenu:
            resultats.append({"devis": r["devis"], "statut": "sans_objet",
                              "explication": f"Sans objet : le devis {retenu} a déjà été adopté"})
            continue
        d = decompter(r["bulletins"], voix, majorite)
        d["devis"] = r["devis"]
        if not d["adoptee"] and d["passerelle_25_1"] and r.get("second_vote_24"):
            d2 = decompter(r["second_vote_24"], voix, "24")
            d["second_vote"] = d2
            d["adoptee"] = d2["adoptee"]
        d["statut"] = "adoptee" if d["adoptee"] else "rejetee"
        if d["adoptee"]:
            retenu = r["devis"]
        resultats.append(d)
    return {"devis_retenu": retenu, "resolutions": resultats,
            "avertissement": "Calcul indicatif : seul le procès-verbal signé de l'AG fait foi."}


def vote_conseil(bulletins: dict[str, str], membres: list[str]) -> dict:
    """Une voix par membre ; adoption à la majorité des membres du conseil (pas seulement des votants)."""
    inconnus = set(bulletins) - set(membres)
    if inconnus:
        raise ValueError(f"Non membres du conseil syndical : {', '.join(sorted(inconnus))}")
    pour = sum(1 for c in bulletins.values() if c == "pour")
    return {"pour": pour, "contre": sum(1 for c in bulletins.values() if c == "contre"),
            "abstention": sum(1 for c in bulletins.values() if c == "abstention"),
            "membres": len(membres), "adoptee": pour * 2 > len(membres),
            "regle": f"{pour} voix pour sur {len(membres)} membres (majorité des membres requise, "
                     f"sauf règle différente du règlement du conseil)"}


# --------------------------------------------------------------------------- #
# Sondage consultatif
# --------------------------------------------------------------------------- #
SANS_AVIS = "sans_avis"
AUCUN = "aucun"


def hacher(jeton: str) -> str:
    return hashlib.sha256(jeton.encode()).hexdigest()


def ouvrir_sondage(fiche: dict, dossier: dict, options: list[str], duree_jours: int,
                   maintenant: datetime | None = None) -> tuple[dict, dict[str, str]]:
    """Crée le sondage et renvoie (sondage à stocker, {copropriétaire: jeton en clair}).
    Les jetons en clair ne sont JAMAIS stockés : seule leur empreinte l'est."""
    if not 1 <= duree_jours <= 30:
        raise ValueError("Durée du sondage : entre 1 et 30 jours")
    connus = {d["id"] for d in dossier["devis"]}
    if len(options) < 2 or set(options) - connus:
        raise ValueError("Choisissez au moins 2 devis existants du dossier")
    debut = (maintenant or datetime.now()).replace(microsecond=0)
    voix = electorat(fiche, dossier["cle"])
    jetons = {cid: secrets.token_urlsafe(18) for cid in voix}
    numero = len(dossier.get("sondages", [])) + 1
    sondage = {
        "id": f"S{numero}", "type": "consultatif", "options": options, "cle": dossier["cle"],
        "ouvert_le": debut.isoformat(), "clos_le": (debut + timedelta(days=duree_jours)).isoformat(),
        "electeurs": {cid: {"jeton": hacher(j), "voix": voix[cid], "choix": None, "vote_le": None}
                      for cid, j in jetons.items()},
    }
    return sondage, jetons


def trouver_electeur(sondage: dict, jeton: str) -> str | None:
    h = hacher(jeton)
    return next((cid for cid, e in sondage["electeurs"].items() if secrets.compare_digest(e["jeton"], h)), None)


def est_ouvert(sondage: dict, maintenant: datetime | None = None) -> bool:
    return (maintenant or datetime.now()) < datetime.fromisoformat(sondage["clos_le"]) and not sondage.get("cloture_anticipee")


def voter_sondage(sondage: dict, cid: str, choix: str, maintenant: datetime | None = None) -> None:
    if not est_ouvert(sondage, maintenant):
        raise ValueError("Le sondage est clos")
    if choix not in sondage["options"] + [AUCUN, SANS_AVIS]:
        raise ValueError("Choix invalide")
    e = sondage["electeurs"][cid]
    e["choix"], e["vote_le"] = choix, (maintenant or datetime.now()).replace(microsecond=0).isoformat()


def resultats_sondage(sondage: dict, maintenant: datetime | None = None) -> dict:
    electeurs = sondage["electeurs"]
    total = sum(e["voix"] for e in electeurs.values())
    lignes = []
    for opt in sondage["options"] + [AUCUN, SANS_AVIS]:
        qui = [e for e in electeurs.values() if e["choix"] == opt]
        v = sum(e["voix"] for e in qui)
        lignes.append({"choix": opt, "voix": v, "nb": len(qui),
                       "part_voix_pct": round(100 * v / total, 1) if total else 0})
    ont_vote = [e for e in electeurs.values() if e["choix"]]
    return {"sondage": sondage["id"], "type": "consultatif", "ouvert": est_ouvert(sondage, maintenant),
            "clos_le": sondage["clos_le"], "total_voix": total, "nb_electeurs": len(electeurs),
            "participation_nb": len(ont_vote),
            "participation_voix_pct": round(100 * sum(e["voix"] for e in ont_vote) / total, 1) if total else 0,
            "resultats": lignes,
            "avertissement": "Sondage consultatif : il éclaire la décision mais ne vaut pas vote. "
                             "La décision appartient au syndic, au conseil syndical ou à l'AG selon le cas."}
