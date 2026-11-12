"""Table des gares consécutives (lecture seule).
1) nettoie chaque morceau des aller-retours (pointes) ; 2) projette chaque gare sur les segments (tous les passages) ;
3) pour chaque morceau, liste les gares dans l'ordre -> couples voisins ; 4) agrège par couple ;
5) teste l'additivité sur les 106 couples des trains (chemin dans le graphe des couples voisins) et la vitesse."""
import csv, json, math, statistics, sys
from collections import defaultdict, deque
from datetime import datetime
trace_p, csv_p, ech_p = sys.argv[1:4]
SEUIL, TOL_RETRACE, GAP_MIN = 150.0, 10.0, 20.0

R = 6371000.0
def xy(lat, lon, lat0):
    return (math.radians(lon) * math.cos(math.radians(lat0)) * R, math.radians(lat) * R)
def d(a, b):
    x = math.radians(b[1] - a[1]) * math.cos(math.radians(a[0]))
    y = math.radians(b[0] - a[0])
    return R * math.hypot(x, y)

rows = list(csv.DictReader(open(csv_p, encoding="utf-8")))
stops = {r["stop_id"].split(":")[-1]: (r["stop_name"], float(r["stop_lat"]), float(r["stop_lon"])) for r in rows}
name = {n: v[0] for n, v in stops.items()}
sh = json.load(open(trace_p))["results"][0]["shape"]
mor = sh.get("geometry", sh)["coordinates"]   # [lon, lat]

def nettoie(seg):
    """Supprime les retours en arrière : un sommet qui retombe (à < 10 m) sur un sommet déjà parcouru
    (au moins 20 m de chemin plus tôt) efface tout ce qui a été parcouru entre les deux."""
    pile, cum = [], []
    retire = 0
    for p in seg:
        pt = (p[1], p[0])
        j_ok = None
        for j in range(len(pile)):
            if cum[-1] - cum[j] > GAP_MIN and d(pile[j], pt) < TOL_RETRACE:
                j_ok = j
                break
        if j_ok is not None:
            retire += len(pile) - 1 - j_ok
            del pile[j_ok + 1:]; del cum[j_ok + 1:]
            continue
        if pile:
            cum.append(cum[-1] + d(pile[-1], pt))
        else:
            cum.append(0.0)
        pile.append(pt)
    return pile, cum, retire

def passages(pile, cum, la, lo):
    """PK de chaque passage près de la gare, par projection sur les segments."""
    lat0 = la
    g = xy(la, lo, lat0)
    res = []   # (indice segment, dist, pk)
    for i in range(len(pile) - 1):
        a = xy(pile[i][0], pile[i][1], lat0); b = xy(pile[i + 1][0], pile[i + 1][1], lat0)
        vx, vy = b[0] - a[0], b[1] - a[1]
        L2 = vx * vx + vy * vy
        t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((g[0] - a[0]) * vx + (g[1] - a[1]) * vy) / L2))
        px, py = a[0] + t * vx, a[1] + t * vy
        dist = math.hypot(g[0] - px, g[1] - py)
        if dist <= SEUIL:
            res.append((i, dist, cum[i] + t * (cum[i + 1] - cum[i])))
    groupes, cur = [], []
    for r in res:
        if cur and r[0] - cur[-1][0] > 3:
            groupes.append(cur); cur = []
        cur.append(r)
    if cur: groupes.append(cur)
    return [min(g, key=lambda r: r[1])[2] for g in groupes]

nettoyes = []
total_retire = 0
for seg in mor:
    pile, cum, retire = nettoie(seg)
    total_retire += retire
    nettoyes.append((pile, cum, retire))
print(f"morceaux : {len(mor)} ; sommets retirés par nettoyage : {total_retire} sur {sum(len(s) for s in mor)}")
print("morceaux avec retours en arrière détectés :", [(i, n[2]) for i, n in enumerate(nettoyes) if n[2] > 0])

pass_par_morceau = []
for pile, cum, _ in nettoyes:
    ps = {}
    for n, (_, la, lo) in stops.items():
        p = passages(pile, cum, la, lo)
        if p: ps[n] = p
    pass_par_morceau.append(ps)

# couples voisins par morceau
voisins = defaultdict(list)    # frozenset({A,B}) -> distances
for ps in pass_par_morceau:
    ordre = sorted((pk, n) for n, l in ps.items() for pk in l)
    for (pa, na), (pb, nb) in zip(ordre, ordre[1:]):
        if na != nb:
            voisins[frozenset((na, nb))].append(pb - pa)
