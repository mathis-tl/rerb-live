"""couloir.json (extraction PBF) -> web/data/decor/ : une cellule JSON par case de 0,02° (~1,5 km) + index.json.
Usage : python3 -I rerb/exploration/fabriquer_decor_ligne.py [couloir.json] [dossier_sortie] [bat_ign.json]   (entrée externe : -I).
bat_ign.json (facultatif) = sortie de extraire_bdtopo.py : hauteurs BD TOPO (h_src « ign »), priorité OSM height > IGN > étages > 9 m.
Sans lui : OSM seul (étape 1)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bdtopo import dedoublonner, enrichir  # noqa: E402
from decor import convertir  # noqa: E402
from decor_ligne import decouper_cellules  # noqa: E402

TAILLE = 0.02
brut = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.home() / "rerb-osm" / "brut" / "ligne" / "couloir.json"
dossier = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(__file__).resolve().parents[2] / "web" / "data" / "decor"
ign = Path(sys.argv[3]) if len(sys.argv) > 3 else None
decor, comptes = convertir(json.loads(brut.read_text(encoding="utf-8")))
SOURCE = "© contributeurs OpenStreetMap, licence ODbL"
repartition = {k[6:]: v for k, v in comptes.items() if k.startswith("h_src_")}
croisement = None
if ign:
    donnees = json.loads(ign.read_text(encoding="utf-8"))
    croisement = enrichir(decor["batiments"], dedoublonner(donnees["lots"]))
    repartition = {k: croisement["h_src"].get(k, 0) for k in ("osm", "ign", "etages", "defaut")}
    SOURCE += " ; hauteurs : " + donnees["source"]
cellules = decouper_cellules(decor, TAILLE)
dossier.mkdir(parents=True, exist_ok=True)
for ancien in dossier.glob("*.json"):
    ancien.unlink()
total = 0
for cle, contenu in cellules.items():
    f = dossier / f"{cle}.json"
    f.write_text(json.dumps(contenu, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    total += f.stat().st_size
(dossier / "index.json").write_text(json.dumps({"taille_deg": TAILLE, "cellules": sorted(cellules), "source": SOURCE,
                                                                      "h_src": repartition, "bd_topo": croisement}), encoding="utf-8")
print(comptes, croisement, f"{len(cellules)} cellules, {total / 1e6:.1f} Mo")
