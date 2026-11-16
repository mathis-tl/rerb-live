"""Décor de toute la ligne : couloir autour du tracé -> requêtes Overpass -> fusion -> cellules chargées selon la vue.

Choix (décision de Mathis : « ce qui nous intéresse se situe aux abords des gares et des voies ») :
• couloir de `rayon_m` autour du tracé (échantillonné tous les `pas_m`), via `around` à plusieurs points (polyligne) ;
• requêtes de `par_requete` points, avec un point de recouvrement entre deux requêtes (pas de trou) ;
• doublons entre requêtes retirés par (type, id) ; • découpage en cellules de `taille_deg` degrés (~1,5 km), un fichier par cellule.
Un objet appartient à la cellule de son premier point (un grand bâtiment à cheval sur deux cellules n'est dans qu'une).
Bibliothèque standard seulement ; aucun réseau ici (le téléchargement est dans exploration/telecharger_decor_ligne.py).
"""
import math

FILTRES = ('way["building"]', 'relation["building"]', 'way["highway"]', 'way["railway"~"rail|platform|subway|tram"]',
           'way["leisure"~"park|garden|pitch"]', 'way["landuse"~"grass|forest|recreation_ground|village_green|cemetery"]',
           'way["natural"~"water|wood"]', 'way["waterway"]', 'way["public_transport"="platform"]')
M_PAR_DEG_LAT = 111_000.0
CLES = ("batiments", "routes", "rails", "quais", "verts", "eaux", "cours_eau")


def echantillonner(trace, pas_m=250.0):
    """trace : liste de chemins [[lon, lat], ...] -> points (lon, lat) tous les ~pas_m, sans doublon (arrondi ~25 m)."""
    if pas_m <= 0:
        raise ValueError("pas_m doit être > 0")
    vus, points = set(), []
    case = pas_m * 0.8      # un seul point par case : les morceaux qui se recouvrent ne multiplient pas les requêtes

    def ajouter(lon, lat):
        k = (math.floor(lon * M_PAR_DEG_LAT * math.cos(math.radians(lat)) / case), math.floor(lat * M_PAR_DEG_LAT / case))
        if k not in vus:
            vus.add(k)
            points.append((round(lon, 5), round(lat, 5)))

    for chemin in trace:
        for (x0, y0), (x1, y1) in zip(chemin, chemin[1:]):
            dx = (x1 - x0) * M_PAR_DEG_LAT * math.cos(math.radians((y0 + y1) / 2))
            dy = (y1 - y0) * M_PAR_DEG_LAT
            n = max(1, int(math.hypot(dx, dy) // pas_m))
            for i in range(n):
                ajouter(x0 + (x1 - x0) * i / n, y0 + (y1 - y0) * i / n)
        if chemin:
            ajouter(*chemin[-1])
    return points


def requetes(points, rayon_m=350, par_requete=30):
    """points (lon, lat) -> liste de requêtes Overpass QL (une par paquet, un point commun entre paquets)."""
    if not points:
        return []
    paquets, i = [], 0
    while True:
        paquets.append(points[i:i + par_requete])
        if i + par_requete >= len(points):
            break
        i += par_requete - 1
    sortie = []
    for p in paquets:
        coord = ",".join(f"{lat},{lon}" for lon, lat in p)
        corps = "".join(f"  {f}(around:{rayon_m},{coord});\n" for f in FILTRES)
        sortie.append(f"[out:json][timeout:180];\n(\n{corps});\nout geom;\n")
    return sortie


def fusionner(reponses):
    """Plusieurs réponses Overpass -> une seule, sans doublon (type, id). ValueError si une réponse est illisible."""
    vus, elements = set(), []
    for r in reponses:
        try:
            liste = r["elements"]
        except (KeyError, TypeError):
            raise ValueError("réponse Overpass illisible") from None
        for e in liste:
            k = (e.get("type"), e.get("id"))
            if k not in vus:
                vus.add(k)
                elements.append(e)
    return {"elements": elements}


def cellule_de(lon, lat, taille_deg):
    return f"{math.floor(lon / taille_deg)}_{math.floor(lat / taille_deg)}"


def _premier(objet, cle):
    if cle == "batiments":
        return objet["p"][0]
    if cle == "routes":
        return objet["c"][0]
    return objet[0]


def decouper_cellules(decor, taille_deg=0.02):
    """décor complet -> {clé de cellule: décor partiel}. Les cellules vides n'existent pas."""
    cellules = {}
    for cle in CLES:
        for objet in decor.get(cle, []):
            lon, lat = _premier(objet, cle)
            c = cellules.setdefault(cellule_de(lon, lat, taille_deg), {k: [] for k in CLES})
            c[cle].append(objet)
    return cellules