print(f"\ncouples voisins distincts (non orientés) : {len(voisins)}   (attendu pour un arbre de 47 gares : 46)")
print("A | B | nb morceaux | min m | médiane m | max m | ligne droite m | médiane/droite")
elem = {}
for k, ds in sorted(voisins.items(), key=lambda kv: -statistics.median(kv[1])):
    a, b = tuple(k)
    vol = d(stops[a][1:], stops[b][1:])
    med = statistics.median(ds)
    elem[k] = med
    print(f"{name[a]} | {name[b]} | {len(ds)} | {min(ds):.0f} | {med:.0f} | {max(ds):.0f} | {vol:.0f} | {med / vol:.2f}")

# graphe + additivité
adj = defaultdict(list)
for k, dist in elem.items():
    a, b = tuple(k)
    adj[a].append((b, dist)); adj[b].append((a, dist))
def chemin(a, b):
    seen = {a: 0.0}; q = deque([a])
    while q:
        x = q.popleft()
        for y, w in adj[x]:
            if y not in seen:
                seen[y] = seen[x] + w; q.append(y)
    return seen.get(b)

def t(s): return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
f = json.load(open(ech_p))["Siri"]["ServiceDelivery"]["EstimatedTimetableDelivery"][0]["EstimatedJourneyVersionFrame"][0]["EstimatedVehicleJourney"]
temps = defaultdict(list)
for v in f:
    lst = []
    for c in v["EstimatedCalls"]["EstimatedCall"]:
        n = c["StopPointRef"]["value"].split(":")[3]
        arr = c.get("AimedArrivalTime") or c.get("AimedDepartureTime")
        dep = c.get("AimedDepartureTime") or c.get("AimedArrivalTime")
        if arr and dep: lst.append((t(arr), t(dep), n))
    lst.sort()
    for (a1, d1, na), (a2, d2, nb) in zip(lst, lst[1:]):
        if a2 - d1 >= 30: temps[(na, nb)].append(a2 - d1)
duree = {k: statistics.median(v) for k, v in temps.items()}

print(f"\nAdditivité : {len(duree)} couples de trains, distance = somme des couples voisins le long du graphe")
sans, kmh_l, rat_l = 0, [], []
ecart_direct = []
for (a, b), dur in duree.items():
    dist = chemin(a, b)
    if dist is None:
        sans += 1; continue
    kmh = dist / dur * 3.6
    vol = d(stops[a][1:], stops[b][1:])
    kmh_l.append((kmh, name[a], name[b], dist, dur, vol))
    rat_l.append(dist / vol)
    # distance directe : plus petit PK_B - PK_A sur les morceaux qui contiennent A puis B
    c = []
    for ps in pass_par_morceau:
        if a in ps and b in ps:
            x = [pb - pa for pa in ps[a] for pb in ps[b] if pb > pa]
            if x: c.append(min(x))
    if c: ecart_direct.append((dist - statistics.median(c), name[a], name[b], dist, statistics.median(c)))
print(f"sans chemin : {sans}")
kmh_l.sort(reverse=True)
print(f"vitesse > 120 km/h : {sum(1 for x in kmh_l if x[0] > 120)} ; > 100 : {sum(1 for x in kmh_l if x[0] > 100)} ; < 15 : {sum(1 for x in kmh_l if x[0] < 15)} ; médiane {statistics.median([x[0] for x in kmh_l]):.0f} km/h")
print(f"ratio somme/ligne droite : min {min(rat_l):.2f}, médiane {statistics.median(rat_l):.2f}, max {max(rat_l):.2f} ; > 1,35 : {sum(1 for r in rat_l if r > 1.35)} ; < 1 : {sum(1 for r in rat_l if r < 1.0)}")
print("5 vitesses les plus élevées : km/h | A -> B | distance m | durée s | ligne droite m")
for k, a, b, dist, dur, vol in kmh_l[:5]:
    print(f"{k:.0f} | {a} -> {b} | {dist:.0f} | {dur:.0f} | {vol:.0f}")
ecart_direct.sort(key=lambda x: -abs(x[0]))
ed = [abs(x[0]) for x in ecart_direct]
print(f"écart somme du graphe - médiane des distances directes : médiane {statistics.median(ed):.0f} m, p90 {sorted(ed)[int(.9*len(ed))]:.0f} m, max {max(ed):.0f} m ; > 100 m : {sum(1 for x in ed if x > 100)} sur {len(ed)}")
print("5 plus gros écarts : écart m | A -> B | somme graphe | médiane directe")
for e, a, b, s, m in ecart_direct[:5]:
    print(f"{e:.0f} | {a} -> {b} | {s:.0f} | {m:.0f}")
