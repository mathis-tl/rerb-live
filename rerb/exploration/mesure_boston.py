"""Erreur en mètres à Boston (niveau 1 et repères 0a, 0b) sur les relevés de l'enregistreur (LECTURE SEULE, aucun appel API).

Usage : python3 -I mesure_boston.py [AAAA-MM-JJ ...]      (défaut : tous les jours de ~/rerb-data/mbta_vehicles)

Pour chaque point GPS (mbta_vehicles), `apparier` (rerb/mbta.py) cherche le dernier relevé de prévisions reçu
au plus tard à l'instant du GPS (E2a). Puis `estimer` (position.py) place le train à l'instant du GPS (J1) et
`mesurer` (mesure.py) donne l'erreur signée le long de la voie (E1, E3, E6). Chaque point GPS tombe dans UNE
seule ligne du tableau : mesuré ou écarté avec son motif (E4).

Une même position GPS (même course, même `updated_at`) revient dans plusieurs relevés toutes les 12 s : on ne
la mesure qu'une fois, les répétitions sont comptées à part (« doublon de position »).

Mémoire : seuls les relevés de prévisions des FENETRE dernières secondes avant chaque relevé GPS sont gardés.
Un GPS périmé (> 120 s) est écarté avant toute recherche, donc 300 s suffisent. Logique testée dans test_mbta.py.
"""
import base64
import collections
import gzip
import json
import sys
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path

RACINE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RACINE / "rerb"))

from mbta import apparier, consolider_releve, lire_gps_mbta  # noqa: E402
from memoire import Memoire  # noqa: E402
from mesure import NIVEAUX, mesurer, resumer  # noqa: E402
from position import estimer  # noqa: E402
from voie import (PkAmbigu, PointHorsVoie, Voie, charger_arrets, charger_formes,  # noqa: E402
                  charger_parents, charger_trips, coordonnees_sur_voie, distance_sur_voie, pk_gares)

DONNEES = Path.home() / "rerb-data"
STATIQUE = RACINE / "data" / "static" / "mbta"
FENETRE = timedelta(seconds=300)
RECUL_PK = 50.0


def releves(flux, jours):
    """(ts_recv_utc, corps JSON:API décodé) en ordre croissant, relevés en erreur ignorés."""
    for jour in jours:
        for f in sorted((DONNEES / flux / jour).glob("*.jsonl.gz")):
            with gzip.open(f, "rt") as fh:
                for ligne in fh:
                    r = json.loads(ligne)
                    if r.get("error") or not r.get("body_b64"):
                        continue
                    yield datetime.fromisoformat(r["ts_recv_utc"]), json.loads(base64.b64decode(r["body_b64"]))


