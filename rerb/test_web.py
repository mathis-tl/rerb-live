"""Tâche 4, J4 : la page de la carte n'appelle rien hors du serveur local (garde-fou statique, sans navigateur).

Complète la politique CSP envoyée par le serveur (test_serveur.py) et la garde réseau de carte.js : si quelqu'un
ajoute un CDN, une tuile ou une police en ligne dans la page, ce test échoue. Le fichier deck.gl vendu
(web/vendor) est exclu : son contenu est audité dans web/vendor/VERSIONS.md.
"""
import re
import unittest
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "web"
FICHIERS = ("index.html", "carte.js", "carte.css")
URL_EXTERNE = re.compile(r"(https?:)?//[A-Za-z0-9.-]+\.[a-z]{2,}", re.I)


class TestPageLocale(unittest.TestCase):
    def test_fichiers_presents(self):
        for f in FICHIERS:
            self.assertTrue((WEB / f).is_file(), f)

    def test_aucune_adresse_externe(self):
        for f in FICHIERS:
            texte = (WEB / f).read_text(encoding="utf-8")
            # on retire les commentaires du code avant de chercher (ils peuvent citer des sites)
            texte = re.sub(r"/\*.*?\*/", "", texte, flags=re.S)
            texte = "\n".join(l for l in texte.splitlines() if not l.lstrip().startswith("//"))
            self.assertIsNone(URL_EXTERNE.search(texte), f"{f} : adresse externe trouvée")

    def test_ressources_de_index_toutes_locales_et_presentes(self):
        html = (WEB / "index.html").read_text(encoding="utf-8")
        liens = re.findall(r'(?:src|href)="([^"]+)"', html)
        self.assertTrue(liens)
        for lien in liens:
            self.assertFalse(lien.startswith(("http", "//")), lien)
            self.assertTrue((WEB / lien).is_file(), lien)

    def test_deck_gl_vendu_a_la_version_notee(self):
        self.assertTrue((WEB / "vendor" / "deck.gl-9.4.1.min.js").is_file())
        self.assertIn("08857e84bc2e5478a62ce1dc59b9b5fae9ae0a21104c78503dece316d69a1ca6",
                      (WEB / "vendor" / "VERSIONS.md").read_text(encoding="utf-8"))

    def test_deck_gl_vendu_non_modifie(self):
        import hashlib
        h = hashlib.sha256((WEB / "vendor" / "deck.gl-9.4.1.min.js").read_bytes()).hexdigest()
        self.assertEqual(h, "08857e84bc2e5478a62ce1dc59b9b5fae9ae0a21104c78503dece316d69a1ca6")

    def test_la_page_annonce_la_position_estimee(self):
        self.assertIn("Position estimée, pas GPS", (WEB / "index.html").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
