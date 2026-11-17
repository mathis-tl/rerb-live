"""T1 (tâche 4) : durée réellement observée contre durée annoncée, par tronçon. LECTURE SEULE des relevés, aucun appel API.

Usage : python3 -I mesurer_duree_troncon.py [--donnees=CHEMIN] [--sortie=FICHIER.json]        (défaut : ~/rerb-data-2/)
Relevés `prim_stop_monitoring_gares` (47 gares, ~1 relevé / 30 s par gare). Règles et vocabulaire : rerb/duree_troncon.py.
Écrit rerb/donnees/duree_min_troncon.json (table lue par rerb/serveur.py) sauf --sortie=-. Seul le RER B est gardé.
"""
import base64
import gzip
import json
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

RACINE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RACINE / "rerb"))

from duree_troncon import (ECART_MAX, N_MIN, CENTILE, PLANCHER_VITESSE_MS, Observation, apparier_troncons,  # noqa: E402
                           arrivees_observees, centile, classer, cle_troncon, table_duree_min, AMBIGU, ERREUR_DONNEE,
                           OBS_IMPOSSIBLE, RALENTI_ANNONCE, RALENTI_NON_ANNONCE)
from serveur import CSV_GARES, TRACE, _charger  # noqa: E402
from stops import StopNotFound, stop_area_number  # noqa: E402
from verite import departs_par_gare  # noqa: E402

LIGNE = "STIF:Line::C01743:"
SORTIE = RACINE / "rerb" / "donnees" / "duree_min_troncon.json"


def valeur(x):
    return x.get("value") if isinstance(x, dict) else x


def heure(t):
    return datetime.fromisoformat(t.replace("Z", "+00:00")) if t else None


def releves(donnees):
    for f in sorted((donnees / "prim_stop_monitoring_gares").glob("*/*.jsonl.gz")):
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


def charger(donnees):
    """-> ({gare: [(instant, listées, à quai)]}, {(gare, course): [(instant, arrivée annoncée, départ annoncé)]}, nb de relevés)."""
    par_gare, annonces, n = defaultdict(list), defaultdict(list), 0
    for ts, url, liv in releves(donnees):
        ref = parse_qs(urlparse(url).query).get("MonitoringRef", [None])[0]
        try:
            gare = stop_area_number(ref)
        except StopNotFound:
            continue
        n += 1
        listees, quai = set(), set()
        for v in liv["StopMonitoringDelivery"][0].get("MonitoredStopVisit") or []:
            j = v["MonitoredVehicleJourney"]
            if valeur(j.get("LineRef")) != LIGNE:
                continue
            course = valeur(j["FramedVehicleJourneyRef"]["DatedVehicleJourneyRef"])
            appel = j.get("MonitoredCall") or {}
            listees.add(course)
            if appel.get("VehicleAtStop"):
                quai.add(course)
            annonces[(gare, course)].append((ts, heure(appel.get("ExpectedArrivalTime")), heure(appel.get("ExpectedDepartureTime"))))
        par_gare[gare].append((ts, listees, quai))
    for rel in par_gare.values():
        rel.sort(key=lambda x: x[0])
    for a in annonces.values():
        a.sort(key=lambda x: x[0])
    return par_gare, annonces, n


def derniere_annonce(liste, instant):
    """Dernier relevé de (gare, course) reçu au plus tard à `instant`, sinon None."""
    sortie = None
    for x in liste:
        if x[0] > instant:
            break
        sortie = x
    return sortie


def attacher_annonce(o, deps_index, annonces):
    """Annoncé = arrivée annoncée à B (dernier relevé de B avant la fin du départ de A) moins départ annoncé de A (dernier relevé de A à quai)."""
    bas, haut = deps_index[(o.course, o.de, o.instant)]
    a = derniere_annonce(annonces.get((o.de, o.course), []), bas)
    b = derniere_annonce(annonces.get((o.vers, o.course), []), haut)
    if not a or not b or not a[2] or not b[1]:
        return o
    return Observation(o.course, o.de, o.vers, o.duree_s, o.incertitude_s, o.instant, (b[1] - a[2]).total_seconds())


