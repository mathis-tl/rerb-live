"""Télécharge (Overpass, un appel par requête, séquentiel, avec pause) le couloir OSM autour de TOUT le tracé du RER B.
Brut -> ~/rerb-osm/brut/ligne/requete_NN.json (hors dépôt). Reprend où il s'est arrêté (fichiers existants conservés).
Usage : python3 outils/decor/telecharger_decor_ligne.py [--rayon 350] [--sec-entre 15] [--essai]
--essai : affiche le nombre de requêtes sans rien télécharger. Aucune donnée personnelle envoyée (zones géographiques seulement)."""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "rerb"))
from decor_ligne import echantillonner, requetes  # noqa: E402
from serveur import charger_geo  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--rayon", type=int, default=350)
ap.add_argument("--sec-entre", type=int, default=15)
ap.add_argument("--essai", action="store_true")
a = ap.parse_args()
points = echantillonner(charger_geo()["trace"], 250)
reqs = requetes(points, a.rayon, 30)
print(f"{len(points)} points, {len(reqs)} requêtes, rayon {a.rayon} m")
if a.essai:
    sys.exit(0)
dossier = Path.home() / "rerb-osm" / "brut" / "ligne"
dossier.mkdir(parents=True, exist_ok=True)
for i, q in enumerate(reqs):
    f = dossier / f"requete_{i:02d}.json"
    if f.exists() and f.stat().st_size > 100:
        continue
    for essai in range(3):
        r = subprocess.run(["curl", "-sS", "-m", "240", "-A", "rerb-live-client", "--data-urlencode", "data@-",
                            "https://overpass-api.de/api/interpreter", "-o", str(f) + ".part", "-w", "%{http_code}"],
                           input=q, capture_output=True, text=True)
        ok = r.stdout.strip() == "200"
        if ok:
            try:
                reponse = json.loads(Path(str(f) + ".part").read_text(encoding="utf-8"))
                # Une réponse sans élément ou avec un « remark » (délai dépassé, mémoire) n'est PAS un succès.
                ok = bool(reponse.get("elements")) and "remark" not in reponse
            except ValueError:
                ok = False
        if ok:
            Path(str(f) + ".part").rename(f)
            print(f"requête {i + 1}/{len(reqs)} : {f.stat().st_size / 1e6:.1f} Mo")
            break
        print(f"requête {i + 1}/{len(reqs)} : échec (HTTP {r.stdout.strip()}), essai {essai + 1}/3")
        time.sleep(60 * 2 ** essai)       # attente croissante : 1, 2, 4 minutes (le serveur public est partagé)
    else:
        sys.exit(f"abandon à la requête {i + 1}")
    time.sleep(a.sec_entre)
