"""Contrôle du croisement OSM / BD TOPO sur le couloir. Usage : python3 -I outils/decor/controle_bdtopo.py couloir.json bat_ign.json [sortie_resultats.json]
1) bâtiments OSM déjà cotés (height, étages) : écart OSM - IGN (médiane, 90e centile des écarts absolus, biais) ;
2) bâtiments 'defaut' : part récupérée, part refusée par raison ; valeurs écartées."""
import json
import statistics
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "rerb"))
from bdtopo import croiser, dedoublonner  # noqa: E402
from decor import convertir  # noqa: E402


def centile(v, q):
    v = sorted(v)
    return v[min(len(v) - 1, int(q * len(v)))]


def resume(nom, ecarts):
    if not ecarts:
        print(nom, ": aucun")
        return
    ab = [abs(e) for e in ecarts]
    print(f"{nom} : n={len(ecarts)} biais(OSM-IGN)={statistics.mean(ecarts):+.2f} m médiane écart abs={statistics.median(ab):.2f} m "
          f"90e centile abs={centile(ab, 0.9):.2f} m | médiane signée={statistics.median(ecarts):+.2f} m")


def main(couloir, ign_json, sortie=None):
    decor, comptes = convertir(json.loads(Path(couloir).read_text(encoding="utf-8")))
    donnees = json.loads(Path(ign_json).read_text(encoding="utf-8"))
    lots = dedoublonner(donnees["lots"])
    print("lots IGN lus", len(donnees["lots"]), "après dédoublonnage", len(lots))
    bats = decor["batiments"]
    t = time.time()
    res = croiser(bats, lots, progression=lambda n: print("  ...", n, f"{time.time() - t:.0f}s", flush=True))
    print(f"croisement : {len(bats)} bâtiments en {time.time() - t:.0f} s")
    groupes = {}
    for b, (r, raison) in zip(bats, res):
        g = groupes.setdefault(b["h_src"], Counter())
        g["total"] += 1
        if r is None:
            g["refus:" + raison] += 1
            continue
        g["apparie"] += 1
        g["ign_hauteur"] += r["hauteur"] is not None
        g["ign_etages_seuls"] += r["hauteur"] is None and r["etages"] is not None
        g["multi"] += r["n"] > 1
    for k, g in groupes.items():
        print(k, dict(g))
    ec_h = [b["h"] - r["hauteur"] for b, (r, _) in zip(bats, res) if b["h_src"] == "osm" and r and r["hauteur"] is not None]
    resume("OSM height vs IGN hauteur", ec_h)
    ec_e = [b["h"] - r["hauteur"] for b, (r, _) in zip(bats, res) if b["h_src"] == "etages" and r and r["hauteur"] is not None]
    resume("OSM étages x 3 vs IGN hauteur", ec_e)
    ec_ee = [b["h"] - r["etages"] * 3 for b, (r, _) in zip(bats, res) if b["h_src"] == "etages" and r and r["etages"] is not None]
    resume("OSM étages x 3 vs IGN étages x 3", ec_ee)
    big = sum(abs(e) > 6 for e in ec_e)
    print("étages OSM : écart > 6 m avec IGN hauteur :", big, "sur", len(ec_e))
    dh = [r["hauteur"] for b, (r, _) in zip(bats, res) if b["h_src"] == "defaut" and r and r["hauteur"] is not None]
    if dh:
        print(f"defaut récupérés (hauteur IGN) : n={len(dh)} médiane={statistics.median(dh):.1f} m 10e={centile(dh, 0.1):.1f} 90e={centile(dh, 0.9):.1f} max={max(dh):.1f}")
    if sortie:
        Path(sortie).write_text(json.dumps([[r, raison] for r, raison in res]), encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
