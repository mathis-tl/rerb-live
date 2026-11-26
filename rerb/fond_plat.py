"""Fond plat de toute la ligne : ce qu'on voit au sol quand on dézoome (zones, parcs, eaux, grandes routes), sans bâtiments.

Choix (21/11 : « plus de vide, comme Apple Plans, au moins tout ce qui concerne le projet ») :
• cellules de `taille_deg` (0,05° ~ 3,7 x 5,5 km) dans un couloir élargi (marge de quelques km) autour du tracé ;
• une requête Overpass par cellule (boîte), seulement le fond : pas de bâtiments, pas de petites rues (ils restent près des rails, voir decor_ligne.py) ;
• coordonnées arrondies à 5 décimales (~1 m) et points alignés retirés ; un objet n'est gardé que dans la cellule de son premier point.
Bibliothèque standard seulement ; aucun réseau ici (le téléchargement est dans telecharger_fond.py).
"""
import math

M_PAR_DEG_LAT = 111_000.0
CLES = ("zones", "verts", "eaux", "routes", "cours_eau")
FILTRES = (
    'way["highway"~"^(motorway|trunk|primary|secondary|tertiary)$"]',
    'way["leisure"~"^(park|garden)$"]',
    'way["landuse"~"^(forest|grass|recreation_ground|cemetery|meadow|village_green|residential|industrial|commercial|retail)$"]',
    'way["natural"~"^(water|wood)$"]',
    'way["waterway"~"^(river|canal)$"]',
    'relation["natural"="water"]',
)
VERTS = {("leisure", "park"), ("leisure", "garden"), ("natural", "wood")} | {
    ("landuse", v) for v in ("forest", "grass", "recreation_ground", "cemetery", "meadow", "village_green")}
ZONES = {("landuse", v) for v in ("residential", "industrial", "commercial", "retail")}
GRANDES = {"motorway", "trunk", "primary"}
MOYENNES = {"secondary", "tertiary"}


def _cle(lon, lat, t):
    return f"{math.floor(lon / t)}_{math.floor(lat / t)}"


def cellules_du_couloir(points, marge_m=4000, taille_deg=0.05):
    """points (lon, lat) du tracé -> ensemble de clés de cellules touchées par le tracé élargi de `marge_m`."""
    cles = set()
    for lon, lat in points:
        d_lat = marge_m / M_PAR_DEG_LAT
        d_lon = marge_m / (M_PAR_DEG_LAT * math.cos(math.radians(lat)))
        i0, i1 = math.floor((lon - d_lon) / taille_deg), math.floor((lon + d_lon) / taille_deg)
        j0, j1 = math.floor((lat - d_lat) / taille_deg), math.floor((lat + d_lat) / taille_deg)
        cles.update(f"{i}_{j}" for i in range(i0, i1 + 1) for j in range(j0, j1 + 1))
    return cles


def requete_cellule(cle, taille_deg=0.05):
    i, j = (int(x) for x in cle.split("_"))
    sud, ouest = round(j * taille_deg, 6), round(i * taille_deg, 6)
    boite = f"{sud},{ouest},{round(sud + taille_deg, 6)},{round(ouest + taille_deg, 6)}"
    corps = "".join(f" {f};\n" for f in FILTRES)
    return f"[out:json][timeout:120][bbox:{boite}];\n(\n{corps});\nout geom;\n"


def simplifier(ligne, tol_deg=5e-5):
    """Douglas-Peucker : retire les points à moins de `tol_deg` du segment qui les encadre (extrémités toujours gardées)."""
    n = len(ligne)
    if n < 3:
        return list(ligne)
    garde = [False] * n
    garde[0] = garde[-1] = True
    pile = [(0, n - 1)]
    while pile:
        i, j = pile.pop()
        (x0, y0), (x1, y1) = ligne[i], ligne[j]
        dx, dy = x1 - x0, y1 - y0
        long2 = dx * dx + dy * dy
        pire, k_pire = -1.0, -1
        for k in range(i + 1, j):
            x, y = ligne[k]
            if long2 == 0:
                d = math.hypot(x - x0, y - y0)
            else:
                t = max(0.0, min(1.0, ((x - x0) * dx + (y - y0) * dy) / long2))
                d = math.hypot(x - (x0 + t * dx), y - (y0 + t * dy))
            if d > pire:
                pire, k_pire = d, k
        if pire > tol_deg:
            garde[k_pire] = True
            pile += [(i, k_pire), (k_pire, j)]
    return [p for p, g in zip(ligne, garde) if g]


def _pts(geometrie):
    return [[round(g["lon"], 5), round(g["lat"], 5)] for g in geometrie]


def _famille(tags):
    for cle, val in tags.items():
        if (cle, val) in VERTS:
            return "verts"
        if (cle, val) in ZONES:
            return "zones"
    if tags.get("natural") == "water":
        return "eaux"
    if tags.get("waterway") in ("river", "canal"):
        return "cours_eau"
    h = tags.get("highway")
    return "routes" if h in GRANDES or h in MOYENNES else None


def convertir(reponse, cle, taille_deg=0.05):
    """Réponse Overpass -> {famille: [objets]} pour la cellule `cle` (surfaces : anneaux [[lon, lat]] ; routes : {c, t} ; cours d'eau : chemins)."""
    sortie = {k: [] for k in CLES}
    for e in reponse.get("elements", []):
        fam = _famille(e.get("tags", {}))
        if fam is None:
            continue
        if e.get("type") == "relation":
            chemins = [_pts(m["geometry"]) for m in e.get("members", []) if m.get("role") == "outer" and m.get("geometry")]
        elif e.get("geometry"):
            chemins = [_pts(e["geometry"])]
        else:
            continue
        surface = fam in ("verts", "zones", "eaux")
        for c in chemins:
            if len(c) < (4 if surface else 2) or (surface and c[0] != c[-1]):
                continue
            if _cle(c[0][0], c[0][1], taille_deg) != cle:
                continue
            c = simplifier(c)
            if fam == "routes":
                sortie[fam].append({"c": c, "t": "grand" if e["tags"]["highway"] in GRANDES else "moyen"})
            else:
                sortie[fam].append(c)
    return sortie


def decouper(cellules):
    """{clé: fond} -> sans les cellules vides."""
    return {k: v for k, v in cellules.items() if any(v.get(c) for c in CLES)}
