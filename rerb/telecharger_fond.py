"""Télécharge le fond plat de toute la ligne (une fois) : python3 rerb/telecharger_fond.py [--cache DOSSIER] [--marge-m 4000]

Une requête Overpass par cellule (miroirs essayés à tour de rôle, reprise possible : les réponses brutes sont gardées dans --cache),
puis écrit web/data/fond/<cellule>.json et web/data/fond/index.json. Données © contributeurs OpenStreetMap (ODbL).
"""
import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

from decor_ligne import echantillonner
from fond_plat import cellules_du_couloir, convertir, decouper, requete_cellule

RACINE = Path(__file__).resolve().parent.parent
TRACE = RACINE / "data" / "static" / "rerb" / "rerb_trace_C01743.json"
SORTIE = RACINE / "web" / "data" / "fond"
MIROIRS = ("https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter")   # le 1er répondait seul le 21/11 (les autres temporisaient)
TAILLE_DEG = 0.05
SOURCE = "© contributeurs OpenStreetMap, licence ODbL"


def telecharger(requete, tentatives=12):
    donnees = urllib.parse.urlencode({"data": requete}).encode()
    for n in range(tentatives):
        url = MIROIRS[0] if n < tentatives - 2 else MIROIRS[1]
        try:
            req = urllib.request.Request(url, data=donnees, headers={"User-Agent": "rerb-live-fond-plat"})
            with urllib.request.urlopen(req, timeout=150) as r:
                return json.loads(r.read())
        except Exception as e:   # saturé, délai, JSON tronqué : on réessaie avec une attente croissante
            print(f"  essai {n + 1} ({url.split('/')[2]}) : {e}", file=sys.stderr)
            time.sleep(min(60, 8 * (n + 1)))
    raise RuntimeError("Overpass injoignable")


def ecrire(fonds):
    gardees = decouper(fonds)
    SORTIE.mkdir(parents=True, exist_ok=True)
    for ancien in SORTIE.glob("*.json"):
        ancien.unlink()
    for cle, fond in gardees.items():
        (SORTIE / f"{cle}.json").write_text(json.dumps(fond, separators=(",", ":")), encoding="utf-8")
    (SORTIE / "index.json").write_text(json.dumps({"taille_deg": TAILLE_DEG, "source": SOURCE, "cellules": sorted(gardees)}, separators=(",", ":")), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", type=Path, required=True, help="dossier des réponses brutes (reprise)")
    ap.add_argument("--marge-m", type=float, default=4000)
    a = ap.parse_args()
    a.cache.mkdir(parents=True, exist_ok=True)
    SORTIE.mkdir(parents=True, exist_ok=True)
    lignes = json.loads(TRACE.read_text(encoding="utf-8"))["results"][0]["shape"]
    lignes = lignes.get("geometry", lignes)["coordinates"]
    points = echantillonner(lignes, pas_m=1000)
    cles = sorted(cellules_du_couloir(points, a.marge_m, TAILLE_DEG))
    print(len(cles), "cellules à traiter")
    fonds = {}
    for n, cle in enumerate(cles, 1):
        brut = a.cache / f"{cle}.json"
        if brut.exists():
            rep = json.loads(brut.read_text(encoding="utf-8"))
        else:
            rep = telecharger(requete_cellule(cle, TAILLE_DEG))
            brut.write_text(json.dumps(rep), encoding="utf-8")
            time.sleep(2)
        fonds[cle] = convertir(rep, cle, TAILLE_DEG)
        print(f"{n}/{len(cles)} {cle} : {sum(len(v) for v in fonds[cle].values())} objets", flush=True)
        if n % 3 == 0:
            ecrire(fonds)   # fichiers à jour au fil de l'eau : la carte peut déjà s'en servir
    ecrire(fonds)
    print(len(decouper(fonds)), "cellules écrites dans", SORTIE)


if __name__ == "__main__":
    main()
