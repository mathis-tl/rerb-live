"""Fabrique web/data/decor-massy.json depuis l'extrait OSM brut (hors dépôt, ~/rerb-osm/brut/massy.json).
Usage : python3 -I outils/decor/fabriquer_decor.py [brut.json] [sortie.json]   (entrée = donnée externe : toujours -I)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "rerb"))
from decor import convertir  # noqa: E402

brut = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.home() / "rerb-osm" / "brut" / "massy.json"
sortie = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(__file__).resolve().parents[2] / "web" / "data" / "decor-massy.json"
decor, comptes = convertir(json.loads(brut.read_text(encoding="utf-8")))
decor["source"] = "© contributeurs OpenStreetMap, licence ODbL"
sortie.parent.mkdir(parents=True, exist_ok=True)
sortie.write_text(json.dumps(decor, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
print({k: len(v) for k, v in decor.items() if isinstance(v, list)}, comptes, f"{sortie.stat().st_size / 1e6:.2f} Mo")