def main():
    donnees, sortie = Path.home() / "rerb-data-2", SORTIE
    for a in sys.argv[1:]:
        if a.startswith("--donnees="):
            donnees = Path(a.split("=", 1)[1])
        elif a.startswith("--sortie="):
            sortie = None if a.split("=", 1)[1] == "-" else Path(a.split("=", 1)[1])
    gares, _, reseau = _charger(CSV_GARES, TRACE)
    voisins = {cle_troncon(a, b): reseau.distance(a, b) for a, b in
               {cle_troncon(a, b) for a in gares for b in gares if a != b and len(reseau.chemin(a, b)) == 2}}
    nom = {n: g.name for n, g in gares.items()}

    par_gare, annonces, n_releves = charger(donnees)
    ecarts = [(t2 - t1).total_seconds() for rel in par_gare.values() for (t1, _, _), (t2, _, _) in zip(rel, rel[1:])]
    print(f"relevés lus : {n_releves} ; gares : {len(par_gare)} ; écart entre relevés d'une gare : médiane {statistics.median(ecarts):.1f} s, "
          f"90e centile {centile(ecarts, 90):.1f} s, > {ECART_MAX.total_seconds():.0f} s : {sum(e > ECART_MAX.total_seconds() for e in ecarts)} sur {len(ecarts)}")

    refus_arr, arrivees = Counter(), []
    for g, rel in par_gare.items():
        a, r = arrivees_observees(rel)
        arrivees += [(g, c, bas, haut) for c, bas, haut in a]
        refus_arr += r
    departs = departs_par_gare({g: [(t, quai) for t, _, quai in rel] for g, rel in par_gare.items()}, ECART_MAX)
    print(f"départs observés : {len(departs)} ; arrivées observées : {len(arrivees)}")
    print("refus d'arrivées :", dict(refus_arr))

    observations, refus = apparier_troncons(departs, arrivees, lambda a, b: cle_troncon(a, b) in voisins)
    print(f"tronçons observés (départ de A -> arrivée à B voisine) : {len(observations)} ; refus d'appariement : {dict(refus)}")
    n_dep = len(departs)
    print(f"part des départs sans durée utilisable : {n_dep - len(observations)}/{n_dep} = {100 * (n_dep - len(observations)) / n_dep:.1f} %")

    index = {(c, g, (bas + (haut - bas) / 2)): (bas, haut) for g, c, bas, haut in departs}
    observations = [attacher_annonce(o, index, annonces) for o in observations]

    table = table_duree_min(observations, voisins)
    classes, par_troncon = Counter(), defaultdict(Counter)
    for o in observations:
        t = table[cle_troncon(o.de, o.vers)]
        mediane = t["mediane_s"] or t["plancher_s"]
        k = classer(o, t["duree_min_s"], t["plancher_s"], mediane)
        classes[k] += 1
        par_troncon[cle_troncon(o.de, o.vers)][k] += 1
    print("\nclassement des observations :")
    for k, v in classes.most_common():
        print(f"  {k:60s} {v:6d}  {100 * v / len(observations):5.1f} %")

    annonces_par = defaultdict(list)
    for o in observations:
        if o.annonce_s is not None:
            annonces_par[cle_troncon(o.de, o.vers)].append(o.annonce_s)
    n_ann = sum(len(v) for v in annonces_par.values())
    sous_plancher = sum(a < table[k]["plancher_s"] for k, v in annonces_par.items() for a in v)
    sous_min = sum(a < table[k]["duree_min_s"] for k, v in annonces_par.items() for a in v)
    print(f"\nannonces lues : {n_ann} ; sous le plancher 33 m/s (ancienne règle) : {sous_plancher} = {100 * sous_plancher / n_ann:.1f} % ; "
          f"sous la durée minimale estimée : {sous_min} = {100 * sous_min / n_ann:.1f} %")
    print("sensibilité au centile (annonces sous la durée minimale, observations sous le plancher écartées) :")
    for cp in (5, 10, 25, 50):
        t2 = table_duree_min(observations, voisins, centile_p=cp)
        f = sum(a < t2[k]["duree_min_s"] for k, v in annonces_par.items() for a in v)
        print(f"  centile {cp:2d} : {f} sur {n_ann} = {100 * f / n_ann:.1f} %")

    print("\ntronçon ; distance m ; n valides ; observé médiane s ; annoncé médiane s ; plancher s ; durée min s ; source ; "
          "annoncés < durée min ; erreurs de donnée ; ambigus ; obs. impossibles")
    for cle, t in sorted(table.items(), key=lambda kv: -kv[1]["duree_min_s"] / kv[1]["plancher_s"]):
        c = par_troncon.get(cle, Counter())
        ann = annonces_par.get(cle, [])
        courts = sum(a < t["duree_min_s"] for a in ann)
        med = f"{t['mediane_s']:.0f}" if t["mediane_s"] else "-"
        am = f"{statistics.median(ann):.0f}" if ann else "-"
        print(f"{nom[cle[0]]} | {nom[cle[1]]} ; {t['distance_m']:.0f} ; {t['n']} ; {med} ; {am} ; {t['plancher_s']:.0f} ; "
              f"{t['duree_min_s']:.0f} ; {t['source']} ; {courts}/{len(ann)} ; {c[ERREUR_DONNEE]} ; {c[AMBIGU]} ; {c[OBS_IMPOSSIBLE]}")
    print("\ntronçons ambigus (au moins 2 observations « annoncé trop court, observé long ») :")
    for cle, c in sorted(par_troncon.items(), key=lambda kv: -kv[1][AMBIGU]):
        if c[AMBIGU] >= 2:
            print(f"  {nom[cle[0]]} | {nom[cle[1]]} : {c[AMBIGU]} ambigus, {c[ERREUR_DONNEE]} erreurs de donnée, "
                  f"{c[RALENTI_ANNONCE]} ralentissements annoncés, {c[RALENTI_NON_ANNONCE]} non annoncés")

    if sortie is not None:
        out = {"méthode": f"max(plancher {PLANCHER_VITESSE_MS:.0f} m/s, centile {CENTILE} des durées observées valides) si au moins {N_MIN} observations, sinon plancher ; correction estimée",
               "relevés": n_releves, "observations": len(observations),
               "troncons": {f"{a}|{b}": {"duree_min_s": round(t["duree_min_s"], 1), "source": t["source"], "n": t["n"],
                                         "distance_m": round(t["distance_m"], 1)} for (a, b), t in sorted(table.items())}}
        sortie.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\nécrit : {sortie}")


if __name__ == "__main__":
    main()
