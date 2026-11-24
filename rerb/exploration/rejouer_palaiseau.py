"""Rejeu hors ligne du relevé de Palaiseau (18/11/2026) : que donne la règle « séjour à quai 25/30 s + lissage » sur de vraies données ?

LECTURE SEULE : aucun appel PRIM, rien n'est écrit. On relit les relevés `estimated-timetable` enregistrés ce jour-là (dossier hors dépôt) et
le relevé de vérité terrain de Mathis, on rejoue le serveur pas à pas (3 s) et on compare trois scénarios :
  A  avant  : ni règle de séjour, ni lissage (comportement du 17/11) ;
  B  règle seule ;
  C  règle + lissage (comportement livré).

Usage : python3 rerb/exploration/rejouer_palaiseau.py [dossier_des_donnees]   (défaut : ~/rerb-data-palaiseau-20261118)
Mesures : séjour affiché à Palaiseau et recouvrement avec l'arrêt réel ; erreur de l'heure de départ affichée ; sauts de position par pas de 3 s.
"""
import base64
import glob
import gzip
import json
import math
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import position  # noqa: E402
import serveur  # noqa: E402
from lissage import Lisseur  # noqa: E402
from memoire import Memoire  # noqa: E402
from serveur import Releve, calculer_trains, construire_contexte, consolider_livraison  # noqa: E402

PALAISEAU = "STIF:StopArea:SP:47009:"
PAS_S = 3
SAUT_M = 110.0   # 3 s à 33 m/s = 99 m, plus une marge
UTC = timezone.utc


def _heure(texte):
    return datetime.fromisoformat(texte.replace("Z", "+00:00"))


def lire_releves(dossier):
    """[(instant de réception UTC, livraison PRIM)] triés."""
    sortie = []
    for f in sorted(glob.glob(str(Path(dossier) / "prim_estimated_timetable" / "*" / "*.jsonl.gz"))):
        for ligne in gzip.open(f, "rt"):
            r = json.loads(ligne)
            if r.get("body_b64"):
                corps = json.loads(base64.b64decode(r["body_b64"]))
                sortie.append((_heure(r["ts_recv_utc"]), corps["Siri"]["ServiceDelivery"]))
    return sorted(sortie, key=lambda x: x[0])


def lire_verite(dossier):
    """[(mission, arrivée réelle UTC, départ réel UTC)] d'après releve_reel_palaiseau.txt (heures de Paris = UTC+1 le 18/11)."""
    lignes = (Path(dossier) / "releve_reel_palaiseau.txt").read_text(encoding="utf-8").splitlines()
    sortie = []
    for l in lignes:
        morceaux = [m.strip() for m in l.split("|")]
        if len(morceaux) == 5 and morceaux[0] in ("Sud", "Nord"):
            def hms(x):
                hh, mm, ss = map(int, x.split(":"))
                return datetime(2026, 11, 18, hh - 1, mm, ss, tzinfo=UTC)
            sortie.append((morceaux[1], hms(morceaux[2]), hms(morceaux[3])))
    return sortie


def courses_des_missions(releves, verite):
    """mission -> référence de course (celle dont l'arrivée théorique à Palaiseau est la plus proche de l'arrivée réelle)."""
    reponse = {}
    for mission, arrivee, _ in verite:
        meilleur = None
        for _t, liv in releves:
            for cadre in liv["EstimatedTimetableDelivery"][0].get("EstimatedJourneyVersionFrame") or []:
                for ej in cadre.get("EstimatedVehicleJourney") or []:
                    if ((ej.get("VehicleJourneyName") or [{}])[0].get("value")) != mission:
                        continue
                    for c in (ej.get("EstimatedCalls") or {}).get("EstimatedCall", []):
                        if c["StopPointRef"]["value"] == PALAISEAU and c.get("AimedArrivalTime"):
                            ecart = abs((_heure(c["AimedArrivalTime"]) - arrivee).total_seconds())
                            if meilleur is None or ecart < meilleur[0]:
                                meilleur = (ecart, ej["DatedVehicleJourneyRef"]["value"] if isinstance(ej["DatedVehicleJourneyRef"], dict) else ej["DatedVehicleJourneyRef"])
        if meilleur and meilleur[0] < 600:
            reponse[mission] = meilleur[1]
    return reponse


def rejouer(releves, debut, fin, lisser, regle_sejour, tolerance_s=None, rampe=None, biais_s=None):
    """-> {course: [(t, état, gare, lat, lon, nouveau_releve)]} pour toutes les courses, un pas de 3 s."""
    distance, localiser, nom = construire_contexte()
    ancien_plafond = position.ARRET_ANNONCE_MAX
    ancien_incertitude = serveur._incertitude
    position.ARRET_ANNONCE_MAX = ancien_plafond if regle_sejour else timedelta(0)   # plafond nul : la règle ne s'applique jamais
    serveur._incertitude = lambda *a, **k: None                                    # inutile ici et coûteux
    try:
        reglages = {k: v for k, v in (("tolerance_s", tolerance_s), ("rampe", rampe), ("biais_s", biais_s)) if v is not None}
        memoire, lisseur = Memoire(), (Lisseur(**reglages) if lisser else None)
        trace, i, courant, nouveau = {}, 0, None, False
        t = debut
        while t <= fin:
            while i < len(releves) and releves[i][0] <= t:
                courant = Releve(releves[i][0], consolider_livraison(releves[i][1], memoire))
                if lisseur is not None:   # T3 : le lissage est avancé une fois par relevé reçu, puis lu seul
                    lisseur.avancer({ref: tr for ref, (tr, _r) in courant.trains.items()}, courant.recu)
                nouveau, i = True, i + 1
            if courant is not None:
                for d in calculer_trains(courant, t, distance, localiser, nom, lisseur=lisseur):
                    trace.setdefault(d["course"], []).append((t, d["etat"], d["gare"], d["lat"], d["lon"], nouveau))
            nouveau = False
            t += timedelta(seconds=PAS_S)
        return trace, nom(PALAISEAU)
    finally:
        position.ARRET_ANNONCE_MAX = ancien_plafond
        serveur._incertitude = ancien_incertitude