def compter(jours, decalage=0.0):
    trips = charger_trips(STATIQUE / "trips.txt")
    arrets = charger_arrets(STATIQUE / "stops.txt")
    parents = charger_parents(STATIQUE / "stops.txt")
    quais = collections.defaultdict(list)  # gare parent -> coordonnées de ses quais
    for quai, gare in parents.items():
        if quai in arrets:
            quais[gare].append(arrets[quai])
    formes = charger_formes(STATIQUE / "shapes.txt", set(trips.values()))
    voies, pks_cache, dernier_pk, vus = {}, {}, {}, set()
    prev = releves("mbta_predictions", jours)
    suivant = next(prev, None)
    fenetre = deque()
    memoire = Memoire()
    c = collections.Counter()
    info = collections.Counter()  # compteurs informatifs, hors tableau exclusif
    erreurs = collections.defaultdict(list)  # (categorie, niveau) -> [erreurs signées]
    for recu, corps in releves("mbta_vehicles", jours):
        while suivant is not None and suivant[0] <= recu:
            fenetre.append(consolider_releve(suivant[1], suivant[0], memoire, parents))
            suivant = next(prev, None)
        while fenetre and fenetre[0][0] < recu - FENETRE:
            fenetre.popleft()
        liste = list(fenetre)
        for p in lire_gps_mbta(corps, recu):
            c["points GPS"] += 1
            cle_pos = (p.course, p.instant, p.latitude, p.longitude)
            if cle_pos in vus:  # revue R1 : avant `apparier`, sinon un véhicule figé est recompté à chaque relevé
                c["répétition d'une position déjà vue (même updated_at)"] += 1
                continue
            vus.add(cle_pos)
            train, motif = apparier(p, liste)
            if motif:
                c[f"écarté : {motif}"] += 1
                continue
            shape = trips.get(p.course)
            if shape not in formes:
                c["écarté : course sans forme de voie"] += 1
                continue
            voie = voies.setdefault(shape, Voie(formes[shape]))
            gares = tuple(q.gare for q in train.passages if q.gare in arrets)
            cle = (shape, gares)
            if cle not in pks_cache:
                try:
                    pks_cache[cle] = dict(pk_gares(voie, [(g, *((quais[g] and coordonnees_sur_voie(voie, quais[g])) or arrets[g])) for g in gares]))
                except PointHorsVoie:
                    pks_cache[cle] = "une gare hors voie (> 150 m) ou hors ordre"
                except PkAmbigu:
                    pks_cache[cle] = "1re gare ambiguë"
            pks = pks_cache[cle]
            if isinstance(pks, str):
                c[f"écarté : {pks}"] += 1
                continue
            try:
                try:
                    pk_gps = voie.pk(p.latitude, p.longitude)
                except PkAmbigu:
                    if p.course not in dernier_pk:
                        raise
                    # continuité : on tolère un recul du GPS de RECUL_PK m (bruit à l'arrêt), revue R1 point 3
                    pk_gps = voie.pk(p.latitude, p.longitude, apres=dernier_pk[p.course] - RECUL_PK)
                    info["GPS ambigu résolu par continuité du PK"] += 1
            except PointHorsVoie:
                c["écarté : GPS hors voie (> 150 m)"] += 1
                continue
            except PkAmbigu:
                c["écarté : GPS ambigu (boucle)"] += 1
                continue
            dernier_pk[p.course] = pk_gps
            mesure, motif = mesurer(estimer(train, p.instant - timedelta(seconds=decalage), distance_sur_voie(pks)), pks, pk_gps)
            if motif:
                c[f"écarté : {motif}"] += 1
                continue
            c[f"mesuré : {mesure.categorie}"] += 1
            for n in NIVEAUX:
                erreurs[(mesure.categorie, n)].append(mesure.erreurs[n])
    return c, erreurs, info


if __name__ == "__main__":
    args = sys.argv[1:]
    decalage = next((float(a.split("=")[1]) for a in args if a.startswith("--decalage=")), 0.0)
    jours = [a for a in args if not a.startswith("--")] or sorted(p.name for p in (DONNEES / "mbta_vehicles").iterdir())
    c, erreurs, info = compter(jours, decalage)
    total = c["points GPS"]
    repetitions = c["répétition d'une position déjà vue (même updated_at)"]
    uniques = total - repetitions
    print(f"jours : {', '.join(jours)} ; décalage de l'instant du GPS : {decalage:g} s")
    print(f"{total} lignes GPS = {uniques} positions uniques + {repetitions} répétitions. Pourcentages : positions uniques.")
    for k, n in sorted(c.items(), key=lambda kv: -kv[1]):
        if k != "points GPS" and not k.startswith("répétition"):
            print(f"{k:60s} {n:8d}  {100 * n / uniques:5.1f} %")
    somme = sum(n for k, n in c.items() if k != "points GPS" and not k.startswith("répétition"))
    print(f"contrôle : somme des lignes = {somme}, positions uniques = {uniques}, {'OK' if somme == uniques else 'ÉCART'}")
    for k, n in info.items():
        print(f"info : {k} : {n}")
    for cat in ("en route", "à quai"):
        print(f"\nErreur en mètres le long de la voie, {cat}" + (" (positif = estimé en avance) :" if cat == "en route" else " (valeur absolue seulement : sens de marche inconnu à quai) :"))
        for n in NIVEAUX:
            r = resumer(erreurs.get((cat, n), []))
            if r["n"] and (cat == "en route" or n == "niveau1"):
                biais = f"biais {r['biais']:8.1f}  " if cat == "en route" else ""
                print(f"  {n:8s} n={r['n']:6d}  {biais}médiane |e| {r['mediane_abs']:8.1f}  90e centile |e| {r['p90_abs']:8.1f}")
