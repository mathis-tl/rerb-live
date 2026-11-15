"""J2 (tâche 4) : vérité RER B à Massy - Palaiseau, hors ligne (LECTURE SEULE des relevés, aucun appel API).

Usage : python3 -I verite_massy.py [--decalage=SECONDES]      (relevés `prim_stop_monitoring` et `prim_estimated_timetable` de ~/rerb-data/)

À chaque relevé stop-monitoring (toutes les 2 min), `VehicleAtStop` dit quels trains sont vraiment à quai à Massy.
On les compare à `estimer` (position.py) avec le dernier relevé estimated-timetable reçu AVANT (E2a), mémoire M2
appliquée à chaque relevé dans l'ordre (clé = DatedVehicleJourneyRef). Logique et seuils (fixés avant de regarder les
résultats) : rerb/verite.py.
--decalage=S : DIAGNOSTIC seulement, `estimer` est évalué à (instant du relevé − S) ; ne change pas les seuils. Seul le RER B (LineRef STIF:Line::C01743:) est gardé.
"""
import base64
import bisect
import gzip
import json
import statistics
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

RACINE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RACINE / "rerb"))

from memoire import Memoire  # noqa: E402
from mesure import resumer  # noqa: E402
from position import Etat, estimer, lire_train  # noqa: E402
from verite import (FP, FP_ABSENT, MANQUE_ABSENT, MANQUE_ETAT, VP, classer_a_quai, departs_observes,  # noqa: E402
                    ecart_depart, rapport, verdict)

DONNEES = Path.home() / "rerb-data"
MASSY = "STIF:StopArea:SP:58774:"
LIGNE = "STIF:Line::C01743:"
JAMAIS_PERIME = timedelta(days=3650)
FACTICE = lambda a, b: 1.0  # noqa: E731  (seuls les états comptent ici, pas les distances)


def valeur(x):
    return x.get("value") if isinstance(x, dict) else x


def relevés(flux):
    for f in sorted((DONNEES / flux).glob("*/*.jsonl.gz")):
        try:
            with gzip.open(f, "rt") as fh:
                lignes = fh.readlines()
        except (EOFError, OSError):
            continue
        for l in lignes:
            r = json.loads(l)
            if str(r.get("status")) != "200" or r.get("error") or not r.get("body_b64"):
                continue
            c = base64.b64decode(r["body_b64"])
            try:
                c = gzip.decompress(c)
            except OSError:
                pass
            yield datetime.fromisoformat(r["ts_recv_utc"]), json.loads(c)["Siri"]["ServiceDelivery"]


def charger_stop_monitoring():
    """[(instant, courses à quai, courses listées)] pour le RER B."""
    sortie = []
    for ts, livraison in relevés("prim_stop_monitoring"):
        listees, a_quai = set(), set()
        for v in livraison["StopMonitoringDelivery"][0].get("MonitoredStopVisit") or []:
            j = v["MonitoredVehicleJourney"]
            if valeur(j.get("LineRef")) != LIGNE:
                continue
            ref = valeur(j["FramedVehicleJourneyRef"]["DatedVehicleJourneyRef"])
            listees.add(ref)
            if j["MonitoredCall"].get("VehicleAtStop"):
                a_quai.add(ref)
        sortie.append((ts, a_quai, listees))
    return sortie


def charger_previsions():
    """[(instant, {course: Train consolidé par la mémoire M2})] en ordre de réception."""
    memoire, sortie = Memoire(), []
    for ts, livraison in relevés("prim_estimated_timetable"):
        trains = {}
        for cadre in livraison["EstimatedTimetableDelivery"][0].get("EstimatedJourneyVersionFrame") or []:
            for ej in cadre.get("EstimatedVehicleJourney") or []:
                ref = valeur(ej.get("DatedVehicleJourneyRef"))
                if ref is not None:
                    trains[ref] = memoire.consolider(ref, lire_train(ej))
        sortie.append((ts, trains))
    return sortie


