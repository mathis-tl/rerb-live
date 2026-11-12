import json, csv, math, sys
from collections import Counter
trace = json.load(open(sys.argv[1]))
rows = list(csv.DictReader(open(sys.argv[2], encoding="utf-8")))
stops = [(r["stop_name"], float(r["stop_lat"]), float(r["stop_lon"])) for r in rows]
def d(a, b):
    x = math.radians(b[1]-a[1]) * math.cos(math.radians(a[0]))
    y = math.radians(b[0]-a[0])
    return 6371000*math.hypot(x, y)
def near(lat, lon):
    n = min(stops, key=lambda s: d((lat, lon), (s[1], s[2])))
    return n[0], d((lat, lon), (n[1], n[2]))
def length(pts):
    return sum(d((p[1], p[0]), (q[1], q[0])) for p, q in zip(pts, pts[1:]))
print(trace.keys() if isinstance(trace, dict) else type(trace))
sh = trace["results"][0]["shape"]
geo = sh.get("geometry", sh)
print(geo.get("type"), len(geo["coordinates"]))
res = []
for i, seg in enumerate(geo["coordinates"]):
    a, b = seg[0], seg[-1]
    na, da = near(a[1], a[0]); nb, db = near(b[1], b[0])
    # gares traversées (à moins de 100 m d'un point du morceau)
    cnt = 0
    for s in stops:
        if min(d((p[1], p[0]), (s[1], s[2])) for p in seg) < 100: cnt += 1
    res.append((i, len(seg), round(length(seg)/1000, 1), na, round(da), nb, round(db), cnt))
for r in res: print(*r, sep=" | ")
print(Counter((r[3], r[5]) for r in res).most_common(60))
print("départs distincts:", Counter(r[3] for r in res))
print("arrivées distinctes:", Counter(r[5] for r in res))
