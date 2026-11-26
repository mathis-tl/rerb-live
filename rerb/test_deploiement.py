"""Déploiement Vercel (T4) : configuration statique, en-têtes, pages de développement, geo.json, page (cadence, pause, nuit).

Aucun réseau. Les règles JS (cadence, inactivité, message de nuit) sont exécutées avec node quand il est installé.
"""
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from serveur import EN_TETES, charger_geo

RACINE = Path(__file__).resolve().parent.parent
WEB = RACINE / "web"
PAGES_DEV = ["comparaison-modeles.html", "comparaison-modeles.js", "comparaison-zoom.html", "comparaison-zoom.js",
             "maquette-rendus.html", "tableau-rendu.html", "tableau-rendu.js"]


def _node(bloc, expression):
    """Exécute `bloc` (JS pur) avec node et renvoie le JSON de `expression`. Ignoré si node est absent."""
    if not shutil.which("node"):
        raise unittest.SkipTest("node absent")
    sortie = subprocess.run(["node", "-e", bloc + f"; console.log(JSON.stringify({expression}))"],
                            capture_output=True, text=True, timeout=30, check=True).stdout
    return json.loads(sortie)


def _bloc_chargement():
    """Section « chargement » de carte.js : constantes de cadence, inactivité, messages d'état (fonctions pures)."""
    js = (WEB / "carte.js").read_text(encoding="utf-8")
    return js.split("// --- chargement")[1].split("// --- fin chargement")[0]