def main():
    decalage = timedelta(seconds=next((float(a.split('=')[1]) for a in sys.argv[1:] if a.startswith('--decalage=')), 0.0))
    sm, et = charger_stop_monitoring(), charger_previsions()
    instants = [t for t, _ in et]
    print(f"{len(sm)} relevés stop-monitoring, {len(et)} relevés estimated-timetable, {sm[0][0]} -> {sm[-1][0]}")
    comptes, sans_prevision, etats_manques = Counter(), 0, Counter()
    print(f"décalage de l'instant estimé : {decalage.total_seconds():g} s")
    for ts, a_quai, listees in sm:
        i = bisect.bisect_right(instants, ts) - 1   # dernier relevé reçu au plus tard à ts (E2a)
        if i < 0:
            sans_prevision += 1
            continue
        trains = et[i][1]
        estimes = {r: estimer(tr, ts - decalage, FACTICE, JAMAIS_PERIME) for r, tr in trains.items()}
        predits = {r for r, e in estimes.items() if e.etat == Etat.A_QUAI and e.gare == MASSY}
        for r in (a_quai & set(trains)) - predits:
            e = estimes[r]
            etats_manques[f"{e.etat.value}" + (f" (gare précédente Massy)" if e.gare == MASSY else "")] += 1
        comptes.update(classer_a_quai(a_quai, listees, set(trains), predits))
    if sans_prevision:
        print(f"relevés sans prévision antérieure : {sans_prevision}")
    print("\nÉTAT À QUAI à Massy - Palaiseau (comptes bruts, tous relevés confondus)")
    for k in (VP, MANQUE_ETAT, MANQUE_ABSENT, FP, FP_ABSENT):
        print(f"  {k:48s} {comptes[k]:5d}")
    for nom, (n, d) in rapport(comptes).items():
        if nom == "précision, absents inclus":
            print(f"  {nom:28s} {n}/{d} = {100 * n / d:5.1f} %" if d else f"  {nom} : aucun")
        else:
            lecture = verdict(nom, n / d) if d else "sans objet"
            print(f"  {nom:28s} {n}/{d} = {100 * n / d:5.1f} %  -> {lecture}" if d else f"  {nom} : aucun")

    if etats_manques:
        print("  état estimé des trains manqués :", dict(etats_manques.most_common()))

    # heure de départ
    ecarts, dans, exclus = [], 0, Counter()
    for ref, bas, haut in departs_observes([(t, q) for t, q, _ in sm]):
        i = bisect.bisect_right(instants, bas) - 1
        train = et[i][1].get(ref) if i >= 0 else None
        if train is None:
            exclus["train absent des prévisions"] += 1
            continue
        massy = [p for p in train.passages if p.gare == MASSY]
        if not massy or (massy[0].depart or massy[0].arrivee) is None:
            exclus["pas d'heure estimée à Massy"] += 1
            continue
        e, ok = ecart_depart(massy[0].depart or massy[0].arrivee, bas, haut)
        ecarts.append(e)
        dans += ok
    print(f"\nHEURE DE DÉPART de Massy - Palaiseau : {len(ecarts)} départs observés (+ écartés : {dict(exclus)})")
    if ecarts:
        r = resumer(ecarts)
        print(f"  biais (positif = estimation en avance) {r['biais']:7.1f} s")
        print(f"  médiane |écart| {r['mediane_abs']:7.1f} s -> {verdict('médiane départ', r['mediane_abs'])}")
        print(f"  90e centile |écart| {r['p90_abs']:7.1f} s -> {verdict('p90 départ', r['p90_abs'])}")
        print(f"  dans l'incertitude de lecture (±60 s) : {dans} sur {len(ecarts)} (indiscernables du bruit)")

    # info : temps d'arrêt estimé à Massy (arrivée -> départ) dans les prévisions
    arrets = []
    for _, trains in et:
        for tr in trains.values():
            for p in tr.passages:
                if p.gare == MASSY and p.arrivee and p.depart:
                    arrets.append((p.depart - p.arrivee).total_seconds())
    if arrets:
        zero = sum(1 for a in arrets if a == 0)
        print(f"\nINFO arrêt estimé à Massy (départ - arrivée) : {len(arrets)} passages, médiane {statistics.median(arrets):.0f} s, "
              f"{zero} à 0 s ({100 * zero / len(arrets):.0f} %)")


if __name__ == "__main__":
    main()