def sauts(trace):
    """Liste des (course, t, mètres, au_changement_de_releve) pour chaque pas de plus de SAUT_M."""
    liste = []
    for course, points in trace.items():
        for a, b in zip(points, points[1:]):
            if a[3] is None or b[3] is None or (b[0] - a[0]).total_seconds() > PAS_S:
                continue
            m = math.hypot((b[4] - a[4]) * 74000.0, (b[3] - a[3]) * 111000.0)
            if m > SAUT_M:
                liste.append((course, b[0], m, b[5], a[1], b[1], a[2], b[2]))
    return liste


def sejours(trace, nom_gare, courses_verite, verite):
    """Pour chaque train de la vérité terrain : secondes affichées « à quai » à Palaiseau, recouvrement avec l'arrêt réel, heure de départ affichée."""
    sortie = []
    for mission, arrivee, depart in verite:
        course = courses_verite.get(mission)
        if course is None or course not in trace:
            continue
        quai = [p[0] for p in trace[course] if p[1] == "à quai" and p[2] == nom_gare]
        if not quai:
            sortie.append((mission, 0.0, 0.0, None))
            continue
        premier, dernier = min(quai), max(quai)
        recouvrement = max(0.0, (min(dernier, depart) - max(premier, arrivee)).total_seconds())
        sortie.append((mission, (dernier - premier).total_seconds() + PAS_S, recouvrement, (dernier + timedelta(seconds=PAS_S) - depart).total_seconds()))
    return sortie


def main(dossier, variantes=False):
    releves, verite = lire_releves(dossier), lire_verite(dossier)
    courses = courses_des_missions(releves, verite)
    debut, fin = min(v[1] for v in verite) - timedelta(minutes=12), max(v[2] for v in verite) + timedelta(minutes=4)
    print(f"{len(releves)} relevés, {len(verite)} trains mesurés, {len(courses)} appariés ; fenêtre {debut:%H:%M} - {fin:%H:%M} UTC, pas de {PAS_S} s")
    scenarios = [("A avant (ni règle ni lissage)", False, False, None, None, None), ("B règle de séjour seule", False, True, None, None, None),
                 ("C règle + lissage (livré : tolérance 15 s, rampe 0,2, biais +10 s)", True, True, None, None, None)]
    if variantes:   # réglages alternatifs du lissage, pour éclairer les compromis tolérance / biais / erreur de départ
        scenarios += [(f"C' tolérance {tol:g} s, rampe {ramp:g}, biais {biais:+g} s", True, True, tol, ramp, biais)
                      for tol, ramp, biais in ((15, 0.2, 0), (30, 0.2, 0), (30, 0.2, 10), (15, 0.2, 20), (15, 0.2, 26), (15, 0.5, 10), (0, 0.5, 0))]
    for nom, lisser, regle, tol, ramp, biais in scenarios:
        trace, nom_gare = rejouer(releves, debut, fin, lisser, regle, tol, ramp, biais)
        s = sauts(trace)
        sj = sejours(trace, nom_gare, courses, verite)
        affiches = [x[1] for x in sj]
        print(f"\n== {nom}")
        print(f"   séjour affiché à Palaiseau : total {sum(affiches):.0f} s sur {len(sj)} trains (réel : {sum((d - a).total_seconds() for m, a, d in verite if m in courses):.0f} s) ; moyenne {statistics.mean(affiches):.0f} s")
        print(f"   recouvrement avec l'arrêt réel : {sum(x[2] for x in sj):.0f} s")
        deps = [abs(x[3]) for x in sj if x[3] is not None]
        biais = [x[3] for x in sj if x[3] is not None]
        print(f"   erreur du départ affiché : moyenne {statistics.mean(deps):.0f} s, médiane {statistics.median(deps):.0f} s, biais {statistics.mean(biais):+.0f} s")
        pas = sum(len(p) for p in trace.values())
        print(f"   sauts de plus de {SAUT_M:.0f} m en {PAS_S} s : {len(s)} sur {pas} pas ({sum(1 for x in s if x[3])} au moment d'un nouveau relevé) ; plus grand : {max((x[2] for x in s), default=0):.0f} m")
        if lisser and len(s) <= 12:
            for course, t, m, _n, e0, e1, g0, g1 in sorted(s, key=lambda x: -x[2]):
                print(f"      {t:%H:%M:%S} {course[-12:]} {m:.0f} m : {e0} {g0} -> {e1} {g1}")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    main(args[0] if args else Path.home() / "rerb-data-palaiseau-20261118", variantes="--variantes" in sys.argv)
