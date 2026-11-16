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


def _ecart_m(p, q):
    return math.hypot((q[0] - p[0]) * M_PAR_DEG_LAT * math.cos(math.radians(p[1])), (q[1] - p[1]) * M_PAR_DEG_LAT)


def requetes(points, rayon_m=350, par_requete=30, ecart_max_m=700.0):
    """points (lon, lat) -> liste de requêtes Overpass QL (une par paquet, un point commun entre paquets).
    Overpass relie les points d'un `around` dans l'ordre donné : on coupe donc le paquet dès que deux points consécutifs
    sont à plus de `ecart_max_m` (le tracé IDFM est fait de morceaux qui ne se suivent pas)."""
    if not points:
        return []
    paquets, courant = [], [points[0]]
    for p in points[1:]:
        if _ecart_m(courant[-1], p) > ecart_max_m:
            paquets.append(courant)
            courant = [p]
        elif len(courant) >= par_requete:
            paquets.append(courant)
            courant = [courant[-1], p]       # un point commun : pas de trou entre deux requêtes
        else:
            courant.append(p)
    paquets.append(courant)
    sortie = []
    for p in paquets:
        coord = ",".join(f"{lat},{lon}" for lon, lat in p)
        corps = "".join(f"  {f}(around:{rayon_m},{coord});\n" for f in FILTRES)
        sortie.append(f"[out:json][timeout:180];\n(\n{corps});\nout geom;\n")
    return sortie


class Couloir:
    """Ensemble des points à moins de `rayon_m` d'un des points échantillonnés du tracé (grille de cases de `rayon_m`)."""

    def __init__(self, points, rayon_m=350.0):
        self.rayon = rayon_m
        self._m_lon = M_PAR_DEG_LAT * math.cos(math.radians(sum(p[1] for p in points) / len(points))) if points else 1.0
        self._cases = {}
        for lon, lat in points:
            self._cases.setdefault(self._case(lon, lat), []).append(self._xy(lon, lat))

    def _xy(self, lon, lat):
        return (lon * self._m_lon, lat * M_PAR_DEG_LAT)

    def _case(self, lon, lat):
        x, y = self._xy(lon, lat)
        return (math.floor(x / self.rayon), math.floor(y / self.rayon))

    def contient(self, lon, lat):
        if not self._cases:
            return False
        cx, cy = self._case(lon, lat)
        x, y = self._xy(lon, lat)
        r2 = self.rayon ** 2
        return any((x - px) ** 2 + (y - py) ** 2 <= r2
                   for i in (-1, 0, 1) for j in (-1, 0, 1) for px, py in self._cases.get((cx + i, cy + j), ()))

    def touche(self, sommets):
        """Vrai si au moins un sommet (lon, lat) est dans le couloir."""
        return any(self.contient(lon, lat) for lon, lat in sommets)


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
