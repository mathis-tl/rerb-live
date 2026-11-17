"""T2 étape 2 : hauteurs des bâtiments par croisement avec la BD TOPO v3 de l'IGN (couche `batiment`).

Données IGN : Licence Ouverte 2.0 (source et date à citer). Aucune donnée IGN n'est dans le dépôt.
• lambert93 : WGS84 (lon, lat) -> Lambert 93 (EPSG:2154, mètres). RGF93 et WGS84 diffèrent de ~1 m : négligé (annoncé).
• decoder_geometrie : géométrie GeoPackage (en-tête « GP » + WKB) -> liste de polygones (liste d'anneaux).
• apparier : taux de recouvrement SURFACIQUE (pas le centroïde). L'aire d'intersection est estimée par échantillonnage régulier
  de points dans le bâtiment OSM (pas de découpage de polygones : robuste aux formes en L ou en U et aux cours intérieures ;
  erreur moyenne ~0,1 % mais plus forte sur les très petites emprises (sous ~25 m², pas minimal 0,25 m) :
  la revue du 17/11 a mesuré jusqu'à +27 points de recouvrement sur un bâtiment de 1,2 m²).
• Règle de retenue : un bâtiment IGN est candidat si I/aire(OSM) >= 0,20 ou I/aire(IGN) >= 0,20 ; les candidats cotés
  (hauteur plausible) doivent couvrir au moins 50 % du bâtiment OSM ; hauteur = moyenne pondérée par l'aire d'intersection.
• choisir_hauteur : priorité OSM height > IGN hauteur > étages OSM x 3 m > IGN nombre_d_etages x 3 m > 9 m.
Bibliothèque standard seulement.
"""
import math
import struct

HAUTEUR_MAX_M = 400.0
ETAGES_MAX = 100
METRES_PAR_ETAGE = 3.0
SEUIL_CANDIDAT = 0.20
SEUIL_COUVERTURE = 0.50
POINTS_VISES = 400
PAS_MIN_M, PAS_MAX_M = 0.25, 2.5
CASE_M = 50.0

# --- projection Lambert 93 (IGN, NTG 71) ---
_A, _E = 6378137.0, 0.0818191910428158
_L0, _P0, _P1, _P2 = math.radians(3.0), math.radians(46.5), math.radians(44.0), math.radians(49.0)
_X0, _Y0 = 700000.0, 6600000.0


def _m(p):
    return math.cos(p) / math.sqrt(1 - _E ** 2 * math.sin(p) ** 2)


def _t(p):
    return math.tan(math.pi / 4 - p / 2) / ((1 - _E * math.sin(p)) / (1 + _E * math.sin(p))) ** (_E / 2)


_N = (math.log(_m(_P1)) - math.log(_m(_P2))) / (math.log(_t(_P1)) - math.log(_t(_P2)))
_F = _m(_P1) / (_N * _t(_P1) ** _N)
_R0 = _A * _F * _t(_P0) ** _N


def lambert93(lon, lat):
    r = _A * _F * _t(math.radians(lat)) ** _N
    g = _N * (math.radians(lon) - _L0)
    return _X0 + r * math.sin(g), _Y0 + _R0 - r * math.cos(g)


# --- géométrie GeoPackage ---
_TAILLE_ENV = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}


def _lire_wkb(b, i, polys):
    ordre = "<" if b[i] == 1 else ">"
    t = struct.unpack_from(ordre + "I", b, i + 1)[0]
    i += 5
    base, dim = t % 1000, {0: 2, 1: 3, 2: 3, 3: 4}.get(t // 1000)
    if t >= 1000 and dim is None or base not in (3, 6):
        raise ValueError("type de géométrie non géré : %d" % t)
    dim = dim or 2
    if base == 6:
        n = struct.unpack_from(ordre + "I", b, i)[0]
        i += 4
        for _ in range(n):
            i = _lire_wkb(b, i, polys)
        return i
    nb = struct.unpack_from(ordre + "I", b, i)[0]
    i += 4
    anneaux = []
    for _ in range(nb):
        n = struct.unpack_from(ordre + "I", b, i)[0]
        i += 4
        pts = []
        for _ in range(n):
            pts.append(struct.unpack_from(ordre + "dd", b, i))
            i += 8 * dim
        anneaux.append(pts)
    if not anneaux or any(len(a) < 4 for a in anneaux):
        raise ValueError("polygone sans anneau ou anneau trop court (donnée externe non fiable)")
    polys.append(anneaux)
    return i


def decoder_geometrie(blob):
    """Blob GeoPackage -> liste de polygones (chacun : liste d'anneaux [(x, y), ...], extérieur d'abord).
    None si absent ou vide. ValueError si le contenu est incohérent (donnée externe non fiable)."""
    if not blob or len(blob) < 8 or blob[:2] != b"GP":
        return None
    flags = blob[3]
    if flags & 0x10:
        return None
    env = (flags >> 1) & 7
    if env not in _TAILLE_ENV:
        raise ValueError("enveloppe GeoPackage inconnue")
    debut = 8 + _TAILLE_ENV[env]
    polys = []
    try:
        _lire_wkb(blob, debut, polys)
    except struct.error:
        raise ValueError("WKB tronqué") from None
    return polys


def aire_polygone(anneaux):
    """Aire (m²) : anneau extérieur moins les trous (formule du lacet)."""
    def lacet(a):
        return abs(sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(a, a[1:]))) / 2
    return lacet(anneaux[0]) - sum(lacet(a) for a in anneaux[1:])


