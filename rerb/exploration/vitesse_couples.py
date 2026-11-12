"""Test de vitesse (lecture seule). Compare 3 règles de choix de distance par couple de gares.
Usage : python3 -I test_vitesse.py trace.json arrets.csv echantillon.json"""
import csv, json, math, statistics, sys
from datetime import datetime
from collections import defaultdict

trace_p, csv_p, ech_p = sys.argv[1:4]

def d(a, b):
    x = math.radians(b[1] - a[1]) * math.cos(math.radians(a[0]))
    y = math.radians(b[0] - a[0])
    return 6371000 * math.hypot(x, y)

rows = list(csv.DictReader(open(csv_p, encoding="utf-8")))
stops = {r["stop_id"].split(":")[-1]: (r["stop_name"], float(r["stop_lat"]), float(r["stop_lon"])) for r in rows}
name = {n: v[0] for n, v in stops.items()}
sh = json.load(open(trace_p))["results"][0]["shape"]
morceaux = sh.get("geometry", sh)["coordinates"]

cums = []
for seg in morceaux:
    cum = [0.0]
    for p, q in zip(seg, seg[1:]):
        cum.append(cum[-1] + d((p[1], p[0]), (q[1], q[0])))
    cums.append(cum)

def build_pk(seuil):
    pk = []
    for seg, cum in zip(morceaux, cums):
        m = {}
        for n, (_, la, lo) in stops.items():
            ds = [d((la, lo), (p[1], p[0])) for p in seg]
            k = min(range(len(seg)), key=lambda i: ds[i])
            if ds[k] <= seuil:
                m[n] = cum[k]
        pk.append(m)
    return pk

def t(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()

f = json.load(open(ech_p))["Siri"]["ServiceDelivery"]["EstimatedTimetableDelivery"][0]["EstimatedJourneyVersionFrame"][0]["EstimatedVehicleJourney"]
temps = defaultdict(list)   # (A,B) -> durées de parcours en s (horaires prévus)
for v in f:
    lst = []
    for c in v["EstimatedCalls"]["EstimatedCall"]:
        n = c["StopPointRef"]["value"].split(":")[3]
        arr = c.get("AimedArrivalTime") or c.get("AimedDepartureTime")
        dep = c.get("AimedDepartureTime") or c.get("AimedArrivalTime")
        if arr and dep:
            lst.append((t(arr), t(dep), n))
    lst.sort()
    for (a1, d1, na), (a2, d2, nb) in zip(lst, lst[1:]):
        dt = a2 - d1
        if dt >= 30:
            temps[(na, nb)].append(dt)
duree = {k: statistics.median(v) for k, v in temps.items()}
print(f"couples avec durée mesurable : {len(duree)}")

def regle_plus_court(ds):
    return min(ds)

def regle_mediane(ds):
    return statistics.median(ds)

def regle_majorite(ds, tol=50.0):
    # groupes de distances à moins de tol m de l'une d'elles ; on garde le groupe le plus nombreux
    best = None
    for x in ds:
        g = [y for y in ds if abs(y - x) <= tol]
        if best is None or len(g) > len(best):
            best = g
    egal = [x for x in ds if len([y for y in ds if abs(y - x) <= tol]) == len(best)]
    return statistics.median(best), len(egal) > len(best)  # (valeur, égalité entre groupes ?)

REGLES = {"majorité": None, "plus court": regle_plus_court, "médiane": regle_mediane}

for seuil in (100, 150, 300):
    pk = build_pk(seuil)
    cand = {}
    for (a, b) in duree:
        ds = [m[b] - m[a] for m in pk if a in m and b in m and m[a] < m[b]]
        if ds:
            cand[(a, b)] = ds
    print(f"\n===== seuil {seuil} m : {len(cand)} couples avec distances, {len(duree) - len(cand)} sans =====")
    print("règle | couples | vitesse > 120 km/h | vitesse > 100 km/h | vitesse < 15 km/h | distance < ligne droite | vitesse médiane km/h")
    detail = {}
    for nom in REGLES:
        v = []
        nb120 = nb100 = nb15 = nbdroite = 0
        for k, ds in cand.items():
            if nom == "majorité":
                dist, egal = regle_majorite(ds)
            else:
                dist = REGLES[nom](ds)
            kmh = dist / duree[k] * 3.6
            v.append(kmh)
            nb120 += kmh > 120
            nb100 += kmh > 100
            nb15 += kmh < 15
            la = stops[k[0]]; lb = stops[k[1]]
            nbdroite += dist < d((la[1], la[2]), (lb[1], lb[2])) - 1
            detail[(nom, k)] = (dist, kmh)
        print(f"{nom} | {len(v)} | {nb120} | {nb100} | {nb15} | {nbdroite} | {statistics.median(v):.0f}")
    if seuil == 150:
        print("\nCouples où la majorité donne > 100 km/h ou diffère du plus court de plus de 100 m")
        print("A -> B | durée s | nb morceaux | majorité m (km/h) | plus court m (km/h) | médiane m (km/h)")
        sel = []
        for k, ds in cand.items():
            mj = detail[("majorité", k)]; pc = detail[("plus court", k)]; md = detail[("médiane", k)]
            if mj[1] > 100 or abs(mj[0] - pc[0]) > 100:
                sel.append((k, len(ds), mj, pc, md))
        for k, n, mj, pc, md in sorted(sel, key=lambda x: -x[2][1])[:25]:
            print(f"{name[k[0]]} -> {name[k[1]]} | {duree[k]:.0f} | {n} | {mj[0]:.0f} ({mj[1]:.0f}) | {pc[0]:.0f} ({pc[1]:.0f}) | {md[0]:.0f} ({md[1]:.0f})")
        print(f"\n(nombre total de couples listés : {len(sel)})")
