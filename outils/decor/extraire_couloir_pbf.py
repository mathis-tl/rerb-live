"""Extrait d'un fichier OSM PBF (Geofabrik, hors dépôt) les objets du couloir autour du tracé du RER B.
Sortie : ~/rerb-osm/brut/ligne/couloir.json au format Overpass `out geom` (lu ensuite par decor.convertir).
À lancer avec l'interpréteur qui a pyosmium : ~/rerb-osm/.venv/bin/python outils/decor/extraire_couloir_pbf.py [pbf] [rayon_m]
Règles : lignes (rues, voies ferrées, cours d'eau) = ways ; surfaces (bâtiments, parcs, eau, quais) = zones assemblées par
osmium (relations multipolygones comprises), contour extérieur seulement. Un objet est gardé si un sommet est dans le couloir."""
import json
import sys
from pathlib import Path

import osmium

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "rerb"))
from decor_ligne import Couloir, echantillonner  # noqa: E402
from serveur import charger_geo  # noqa: E402

pbf = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.home() / "rerb-osm" / "brut" / "ile-de-france.osm.pbf"
rayon = float(sys.argv[2]) if len(sys.argv) > 2 else 350.0
sortie = Path.home() / "rerb-osm" / "brut" / "ligne" / "couloir.json"
couloir = Couloir(echantillonner(charger_geo()["trace"], 250), rayon)

CLES = ("building", "highway", "railway", "leisure", "landuse", "natural", "waterway", "public_transport")
LIGNES = ("highway", "waterway")
elements, comptes = [], {"lignes": 0, "zones": 0, "hors_couloir": 0}


def geom(noeuds):
    return [{"lat": n.lat, "lon": n.lon} for n in noeuds]


def garder(tags, g, type_):
    if not couloir.touche([(p["lon"], p["lat"]) for p in g]):
        comptes["hors_couloir"] += 1
        return
    elements.append({"type": type_, "tags": tags, "geometry": g})


fp = osmium.FileProcessor(str(pbf), osmium.osm.NODE | osmium.osm.WAY | osmium.osm.RELATION | osmium.osm.AREA).with_locations().with_areas() \
    .with_filter(osmium.filter.KeyFilter(*CLES))
for o in fp:
    if o.is_node() or o.is_relation():
        continue
    tags = {t.k: t.v for t in o.tags}
    if o.is_way():
        # lignes : rues, cours d'eau, voies ferrées (hors quais) ; le reste est traité comme zone ci-dessous
        ligne = any(k in tags for k in LIGNES) or tags.get("railway") in ("rail", "subway", "tram")
        if not ligne or "building" in tags:
            continue
        try:
            g = geom(o.nodes)
        except osmium.InvalidLocationError:
            continue
        comptes["lignes"] += 1
        garder(tags, g, "way")
    elif o.is_area():
        if any(k in tags for k in LIGNES) or tags.get("railway") in ("rail", "subway", "tram"):
            continue
        for anneau in o.outer_rings():
            comptes["zones"] += 1
            garder(tags, geom(anneau), "way")
sortie.parent.mkdir(parents=True, exist_ok=True)
sortie.write_text(json.dumps({"elements": elements}, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
print(comptes, len(elements), "éléments gardés,", f"{sortie.stat().st_size / 1e6:.1f} Mo")
