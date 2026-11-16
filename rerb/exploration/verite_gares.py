"""J2 bis (tâche 4) : vérité RER B sur les 47 gares, hors ligne (LECTURE SEULE des relevés, aucun appel API).

Usage : python3 -I verite_gares.py [--decalage=SECONDES] [--donnees=CHEMIN]      (défaut : ~/rerb-data-2/)

Relevés `prim_stop_monitoring_gares` (47 gares, toutes les ~30 s) et `prim_estimated_timetable` (toutes les 2 min).
Même méthode que J2 (verite_massy.py), généralisée : un train à une gare = (gare, course). `VehicleAtStop` donne
les trains vraiment à quai ; on les compare à `estimer` (position.py) avec le dernier relevé estimated-timetable
reçu AVANT (E2a), mémoire M2 appliquée dans l'ordre. Seuils S2 INCHANGÉS (rerb/verite.py, SEUILS) ; lecture de
l'heure de départ à ±15 s ; trou de relevés > 45 s (ECART_MAX_GARES) : départ ignoré et compté.
--decalage=S : DIAGNOSTIC seulement, `estimer` évalué à (instant du relevé − S) ; ne change pas les seuils.
Seul le RER B (LineRef STIF:Line::C01743:) est gardé aux gares partagées (Châtelet, Gare du Nord...).
"""
import base64
import bisect
import csv
import gzip
import json
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

RACINE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RACINE / "rerb"))

from memoire import Memoire  # noqa: E402
from mesure import resumer  # noqa: E402
from position import Etat, estimer, lire_train  # noqa: E402
from verite import (ECART_MAX_GARES, FP, FP_ABSENT, MANQUE_ABSENT, MANQUE_ETAT, VP, classer_a_quai,  # noqa: E402
                    departs_par_gare, ecart_depart, nombre_trous, rapport, verdict)

DONNEES = Path.home() / "rerb-data-2"
CSV_GARES = RACINE / "rerb" / "donnees" / "arrets_rerb_C01743.csv"
LIGNE = "STIF:Line::C01743:"
JAMAIS_PERIME = timedelta(days=3650)
FACTICE = lambda a, b: 1.0  # noqa: E731  (seuls les états comptent ici, pas les distances)


def valeur(x):
    return x.get("value") if isinstance(x, dict) else x


def gares_csv():
    """{identifiant StopArea: nom} pour les 47 gares du CSV (même règle que recorder.load_gares)."""
    sortie = {}
    with open(CSV_GARES, newline="", encoding="utf-8") as f:
        for ligne in csv.DictReader(f):
            n = ligne["stop_id"].rsplit(":", 1)[-1]
            sortie.setdefault(f"STIF:StopArea:SP:{n}:", ligne["stop_name"])
    return sortie


def relevés(flux):
    """(instant de réception, url, livraison Siri) des réponses 200 d'un flux, fichiers dans l'ordre."""
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
            yield datetime.fromisoformat(r["ts_recv_utc"]), r.get("url", ""), json.loads(c)["Siri"]["ServiceDelivery"]


def charger_stop_monitoring(gares):
    """-> ({gare: [(instant, courses à quai, courses listées)]} trié par instant, nb de réponses d'une gare inconnue)."""
    par_gare, inconnues = defaultdict(list), 0
    for ts, url, livraison in relevés("prim_stop_monitoring_gares"):
        gare = parse_qs(urlparse(url).query).get("MonitoringRef", [None])[0]
        if gare not in gares:
            inconnues += 1
            continue
        listees, a_quai = set(), set()
        for v in livraison["StopMonitoringDelivery"][0].get("MonitoredStopVisit") or []:
            j = v["MonitoredVehicleJourney"]
            if valeur(j.get("LineRef")) != LIGNE:
                continue
            ref = valeur(j["FramedVehicleJourneyRef"]["DatedVehicleJourneyRef"])
            listees.add(ref)
            if j["MonitoredCall"].get("VehicleAtStop"):
                a_quai.add(ref)
        par_gare[gare].append((ts, a_quai, listees))
    for rel in par_gare.values():
        rel.sort(key=lambda x: x[0])
    return par_gare, inconnues


