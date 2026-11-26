"""Le magasin (rerb/magasin.py). Aucun réseau, horloge simulée."""
import tempfile
import unittest
from pathlib import Path

from magasin import ErreurMagasin, MagasinFaux, MagasinLocal


class TestVerrou(unittest.TestCase):
    def test_verrou_pris_refuse_puis_repris_apres_expiration(self):
        m = MagasinFaux()
        self.assertIsNotNone(m.prendre_verrou("prim", 35))
        self.assertIsNone(m.prendre_verrou("prim", 35))
        m.avancer(35)
        self.assertIsNotNone(m.prendre_verrou("prim", 35))

    def test_rendre_libere_le_verrou(self):
        m = MagasinFaux()
        jeton = m.prendre_verrou("prim", 35)
        m.rendre_verrou("prim", jeton)
        self.assertIsNotNone(m.prendre_verrou("prim", 35))

    def test_rendre_un_jeton_expire_est_sans_effet(self):
        m = MagasinFaux()
        ancien = m.prendre_verrou("prim", 35)
        m.avancer(40)
        nouveau = m.prendre_verrou("prim", 35)            # repris par un autre appel
        m.rendre_verrou("prim", ancien)                   # l'ancien détenteur ne doit pas libérer le verrou du nouveau
        self.assertIsNone(m.prendre_verrou("prim", 35))
        self.assertIsNotNone(nouveau)

    def test_verrous_independants_par_nom(self):
        m = MagasinFaux()
        self.assertIsNotNone(m.prendre_verrou("prim", 35))
        self.assertIsNotNone(m.prendre_verrou("gares", 35))


class TestPanne(unittest.TestCase):
    def test_chaque_methode_leve_erreur_magasin(self):
        m = MagasinFaux()
        m.panne = True
        appels = (
            lambda: m.prendre_verrou("prim", 35),
            lambda: m.rendre_verrou("prim", "x"),
            lambda: m.lire_compteur("c"),
            lambda: m.incrementer_compteur("c"),
            lambda: m.lire("k"),
            lambda: m.ecrire("k", 1),
        )
        for appel in appels:
            with self.assertRaises(ErreurMagasin):
                appel()

    def test_fixer_un_compteur_plein(self):
        m = MagasinFaux()
        m.fixer("plafond:2026-11-16", 1300)
        self.assertEqual(m.lire_compteur("plafond:2026-11-16"), 1300)


class TestMagasinLocal(unittest.TestCase):
    def setUp(self):
        self.dossier = tempfile.TemporaryDirectory()
        self.addCleanup(self.dossier.cleanup)
        self.fichier = Path(self.dossier.name) / "p.json"

    def test_compteurs_persistes_blocage_non(self):
        # INVARIANT (magasin.py) : seuls les compteurs vont sur disque ; un blocage ne survit pas à un redémarrage
        m = MagasinLocal(self.fichier)
        m.incrementer_compteur("plafond:2026-11-16")
        m.incrementer_compteur("plafond:2026-11-16")
        m.ecrire("blocage", {"statut": 401})
        redemarre = MagasinLocal(self.fichier)
        self.assertEqual(redemarre.lire_compteur("plafond:2026-11-16"), 2)
        self.assertIsNone(redemarre.lire("blocage"))

    def test_fichier_illisible_compteurs_a_zero(self):
        self.fichier.write_text("pas du json")
        self.assertEqual(MagasinLocal(self.fichier).lire_compteur("plafond:2026-11-16"), 0)

    def test_sans_fichier_rien_n_est_ecrit(self):
        m = MagasinLocal()
        self.assertEqual(m.incrementer_compteur("c"), 1)
        self.assertFalse(self.fichier.exists())

    def test_ancien_format_converti(self):
        self.fichier.write_text('{"jour": "2026-11-16", "n": 7}')
        self.assertEqual(MagasinLocal(self.fichier).lire_compteur("plafond:2026-11-16"), 7)


if __name__ == "__main__":
    unittest.main()
