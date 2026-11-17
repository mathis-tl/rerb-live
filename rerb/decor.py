"""Tâche 4, décor 3D : extrait OpenStreetMap (Overpass `out geom`) -> fichier compact pour la carte (deck.gl).

Données © contributeurs OpenStreetMap, licence ODbL : attribution affichée dans la carte. Le fichier produit n'est pas versionné
(`web/data/` est ignoré) ; il se régénère avec `rerb/exploration/fabriquer_decor.py`.
Choix simples, annoncés : relations = contours extérieurs seulement (pas de cours intérieures) ; bâtiment : champ h_src (osm, etages, defaut ; ign réservé),
sans hauteur : HAUTEUR_DEFAUT_M, et le nombre de bâtiments concernés est compté ; coordonnées arrondies à 5 décimales (~1 m).
Bibliothèque standard seulement.
"""
import re

HAUTEUR_DEFAUT_M = 9.0          # ≈ 3 niveaux
METRES_PAR_NIVEAU = 3.0
HAUTEUR_MAX_M = 400.0
CLASSES = {
    "grand": {"motorway", "motorway_link", "trunk", "trunk_link", "primary", "primary_link"},
    "moyen": {"secondary", "secondary_link", "tertiary", "tertiary_link"},
    "petit": {"residential", "unclassified", "living_street", "service", "road"},
    "pieton": {"footway", "path", "cycleway", "steps", "pedestrian", "track"},
}
VERTS = {"leisure": {"park", "garden", "pitch"}, "landuse": {"grass", "forest", "recreation_ground", "village_green", "cemetery"},
         "natural": {"wood"}}


def classe_route(valeur):
    return next((c for c, v in CLASSES.items() if valeur in v), None)


def _nombre(texte):
    m = re.match(r"\s*(-?\d+(?:[.,]\d+)?)", str(texte))
    return float(m.group(1).replace(",", ".")) if m else None


def _plausible(h):
    return h is not None and 0 < h <= HAUTEUR_MAX_M


def hauteur_m(tags):
    """-> (hauteur en mètres, h_src). Priorité : height OSM (« osm »), puis building:levels × 3 m (« etages »),
    puis HAUTEUR_DEFAUT_M (« defaut »). Une height absurde est ignorée et on essaie les étages.
    La valeur « ign » est réservée (étape 2, croisement BD TOPO) : jamais produite ici."""
    h = _nombre(tags["height"]) if "height" in tags else None
    if _plausible(h):
        return h, "osm"
    n = _nombre(tags["building:levels"]) if "building:levels" in tags else None
    if n is not None and _plausible(n * METRES_PAR_NIVEAU):
        return n * METRES_PAR_NIVEAU, "etages"
    return HAUTEUR_DEFAUT_M, "defaut"


def _pts(geometrie):
    return [[round(p["lon"], 5), round(p["lat"], 5)] for p in geometrie]


def _ferme(g):
    return len(g) >= 4 and g[0] == g[-1]


def convertir(overpass):
    """-> (décor, comptes). Lève ValueError si ce n'est pas une réponse Overpass."""
    try:
        elements = overpass["elements"]
    except (KeyError, TypeError):
        raise ValueError("extrait OSM illisible") from None
    d = {k: [] for k in ("batiments", "routes", "rails", "quais", "verts", "eaux", "cours_eau")}
    c = {"batiments": 0, "batiments_hauteur_par_defaut": 0,
         "h_src_osm": 0, "h_src_ign": 0, "h_src_etages": 0, "h_src_defaut": 0, "ecartes_non_fermes": 0, "ecartes_sans_geometrie": 0}
    for e in elements:
        t = e.get("tags") or {}
        if e.get("type") == "relation":
            contours = [m.get("geometry") for m in e.get("members", []) if m.get("role") == "outer" and m.get("geometry")]
        else:
            contours = [e["geometry"]] if e.get("geometry") else []
        if not contours:
            c["ecartes_sans_geometrie"] += 1
            continue
        if "building" in t:
            h, h_src = hauteur_m(t)
            for g in contours:
                p = _pts(g)
                if not _ferme(p):
                    c["ecartes_non_fermes"] += 1
                    continue
                d["batiments"].append({"p": p, "h": h, "h_src": h_src})
                c["batiments"] += 1
                c["h_src_" + h_src] += 1
                c["batiments_hauteur_par_defaut"] += h_src == "defaut"
            continue
        g = _pts(contours[0])
        if "highway" in t:
            cl = classe_route(t["highway"])
            if cl:
                d["routes"].append({"c": g, "t": cl})
        elif t.get("railway") == "platform" or t.get("public_transport") == "platform":
            if _ferme(g):
                d["quais"].append(g)
        elif t.get("railway") in ("rail", "subway", "tram"):
            d["rails"].append(g)
        elif t.get("natural") == "water" and _ferme(g):
            d["eaux"].append(g)
        elif "waterway" in t:
            d["cours_eau"].append(g)
        elif any(t.get(k) in v for k, v in VERTS.items()) and _ferme(g):
            d["verts"].append(g)
    return d, c
