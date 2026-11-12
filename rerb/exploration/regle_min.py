"""Test du 2e prompt de Gemini (lecture seule) : projection sur segment, passages multiples, facteur 0,99 et 1,35,
règle du minimum valide. Compare à la médiane valide. Vérifie 'tous les morceaux tombent sur 1 541 m' et '98,9 m de la voie'."""
import csv, json, math, statistics, sys
from collections import Counter, defaultdict
from datetime import datetime
trace_p, csv_p, ech_p = sys.argv[1:4]
SEUIL, R, BAS, HAUT = 150.0, 6371000.0, 0.99, 1.35
def d(a, b):
    x = math.radians(b[1] - a[1]) * math.cos(math.radians(a[0])); y = math.radians(b[0] - a[0])
    return R * math.hypot(x, y)
rows = list(csv.DictReader(open(csv_p, encoding="utf-8")))
stops = {r["stop_id"].split(":")[-1]: (r["stop_name"], float(r["stop_lat"]), float(r["stop_lon"])) for r in rows}
name = {n: v[0] for n, v in stops.items()}; byname = {v[0]: n for n, v in stops.items()}
sh = json.load(open(trace_p))["results"][0]["shape"]
mor = sh.get("geometry", sh)["coordinates"]

def cumul(pts):
    c = [0.0]
    for p, q in zip(pts, pts[1:]): c.append(c[-1] + d(p, q))
    return c
def brut(seg):
    pts = [(p[1], p[0]) for p in seg]; return pts, cumul(pts)
def nettoie(seg):
    pile, cum = [], []
    for p in seg:
        pt = (p[1], p[0]); j_ok = None
        for j in range(len(pile)):
            if cum[-1] - cum[j] > 20 and d(pile[j], pt) < 10: j_ok = j; break
        if j_ok is not None:
            del pile[j_ok + 1:]; del cum[j_ok + 1:]; continue
        cum.append(cum[-1] + d(pile[-1], pt) if pile else 0.0); pile.append(pt)
    return pile, cum
def passages(pts, cum, la, lo):
    k0 = math.cos(math.radians(la)) * R * math.pi / 180; k1 = R * math.pi / 180
    g = (lo * k0, la * k1); res = []
    for i in range(len(pts) - 1):
        a = (pts[i][1] * k0, pts[i][0] * k1); b = (pts[i + 1][1] * k0, pts[i + 1][0] * k1)
        vx, vy = b[0] - a[0], b[1] - a[1]; L2 = vx * vx + vy * vy
        t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((g[0] - a[0]) * vx + (g[1] - a[1]) * vy) / L2))
        dist = math.hypot(g[0] - a[0] - t * vx, g[1] - a[1] - t * vy)
        if dist <= SEUIL: res.append((i, dist, cum[i] + t * (cum[i + 1] - cum[i]), t))
    gr, cur = [], []
    for r in res:
        if cur and r[0] - cur[-1][0] > 3: gr.append(cur); cur = []
        cur.append(r)
    if cur: gr.append(cur)
    return [min(g, key=lambda r: r[1]) for g in gr]   # (segment, distance voie, pk, t)

def t_(s): return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
f = json.load(open(ech_p))["Siri"]["ServiceDelivery"]["EstimatedTimetableDelivery"][0]["EstimatedJourneyVersionFrame"][0]["EstimatedVehicleJourney"]
temps = defaultdict(list)
for v in f:
    lst = []
    for c in v["EstimatedCalls"]["EstimatedCall"]:
        n = c["StopPointRef"]["value"].split(":")[3]
        arr = c.get("AimedArrivalTime") or c.get("AimedDepartureTime"); dep = c.get("AimedDepartureTime") or c.get("AimedArrivalTime")
        if arr and dep: lst.append((t_(arr), t_(dep), n))
    lst.sort()
    for (a1, d1, na), (a2, d2, nb) in zip(lst, lst[1:]):
        if a2 - d1 >= 30: temps[(na, nb)].append(a2 - d1)
