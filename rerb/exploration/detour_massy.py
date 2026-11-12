"""Examen du détour Massy-Verrières -> Massy-Palaiseau (lecture seule)."""
import csv, json, math, sys
trace_p, csv_p = sys.argv[1:3]
def d(a, b):
    x = math.radians(b[1] - a[1]) * math.cos(math.radians(a[0]))
    y = math.radians(b[0] - a[0])
    return 6371000 * math.hypot(x, y)
rows = list(csv.DictReader(open(csv_p, encoding="utf-8")))
st = {r["stop_name"]: (float(r["stop_lat"]), float(r["stop_lon"])) for r in rows}
A, B = st["Massy - Verrières"], st["Massy - Palaiseau"]
sh = json.load(open(trace_p))["results"][0]["shape"]
mor = sh.get("geometry", sh)["coordinates"]
print("ligne droite A-B :", round(d(A, B)), "m")
for i in (11, 12, 33, 14):
    seg = mor[i]
    cum = [0.0]
    for p, q in zip(seg, seg[1:]):
        cum.append(cum[-1] + d((p[1], p[0]), (q[1], q[0])))
    da = [d(A, (p[1], p[0])) for p in seg]
    db = [d(B, (p[1], p[0])) for p in seg]
    ka = min(range(len(seg)), key=lambda k: da[k])
    kb = min(range(len(seg)), key=lambda k: db[k])
    print(f"\n=== morceau {i} : {len(seg)} pts ; sommet le plus proche de A = #{ka} ({da[ka]:.0f} m), de B = #{kb} ({db[kb]:.0f} m), fin du morceau #{len(seg)-1}")
    print("sommets à < 150 m de A :", [k for k in range(len(seg)) if da[k] <= 150])
    print("sommets à < 150 m de B :", [k for k in range(len(seg)) if db[k] <= 150])
    lo = max(0, min(ka, kb) - 3)
    hi = min(len(seg) - 1, max(ka, kb) + 8)
    print("# | lat | lon | pas m | PK m | dist A | dist B")
    for k in range(lo, hi + 1):
        pas = d((seg[k-1][1], seg[k-1][0]), (seg[k][1], seg[k][0])) if k else 0
        print(f"{k} | {seg[k][1]:.5f} | {seg[k][0]:.5f} | {pas:.0f} | {cum[k]:.0f} | {da[k]:.0f} | {db[k]:.0f}")
