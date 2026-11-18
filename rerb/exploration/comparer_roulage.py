"""Compare des sorties JSON de rejouer_roulage.py (lecture seule) : stabilité des catégories par tronçon orienté.

Usage : python3 -I rerb/exploration/comparer_roulage.py A.json B.json [--unite occurrence|course]
Critère D2 (chairman) : au moins 80 % des tronçons communs dans la même catégorie (bas/mixte/haut).
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from roulage import categorie, stabilite  # noqa: E402

VARIANTE = "council (tri Aimed, annulés gardés)"


def categories(chemin, unite):
    d = json.loads(Path(chemin).read_text())["variantes"][VARIANTE]["troncons"]
    cats = {}
    for l in d:
        s, n = l[f"{unite}_sous"], l[f"{unite}_n"]
        if n:
            cats[(l["depart"], l["arrivee"])] = (categorie(s / n), l["troncon"])
    return cats


def main():
    p = argparse.ArgumentParser()
    p.add_argument("a")
    p.add_argument("b")
    p.add_argument("--unite", default="occurrence", choices=["occurrence", "course"])
    a = p.parse_args()
    unite = "occ" if a.unite == "occurrence" else "course"
    ca, cb = categories(a.a, unite), categories(a.b, unite)
    communs, ident, diff = stabilite({k: v[0] for k, v in ca.items()}, {k: v[0] for k, v in cb.items()})
    pct = 100 * ident / communs if communs else float("nan")
    print(f"{Path(a.a).stem} vs {Path(a.b).stem} ({a.unite}) : {ident}/{communs} = {pct:.1f} % identiques (communs ; seuil 80 %)")
    for c, x, y in diff:
        print(f"   {ca[c][1]:60s} {x:>5s} -> {y}")


if __name__ == "__main__":
    main()