def _boite(anneaux):
    xs = [p[0] for p in anneaux[0]]
    ys = [p[1] for p in anneaux[0]]
    return min(xs), min(ys), max(xs), max(ys)


def _dans(x, y, anneaux):
    """Point dans polygone (règle pair-impair sur tous les anneaux : les trous sont exclus)."""
    dedans = False
    for a in anneaux:
        for (x0, y0), (x1, y1) in zip(a, a[1:]):
            if (y0 > y) != (y1 > y) and x < x0 + (y - y0) * (x1 - x0) / (y1 - y0):
                dedans = not dedans
    return dedans


def _plausible_h(h):
    return h is not None and 0 < h <= HAUTEUR_MAX_M


def _plausible_e(e):
    return e is not None and 0 < e <= ETAGES_MAX


class IndexIGN:
    """lots : liste de {"parts": [polygone, ...], "h": hauteur|None, "e": étages|None}. Grille de cases de CASE_M mètres."""

    def __init__(self, lots):
        self.lots = []
        self.grille = {}
        for lot in lots:
            self.ajouter(lot)

    def ajouter(self, lot):
        boites = [_boite(p) for p in lot["parts"]]
        lot = dict(lot)
        lot["boites"] = boites
        lot["aire"] = sum(aire_polygone(p) for p in lot["parts"])
        k = len(self.lots)
        self.lots.append(lot)
        for x0, y0, x1, y1 in boites:
            for cx in range(int(x0 // CASE_M), int(x1 // CASE_M) + 1):
                for cy in range(int(y0 // CASE_M), int(y1 // CASE_M) + 1):
                    self.grille.setdefault((cx, cy), set()).add(k)

    def candidats(self, boite):
        x0, y0, x1, y1 = boite
        ks = set()
        for cx in range(int(x0 // CASE_M), int(x1 // CASE_M) + 1):
            for cy in range(int(y0 // CASE_M), int(y1 // CASE_M) + 1):
                ks |= self.grille.get((cx, cy), set())
        return sorted(ks)


def taux_recouvrement(poly_osm, index):
    """-> liste de (indice du lot IGN, aire d'intersection estimée en m²) pour les lots qui touchent le bâtiment OSM."""
    x0, y0, x1, y1 = _boite(poly_osm)
    aire = aire_polygone(poly_osm)
    if aire <= 0:
        return []
    pas = min(PAS_MAX_M, max(PAS_MIN_M, math.sqrt(aire / POINTS_VISES)))
    cand = []
    for k in index.candidats((x0, y0, x1, y1)):
        lot = index.lots[k]
        parts = [(p, b) for p, b in zip(lot["parts"], lot["boites"]) if b[0] <= x1 and b[2] >= x0 and b[1] <= y1 and b[3] >= y0]
        if parts:
            cand.append((k, parts))
    if not cand:
        return []
    compte = {}
    nx, ny = int((x1 - x0) / pas) + 1, int((y1 - y0) / pas) + 1
    for i in range(nx):
        x = x0 + (i + 0.5) * pas
        for j in range(ny):
            y = y0 + (j + 0.5) * pas
            if not _dans(x, y, poly_osm):
                continue
            for k, parts in cand:
                if any(b[0] <= x <= b[2] and b[1] <= y <= b[3] and _dans(x, y, p) for p, b in parts):
                    compte[k] = compte.get(k, 0) + 1
                    break
    return [(k, n * pas * pas) for k, n in sorted(compte.items())]


def apparier(poly_osm, index):
    """-> (résultat, raison). résultat = {"hauteur", "etages", "couverture", "n"} ou None ; raison = None ou
    "sans_candidat" | "recouvrement_insuffisant" | "sans_hauteur" (sans hauteur NI étages exploitables)."""
    aire = aire_polygone(poly_osm)
    recs = taux_recouvrement(poly_osm, index)
    if not recs:
        return None, "sans_candidat"
    retenus = []
    for k, i in recs:
        lot = index.lots[k]
        if i / aire >= SEUIL_CANDIDAT or (lot["aire"] > 0 and i / lot["aire"] >= SEUIL_CANDIDAT):
            retenus.append((lot, i))
    if not retenus:
        return None, "recouvrement_insuffisant"
    cotes = [(lot, i) for lot, i in retenus if _plausible_h(lot["h"])]
    etages = [(lot, i) for lot, i in retenus if _plausible_e(lot["e"])]
    h = e = None
    if cotes and sum(i for _, i in cotes) / aire >= SEUIL_COUVERTURE:
        h = sum(i * lot["h"] for lot, i in cotes) / sum(i for _, i in cotes)
    if etages and sum(i for _, i in etages) / aire >= SEUIL_COUVERTURE:
        e = sum(i * lot["e"] for lot, i in etages) / sum(i for _, i in etages)
    if h is None and e is None:
        return None, "sans_hauteur"
    return {"hauteur": h, "etages": e, "couverture": sum(i for _, i in retenus) / aire, "n": len(retenus)}, None


def choisir_hauteur(h_osm, src_osm, res_ign, hauteur_defaut, src_defaut="defaut"):
    """-> (hauteur, h_src). h_osm/src_osm : résultat de decor.hauteur_m. res_ign : sortie de apparier (ou None)."""
    if src_osm == "osm":
        return h_osm, "osm"
    if res_ign and _plausible_h(res_ign.get("hauteur")):
        return res_ign["hauteur"], "ign"
    if src_osm == "etages":
        return h_osm, "etages"
    if res_ign and _plausible_e(res_ign.get("etages")):
        return res_ign["etages"] * METRES_PAR_ETAGE, "etages"
    return hauteur_defaut, src_defaut


def dedoublonner(lots):
    """Une même emprise peut sortir de deux départements voisins : on garde la première (clé = parties arrondies)."""
    vus, sortie = set(), []
    for lot in lots:
        cle = (lot["h"], lot["e"], tuple(tuple(tuple(map(tuple, a)) for a in p) for p in lot["parts"]))
        if cle not in vus:
            vus.add(cle)
            sortie.append(lot)
    return sortie


def croiser(batiments_osm, lots, progression=None):
    """batiments_osm : [{"p": [[lon, lat], ...], ...}] (décor). lots : sortie de extraire_bdtopo (h, e, parts en Lambert 93).
    -> liste de (résultat|None, raison|None), dans l'ordre des bâtiments OSM."""
    index = IndexIGN(lots)
    sortie = []
    for n, b in enumerate(batiments_osm):
        poly = [[lambert93(lon, lat) for lon, lat in b["p"]]]
        sortie.append(apparier(poly, index))
        if progression and n % 10000 == 0:
            progression(n)
    return sortie


def enrichir(batiments, lots):
    """Applique la priorité aux bâtiments du décor (modifie "h" et "h_src" en place).
    -> comptes : h_src après croisement, appariés, refus par raison, changements par origine précédente."""
    comptes = {"apparies": 0, "refus_sans_candidat": 0, "refus_recouvrement_insuffisant": 0, "refus_sans_hauteur": 0, "h_src": {}, "changes": {}}
    for b, (r, raison) in zip(batiments, croiser(batiments, lots)):
        if r:
            comptes["apparies"] += 1
        else:
            comptes["refus_" + raison] += 1
        avant = b["h_src"]
        h, src = choisir_hauteur(b["h"], avant, r, b["h"], avant)
        if (h, src) != (b["h"], avant):
            comptes["changes"][avant + "->" + src] = comptes["changes"].get(avant + "->" + src, 0) + 1
            b["h"], b["h_src"] = round(h, 1), src
        comptes["h_src"][b["h_src"]] = comptes["h_src"].get(b["h_src"], 0) + 1
    return comptes