def charger_previsions():
    """[(instant, {course: Train consolidé par la mémoire M2}, {gare: [courses ayant un passage à cette gare]})]."""
    memoire, sortie = Memoire(), []
    for ts, _, livraison in relevés("prim_estimated_timetable"):
        trains = {}
        for cadre in livraison["EstimatedTimetableDelivery"][0].get("EstimatedJourneyVersionFrame") or []:
            for ej in cadre.get("EstimatedVehicleJourney") or []:
                ref = valeur(ej.get("DatedVehicleJourneyRef"))
                if ref is not None:
                    trains[ref] = memoire.consolider(ref, lire_train(ej))
        index = defaultdict(list)
        for ref, tr in trains.items():
            for g in {p.gare for p in tr.passages}:
                index[g].append(ref)
        sortie.append((ts, trains, index))
    return sortie


def ligne_taux(nom, n, d, avec_verdict=True):
    if not d:
        return f"  {nom:28s} aucun"
    txt = f"  {nom:28s} {n}/{d} = {100 * n / d:5.1f} %"
    return txt + (f"  -> {verdict(nom, n / d)}" if avec_verdict else "")


def main():
    global DONNEES
    decalage = timedelta(seconds=next((float(a.split('=')[1]) for a in sys.argv[1:] if a.startswith('--decalage=')), 0.0))
    for a in sys.argv[1:]:
        if a.startswith("--donnees="):
            DONNEES = Path(a.split("=", 1)[1]).expanduser()
    gares = gares_csv()
    sm, inconnues = charger_stop_monitoring(gares)
    et = charger_previsions()
    instants = [t for t, _, _ in et]
    tous = [t for rel in sm.values() for t, _, _ in rel]
    print(f"{sum(len(r) for r in sm.values())} relevés stop-monitoring sur {len(sm)}/{len(gares)} gares "
          f"({inconnues} réponses d'une gare hors liste), {len(et)} relevés estimated-timetable, {min(tous)} -> {max(tous)}")
    print(f"décalage de l'instant estimé : {decalage.total_seconds():g} s ; trou de relevés > "
          f"{ECART_MAX_GARES.total_seconds():g} s : {nombre_trous({g: [(t, q) for t, q, _ in r] for g, r in sm.items()})} intervalles ignorés")

    comptes, par_gare, sans_prevision, etats_manques = Counter(), defaultdict(Counter), 0, Counter()
    sans_passage = Counter()   # gares jamais présentes dans les prévisions
    for g, rel in sm.items():
        for ts, a_quai, listees in rel:
            i = bisect.bisect_right(instants, ts) - 1   # dernier relevé reçu au plus tard à ts (E2a)
            if i < 0:
                sans_prevision += 1
                continue
            _, trains, index = et[i]
            estimes = {r: estimer(trains[r], ts - decalage, FACTICE, JAMAIS_PERIME) for r in index.get(g, [])}
            predits = {r for r, e in estimes.items() if e.etat == Etat.A_QUAI and e.gare == g}
            for r in (a_quai & set(trains)) - predits:
                e = estimes.get(r) or estimer(trains[r], ts - decalage, FACTICE, JAMAIS_PERIME)
                etats_manques[f"{e.etat.value}" + (" (gare précédente = cette gare)" if e.gare == g else "")] += 1
            c = classer_a_quai({(g, r) for r in a_quai}, {(g, r) for r in listees},
                               {(g, r) for r in trains}, {(g, r) for r in predits})
            comptes.update(c)
            par_gare[g].update(c)
    for g in gares:
        if g not in sm:
            sans_passage[g] += 1
    if sans_prevision:
        print(f"relevés sans prévision antérieure : {sans_prevision}")
    if sans_passage:
        print("gares sans aucun relevé :", [gares[g] for g in sans_passage])

    print("\nÉTAT À QUAI, 47 gares (comptes bruts, tous relevés et toutes gares confondus)")
    for k in (VP, MANQUE_ETAT, MANQUE_ABSENT, FP, FP_ABSENT):
        print(f"  {k:48s} {comptes[k]:6d}")
    for nom, (n, d) in rapport(comptes).items():
        print(ligne_taux(nom, n, d, avec_verdict=nom != "précision, absents inclus"))
    if etats_manques:
        print("  état estimé des trains manqués :", dict(etats_manques.most_common()))

    # heure de départ
    releves_q = {g: [(t, q) for t, q, _ in rel] for g, rel in sm.items()}
    ecarts, ecarts_gare, dans, exclus = [], defaultdict(list), 0, Counter()
    for g, ref, bas, haut in departs_par_gare(releves_q):
        i = bisect.bisect_right(instants, bas) - 1
        train = et[i][1].get(ref) if i >= 0 else None
        if train is None:
            exclus["train absent des prévisions"] += 1
            continue
        ici = [p for p in train.passages if p.gare == g]
        if not ici or (ici[0].depart or ici[0].arrivee) is None:
            exclus["pas d'heure estimée à cette gare"] += 1
            continue
        e, ok = ecart_depart(ici[0].depart or ici[0].arrivee, bas, haut)
        ecarts.append(e)
        ecarts_gare[g].append(e)
        dans += ok
    print(f"\nHEURE DE DÉPART, 47 gares : {len(ecarts)} départs observés (+ écartés : {dict(exclus)})")
    if ecarts:
        r = resumer(ecarts)
        print(f"  biais (positif = estimation en avance) {r['biais']:7.1f} s")
        print(f"  médiane |écart| {r['mediane_abs']:7.1f} s -> {verdict('médiane départ', r['mediane_abs'])}")
        print(f"  90e centile |écart| {r['p90_abs']:7.1f} s -> {verdict('p90 départ', r['p90_abs'])}")
        print(f"  dans l'incertitude de lecture (±15 s) : {dans} sur {len(ecarts)} (indiscernables du bruit)")

    print("\nPAR GARE (comptes bruts ; rappel = VP/(VP+manqués), précision = VP/(VP+FP) ; départs : médiane |écart| s)")
    print(f"  {'gare':36s} {'VP':>5} {'manq':>5} {'FP':>5} {'rappel':>7} {'précis':>7} {'dép.':>5} {'méd|é|':>7} {'biais':>7}")
    for g, nom in sorted(gares.items(), key=lambda x: x[1]):
        c = par_gare.get(g)
        if not c:
            print(f"  {nom[:36]:36s} aucun relevé")
            continue
        vrais = c[VP] + c[MANQUE_ETAT] + c[MANQUE_ABSENT]
        rap = f"{100 * c[VP] / vrais:6.1f}%" if vrais else "      -"
        pre = f"{100 * c[VP] / (c[VP] + c[FP]):6.1f}%" if (c[VP] + c[FP]) else "      -"
        e = ecarts_gare.get(g, [])
        med = f"{statistics.median(abs(x) for x in e):7.1f}" if e else "      -"
        bia = f"{statistics.mean(e):7.1f}" if e else "      -"
        print(f"  {nom[:36]:36s} {c[VP]:5d} {vrais - c[VP]:5d} {c[FP]:5d} {rap} {pre} {len(e):5d} {med} {bia}")

    # info : temps d'arrêt estimé (arrivée -> départ) dans les prévisions, aux 47 gares
    arrets = [(p.depart - p.arrivee).total_seconds() for _, trains, _ in et for tr in trains.values()
              for p in tr.passages if p.gare in gares and p.arrivee and p.depart]
    if arrets:
        zero = sum(1 for a in arrets if a == 0)
        print(f"\nINFO arrêt estimé (départ - arrivée), 47 gares : {len(arrets)} passages, médiane {statistics.median(arrets):.0f} s, "
              f"{zero} à 0 s ({100 * zero / len(arrets):.0f} %)")


if __name__ == "__main__":
    main()
