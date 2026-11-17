"""BD TOPO (GeoPackage, couche batiment, « En service ») -> bâtiments proches du décor OSM, en Lambert 93, JSON compact.
Usage : python3 -I rerb/exploration/extraire_bdtopo.py couloir.json sortie.json D075.gpkg D077.gpkg ...
Entrées externes non fiables : lecture seule (sqlite3 mode=ro), jamais d'exécution depuis le dossier de données, python -I.
Ne garde que les bâtiments dont la boîte touche une case de 50 m occupée par un bâtiment OSM du décor (marge 50 m)."""
import json
import sqlite3
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bdtopo import lambert93, decoder_geometrie, CASE_M  # noqa: E402
from decor import convertir  # noqa: E402


def cases_occupees(batiments):
    cases = set()
    for b in batiments:
        pts = [lambert93(lon, lat) for lon, lat in b["p"]]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        for cx in range(int((min(xs) - CASE_M) // CASE_M), int((max(xs) + CASE_M) // CASE_M) + 1):
            for cy in range(int((min(ys) - CASE_M) // CASE_M), int((max(ys) + CASE_M) // CASE_M) + 1):
                cases.add((cx, cy))
    return cases


def main(couloir, sortie, gpkgs):
    decor, _ = convertir(json.loads(Path(couloir).read_text(encoding="utf-8")))
    cases = cases_occupees(decor["batiments"])
    xs = [c[0] for c in cases]
    ys = [c[1] for c in cases]
    gx0, gx1, gy0, gy1 = min(xs) * CASE_M, (max(xs) + 1) * CASE_M, min(ys) * CASE_M, (max(ys) + 1) * CASE_M
    sortie_liste, bilan = [], {}
    for chemin in gpkgs:
        c = sqlite3.connect(Path(chemin).resolve().as_uri() + "?mode=ro", uri=True)
        lus = gardes = sans_geom = erreurs = 0
        for blob, h, e in c.execute("select geometrie, hauteur, nombre_d_etages from batiment where etat_de_l_objet = 'En service'"):
            lus += 1
            if not blob or len(blob) < 56 or blob[:2] != b"GP" or ((blob[3] >> 1) & 7) not in (1, 2, 3, 4):
                sans_geom += 1
                continue
            mnx, mxx, mny, mxy = struct.unpack_from("<4d", blob, 8) if blob[3] & 1 else struct.unpack_from(">4d", blob, 8)
            if mxx < gx0 or mnx > gx1 or mxy < gy0 or mny > gy1:
                continue
            if not any((cx, cy) in cases for cx in range(int(mnx // CASE_M), int(mxx // CASE_M) + 1)
                       for cy in range(int(mny // CASE_M), int(mxy // CASE_M) + 1)):
                continue
            try:
                parts = decoder_geometrie(blob)
            except ValueError:
                erreurs += 1
                continue
            if not parts:
                sans_geom += 1
                continue
            sortie_liste.append({"h": h, "e": e, "parts": [[[(round(x, 2), round(y, 2)) for x, y in a] for a in p] for p in parts]})
            gardes += 1
        bilan[Path(chemin).stem] = {"lus": lus, "gardes": gardes, "sans_geometrie": sans_geom, "erreurs": erreurs}
        c.close()
        print(Path(chemin).stem, bilan[Path(chemin).stem], flush=True)
    Path(sortie).write_text(json.dumps({"source": "IGN BD TOPO 3.5, édition 2026-09-15, Licence Ouverte 2.0", "bilan": bilan, "lots": sortie_liste},
                                       separators=(",", ":")), encoding="utf-8")
    print("total gardés", len(sortie_liste))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3:])