class TestVercelJson(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((RACINE / "vercel.json").read_text(encoding="utf-8"))

    def test_site_statique_sans_build(self):
        self.assertEqual(self.config["outputDirectory"], "web")
        self.assertNotIn("buildCommand", self.config)

    def test_sortie_contient_index(self):
        self.assertTrue((WEB / "index.html").is_file())

    def test_routes_trains_et_gare(self):
        routes = {r["source"]: r["destination"] for r in self.config["rewrites"]}
        self.assertEqual(routes["/trains"], "/api/trains")
        self.assertEqual(routes["/gare"], "/api/gare")

    def test_en_tetes_de_securite_identiques_a_en_tetes(self):
        # une seule règle globale pour la sécurité ; Cache-Control est propre à chaque chemin (règles ci-dessous)
        globale = next(h for h in self.config["headers"] if h["source"] == "/(.*)")
        valeurs = {h["key"]: h["value"] for h in globale["headers"]}
        attendu = {cle: v for cle, v in EN_TETES.items() if cle != "Cache-Control"}
        self.assertEqual(valeurs, attendu)

    def _cache(self, chemin):
        for regle in self.config["headers"]:
            if regle["source"] == chemin:
                for h in regle["headers"]:
                    if h["key"] == "Cache-Control":
                        return h["value"]
        return None

    def test_cache_fichiers_de_la_page_sans_mise_en_cache(self):
        for chemin in ("/", "/index.html", "/carte.js", "/carte.css", "/modele-z8100.js"):
            self.assertEqual(self._cache(chemin), "no-cache", chemin)

    def test_donnees_revalidees_par_etag(self):
        self.assertEqual(self._cache("/data/(.*)"), "public, max-age=0, must-revalidate")
        self.assertEqual(self._cache("/fonts/(.*)"), "public, max-age=0, must-revalidate")

    def test_chaque_fichier_de_vendor_a_une_regle_de_cache(self):
        # un fichier de vendor sans nom versionné ne doit jamais être immuable : revalidé par ETag
        for f in (WEB / "vendor").iterdir():
            if f.name == "deck.gl-9.4.1.min.js":
                continue
            self.assertEqual(self._cache("/vendor/" + f.name), "public, max-age=0, must-revalidate", f.name)

    def test_seul_le_fichier_versionne_est_immuable(self):
        self.assertEqual(self._cache("/vendor/deck.gl-9.4.1.min.js"), "public, max-age=31536000, immutable")
        for regle in self.config["headers"]:
            if "immutable" in str(regle):
                self.assertEqual(regle["source"], "/vendor/deck.gl-9.4.1.min.js")


class TestDeploiementFichiers(unittest.TestCase):
    def test_pages_de_developpement_hors_du_site(self):
        for nom in PAGES_DEV:
            self.assertFalse((WEB / nom).exists(), f"{nom} ne doit plus être dans web/")
            self.assertTrue((RACINE / "outils" / "pages-dev" / nom).is_file(), f"{nom} doit être dans outils/pages-dev/")

    def test_geo_json_egal_a_la_route_geo(self):
        attendu = json.dumps(charger_geo(), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.assertEqual((WEB / "data" / "geo.json").read_bytes(), attendu)

    def test_aucune_reference_a_geo_ou_statut_dans_le_site(self):
        for f in list(WEB.glob("*.js")) + list(WEB.glob("*.html")) + list(WEB.glob("*.css")):
            texte = f.read_text(encoding="utf-8")
            self.assertIsNone(re.search(r"""["'`]/(geo|statut)\b""", texte), f"{f.name} appelle encore /geo ou /statut")

    def test_vercelignore_exclut_les_outils_et_les_secrets(self):
        lignes = {l.strip() for l in (RACINE / ".vercelignore").read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")}
        for motif in ("data/raw", "recorder", "outils", "docs", "rerb/exploration", "rerb/test_*.py", "**/__pycache__", ".env", ".env.*"):
            self.assertIn(motif, lignes)

    def test_api_sante_sans_secret_ni_reseau(self):
        texte = (RACINE / "api" / "sante.py").read_text(encoding="utf-8")
        for interdit in ("environ", "getenv", "urllib", "requests", "http.client", "socket"):
            self.assertNotIn(interdit, texte)
        self.assertIn("class handler(BaseHTTPRequestHandler)", texte)


class TestPageCadence(unittest.TestCase):
    def setUp(self):
        self.js = (WEB / "carte.js").read_text(encoding="utf-8")
        self.html = (WEB / "index.html").read_text(encoding="utf-8")

    def test_cadences_de_production(self):
        self.assertIn("const INTERVALLE_MS = 10000;", self.js)
        self.assertIn("const INTERVALLE_NUIT_MS = 60000;", self.js)
        self.assertIn("const INTERVALLE_GARE_MS = 30000;", self.js)
        self.assertIn("setInterval(actualiserGare, INTERVALLE_GARE_MS)", self.js)

    def test_pas_de_commentaire_trompeur_120_s_ni_3_s(self):
        self.assertNotIn("PRIM au plus toutes les 120 s", self.js)
        self.assertNotIn("le serveur rappelle PRIM si son relevé a plus de 120 s", self.js)
        self.assertNotIn("toutes les 2 minutes", self.html)

    def test_mentions_discretes_presentes(self):
        self.assertIn("Mise à jour en pause après 30 min d'inactivité. Toucher la carte pour reprendre.", self.js)
        self.assertIn("Service de nuit, reprise à 05:00", self.js)

    def test_inactivite_et_actions(self):
        for evenement in ("pointerdown", "pointermove", "wheel", "keydown", "touchstart"):
            self.assertIn(f'"{evenement}"', self.js)
        self.assertIn("faut_interroger(performance.now(), derniereActionMs, document.hidden, false, 0)", self.js)

    def test_faut_interroger_et_etat_donnees(self):
        bloc = _bloc_chargement()

        def faut(maintenant, action, cache=False, nuit=False, requete=-1e12):
            return _node(bloc, f"faut_interroger({maintenant}, {action}, {str(cache).lower()}, {str(nuit).lower()}, {requete})")

        self.assertTrue(faut(10000, 0))                       # jour, avant 30 min
        self.assertTrue(faut(1799999, 0))
        self.assertFalse(faut(1800000, 0))                    # 30 min sans action : pause
        self.assertFalse(faut(10000, 0, cache=True))          # onglet caché : rien
        self.assertTrue(faut(1800000, 1800000 - 5))           # une action remet le compteur à zéro
        self.assertFalse(faut(30000, 0, nuit=True, requete=0))   # nuit : 60 s entre deux requêtes
        self.assertTrue(faut(60000, 0, nuit=True, requete=0))
        self.assertTrue(faut(10000, 0, nuit=False, requete=0))  # jour : pas d'espacement supplémentaire

    def test_etat_donnees_nuit_et_pause(self):
        bloc = _bloc_chargement()
        nuit = _node(bloc, 'etatDonnees({enCours: true, ageS: 20, erreur: null, bloque: false, reveil: false, nuit: true, pause: false})')
        self.assertEqual(nuit["type"], "nuit")
        self.assertIn("Service de nuit, reprise à 05:00", nuit["message"])
        pause = _node(bloc, 'etatDonnees({enCours: true, ageS: 20, erreur: null, bloque: false, reveil: false, nuit: false, pause: true})')
        self.assertEqual(pause["type"], "pause")
        self.assertIsNone(_node(bloc, 'etatDonnees({enCours: true, ageS: 5, erreur: null, bloque: false, reveil: false, nuit: false, pause: false})'))


if __name__ == "__main__":
    unittest.main()
