"""Chaîne de mesure commune (Boston, Helsinki) : points GPS + relevés de prévisions -> erreurs en mètres.

Exploration (LECTURE SEULE des relevés de l'enregistreur). Logique testée dans test_mbta.py, test_hsl.py,
test_mesure.py, test_voie.py. Chaque point GPS tombe dans UNE seule ligne du tableau : mesuré ou écarté avec son
motif (E4). Une position déjà vue (même course, instant, latitude, longitude) n'est mesurée qu'une fois : les
répétitions sont comptées à part (revue R1). Mémoire : seuls les relevés de prévisions des FENETRE dernières
secondes avant chaque relevé GPS sont gardés (un GPS périmé de plus de 120 s est écarté avant toute recherche).
"""
import collections
import sys
from collections import deque
from datetime import timedelta
from pathlib import Path

RACINE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RACINE / "rerb"))

from mbta import apparier  # noqa: E402
from mesure import NIVEAUX, mesurer, resumer  # noqa: E402
from position import estimer  # noqa: E402
from voie import PkAmbigu, PointHorsVoie, Voie, distance_sur_voie, pk_gares  # noqa: E402

FENETRE = timedelta(seconds=300)
RECUL_PK = 50.0
REPETITION = "répétition d'une position déjà vue"


def mesurer_flux(points, previsions, forme_de, arrets, formes, coords_gare, premier_passage=False, decalage=0.0, amont=None):
    """points : itérable de (recu, [PointGps]) ; previsions : itérable de (recu, {course: Train}), tous deux
    en ordre de réception ; forme_de(course) -> shape_id ; coords_gare(voie, gare) -> (lat, lon).
    amont : Counter des points déjà écartés avant la chaîne (S4), ajoutés au total et au tableau.
    -> (compteurs exclusifs, erreurs par (catégorie, niveau), compteurs informatifs)."""
    voies, pks_cache, dernier_pk, vus = {}, {}, {}, set()
    prev = iter(previsions)
    suivant = next(prev, None)
    fenetre = deque()
    c = collections.Counter()
    info = collections.Counter()  # compteurs informatifs, hors tableau exclusif
    erreurs = collections.defaultdict(list)  # (categorie, niveau) -> [erreurs signées]
    for recu, liste_points in points:
        while suivant is not None and suivant[0] <= recu:
            fenetre.append(suivant)
            suivant = next(prev, None)
        while fenetre and fenetre[0][0] < recu - FENETRE:
            fenetre.popleft()
        liste = list(fenetre)
        for p in liste_points:
            c["points GPS"] += 1
            cle_pos = (p.course, p.instant, p.latitude, p.longitude)
            if cle_pos in vus:  # revue R1 : avant `apparier`, sinon un véhicule figé est recompté à chaque relevé
                c[REPETITION] += 1
                continue
            vus.add(cle_pos)
            train, motif = apparier(p, liste)
            if motif:
                c[f"écarté : {motif}"] += 1
                continue
            shape = forme_de(p.course)
            if shape not in formes:
                c["écarté : course sans forme de voie"] += 1
                continue
            voie = voies.setdefault(shape, Voie(formes[shape]))
            gares = tuple(q.gare for q in train.passages if q.gare in arrets)
            cle = (shape, gares)
            if cle not in pks_cache:
                try:
                    pks_cache[cle] = dict(pk_gares(voie, [(g, *coords_gare(voie, g)) for g in gares], premier_passage=premier_passage))
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
    for motif, n in (amont or {}).items():
        c["points GPS"] += n
        c[f"écarté : {motif}"] += n
    return c, erreurs, info


def afficher(c, erreurs, info, entete):
    total = c["points GPS"]
    repetitions = c[REPETITION]
    uniques = total - repetitions
    print(entete)
    print(f"{total} lignes GPS = {uniques} positions uniques + {repetitions} répétitions. Pourcentages : positions uniques.")
    for k, n in sorted(c.items(), key=lambda kv: -kv[1]):
        if k != "points GPS" and k != REPETITION:
            print(f"{k:60s} {n:8d}  {100 * n / uniques:5.1f} %")
    somme = sum(n for k, n in c.items() if k != "points GPS" and k != REPETITION)
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