duree = {k: statistics.median(v) for k, v in temps.items()}
A, B = byname["Massy - Verrières"], byname["Massy - Palaiseau"]

for titre, prep in (("BRUTS", brut), ("NETTOYÉS", nettoie)):
    pm = []
    for seg in mor:
        pts, cum = prep(seg)
        ps = {n: passages(pts, cum, la, lo) for n, (_, la, lo) in stops.items()}
        pm.append({n: p for n, p in ps.items() if p})
    res, sans = [], 0
    massy = []
    for (a, b), dur in duree.items():
        vol = d(stops[a][1:], stops[b][1:]); val = []
        for i, ps in enumerate(pm):
            if a in ps and b in ps:
                c = sorted(pb[2] - pa[2] for pa in ps[a] for pb in ps[b] if pb[2] > pa[2])
                ok = [x for x in c if BAS * vol <= x <= HAUT * vol]
                if ok: val.append(ok[0])
                if (a, b) == (A, B): massy.append((i, c[0] if c else None, ok[0] if ok else None))
        if not val: sans += 1; continue
        res.append((name[a], name[b], len(val), min(val), statistics.median(val), max(val), vol, dur))
    print(f"\n===== MORCEAUX {titre} (facteur bas {BAS}, haut {HAUT}) : {len(res)} couples, {sans} sans morceau valide =====")
    r_min = [r[3] / r[6] for r in res]; k_min = [r[3] / r[7] * 3.6 for r in res]
    print(f"minimum valide : ratio distance/ligne droite min {min(r_min):.2f}, médiane {statistics.median(r_min):.2f}, max {max(r_min):.2f} ; vitesse médiane {statistics.median(k_min):.0f} km/h, max {max(k_min):.0f}, > 100 km/h : {sum(1 for k in k_min if k > 100)}")
    diff = [r for r in res if r[4] - r[3] > 50]
    print(f"couples où médiane valide et minimum valide diffèrent de plus de 50 m : {len(diff)} sur {len(res)}")
    print("A -> B | morceaux valides | minimum | médiane | max | ligne droite | vitesse du minimum")
    for r in sorted(diff, key=lambda r: -(r[4] - r[3]))[:8]:
        print(f"{r[0]} -> {r[1]} | {r[2]} | {r[3]:.0f} | {r[4]:.0f} | {r[5]:.0f} | {r[6]:.0f} | {r[3] / r[7] * 3.6:.0f} km/h")
    vals = Counter(round(x[2]) for x in massy if x[2])
    print(f"Massy - Verrières -> Massy - Palaiseau : distances valides par morceau (arrondies, valeur : nb) {dict(sorted(vals.items()))} ; {sum(1 for x in massy if x[2] is None)} morceaux sans valeur valide")

# distance de la gare Massy-Palaiseau à la voie (segment le plus proche), par morceau brut
la, lo = stops[B][1], stops[B][2]
dm = []
for seg in mor:
    pts, cum = brut(seg)
    k0 = math.cos(math.radians(la)) * R * math.pi / 180; k1 = R * math.pi / 180
    g = (lo * k0, la * k1); best = 1e9
    for i in range(len(pts) - 1):
        a = (pts[i][1] * k0, pts[i][0] * k1); b = (pts[i + 1][1] * k0, pts[i + 1][0] * k1)
        vx, vy = b[0] - a[0], b[1] - a[1]; L2 = vx * vx + vy * vy
        tt = 0.0 if L2 == 0 else max(0.0, min(1.0, ((g[0] - a[0]) * vx + (g[1] - a[1]) * vy) / L2))
        best = min(best, math.hypot(g[0] - a[0] - tt * vx, g[1] - a[1] - tt * vy))
    dm.append(best)
print(f"\nGare Massy - Palaiseau à la voie (segment le plus proche), 49 morceaux : min {min(dm):.0f} m, médiane {statistics.median(dm):.0f} m, max {max(dm):.0f} m")
