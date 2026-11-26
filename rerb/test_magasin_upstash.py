"""Magasin Upstash : formes des commandes, verrou, compteur, erreurs, et contrat commun aux trois magasins.

Aucun réseau : FauxUpstash répond à la place d'Upstash, l'horloge est simulée.
"""
import json
import unittest

from magasin import ErreurMagasin, MagasinFaux, MagasinLocal, compresser_json, decompresser_json
from magasin_upstash import SCRIPT_LIBERER, MagasinUpstash
from test_faux_upstash import JETON_FICTIF, FauxUpstash

URL_FICTIVE = "https://fictif-upstash.example.test"


class _Horloge:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def _magasin(panne=None, jeton_serveur=JETON_FICTIF):
    horloge = _Horloge()
    faux = FauxUpstash(horloge, jeton=jeton_serveur)
    faux.panne = panne
    return MagasinUpstash(URL_FICTIVE, JETON_FICTIF, http=faux.http), faux, horloge


class TestFormesDeCommandes(unittest.TestCase):
    def setUp(self):
        self.m, self.faux, self.h = _magasin()

    def test_verrou_pris_avec_set_nx_px(self):
        jeton = self.m.prendre_verrou("prim", 35)
        self.assertIsInstance(jeton, str)
        self.assertEqual(self.faux.commandes[-1], ["SET", "rerb:verrou:prim", jeton, "NX", "PX", 35000])

    def test_verrou_tenu_renvoie_none(self):
        self.m.prendre_verrou("prim", 35)
        self.assertIsNone(self.m.prendre_verrou("prim", 35))

    def test_verrou_repris_apres_expiration(self):
        self.m.prendre_verrou("prim", 35)
        self.h.t += 36
        self.assertIsNotNone(self.m.prendre_verrou("prim", 35))

    def test_liberation_seulement_par_le_detenteur(self):
        jeton = self.m.prendre_verrou("prim", 35)
        self.m.rendre_verrou("prim", "mauvais-jeton")
        self.assertIsNone(self.m.prendre_verrou("prim", 35))
        self.m.rendre_verrou("prim", jeton)
        self.assertIsNotNone(self.m.prendre_verrou("prim", 35))

    def test_liberation_par_eval_avec_le_script(self):
        jeton = self.m.prendre_verrou("prim", 35)
        self.m.rendre_verrou("prim", jeton)
        self.assertEqual(self.faux.commandes[-1], ["EVAL", SCRIPT_LIBERER, 1, "rerb:verrou:prim", jeton])

    def test_ancien_detenteur_ne_libere_pas_le_verrou_repris(self):
        ancien = self.m.prendre_verrou("prim", 35)
        self.h.t += 36
        nouveau = self.m.prendre_verrou("prim", 35)
        self.m.rendre_verrou("prim", ancien)
        self.assertIsNone(self.m.prendre_verrou("prim", 35))
        self.assertNotEqual(ancien, nouveau)

    def test_compteur_par_incr_puis_expiration_48_h_a_la_creation(self):
        """INCR (1 commande facturée) ; EXPIRE seulement quand le compteur vient d'être créé. Pas de script : facturé par
        commande interne, un EVAL coûterait plus cher que ces deux commandes."""
        self.assertEqual(self.m.incrementer_compteur("plafond:2026-11-24"), 1)
        self.assertEqual(self.faux.commandes[-2:], [["INCR", "rerb:plafond:2026-11-24"], ["EXPIRE", "rerb:plafond:2026-11-24", 172800]])
        self.assertEqual(self.m.incrementer_compteur("plafond:2026-11-24"), 2)
        self.assertEqual(self.faux.commandes[-1], ["INCR", "rerb:plafond:2026-11-24"])
        self.assertEqual(self.m.lire_compteur("plafond:2026-11-24"), 2)

    def test_compteur_absent_vaut_zero_par_get(self):
        self.assertEqual(self.m.lire_compteur("plafond:2026-11-24"), 0)
        self.assertEqual(self.faux.commandes[-1], ["GET", "rerb:plafond:2026-11-24"])

    def test_compteur_expire_apres_48_h(self):
        self.m.incrementer_compteur("plafond:x")
        self.h.t += 172801
        self.assertEqual(self.m.lire_compteur("plafond:x"), 0)

    def test_valeur_json_aller_retour(self):
        self.assertIsNone(self.m.lire("releve"))
        self.m.ecrire("derniere_erreur", {"statut": 429, "x": [1, 2]})
        commande = self.faux.commandes[-1]
        self.assertEqual(commande[:2], ["SET", "rerb:derniere_erreur"])
        self.assertEqual(json.loads(commande[2]), {"statut": 429, "x": [1, 2]})
        self.assertEqual(self.m.lire("derniere_erreur"), {"statut": 429, "x": [1, 2]})

    def test_ecriture_avec_ttl_envoie_ex(self):
        self.m.ecrire("blocage", {"statut": 429}, ttl_s=900)
        self.assertEqual(self.faux.commandes[-1][3:], ["EX", 900])
        self.h.t += 901
        self.assertIsNone(self.m.lire("blocage"))

    def test_lecture_groupee_par_mget_et_ecriture_groupee_par_mset(self):
        self.m.ecrire("a", 1)
        self.assertEqual(self.m.lire_plusieurs(["a", "b"]), [1, None])
        self.assertEqual(self.faux.commandes[-1], ["MGET", "rerb:a", "rerb:b"])
        self.m.ecrire_groupe({"a": 2, "b": "x"})
        self.assertEqual(self.faux.commandes[-1], ["MSET", "rerb:a", "2", "rerb:b", '"x"'])
        self.assertEqual(self.m.lire_plusieurs(["b", "a"]), ["x", 2])


class TestErreurs(unittest.TestCase):
    def _echec(self, panne, appel=lambda m: m.lire("releve")):
        m, _faux, _h = _magasin(panne)
        with self.assertRaises(ErreurMagasin) as cm:
            appel(m)
        texte = str(cm.exception)
        self.assertNotIn(JETON_FICTIF, texte)
        self.assertNotIn("example.test", texte)
        self.assertNotIn("https", texte)
        return texte

    def test_delai_http500_json_et_forme_sont_des_erreurs_magasin(self):
        for panne in ("delai", "http500", "json", "forme"):
            with self.subTest(panne=panne):
                self._echec(panne)

    def test_jeton_refuse_hors_secret_dans_le_message(self):
        m, _faux, _h = _magasin(jeton_serveur="autre-jeton")
        with self.assertRaises(ErreurMagasin) as cm:
            m.prendre_verrou("prim", 35)
        self.assertNotIn(JETON_FICTIF, str(cm.exception))
        self.assertNotIn("example.test", str(cm.exception))

    def test_toute_commande_en_echec_leve_erreur_magasin(self):
        appels = (lambda m: m.ecrire("x", 1), lambda m: m.incrementer_compteur("c"),
                  lambda m: m.prendre_verrou("prim", 35), lambda m: m.rendre_verrou("prim", "j"),
                  lambda m: m.lire_compteur("c"), lambda m: m.lire_plusieurs(["x"]),
                  lambda m: m.ecrire_groupe({"x": 1}))
        for appel in appels:
            with self.subTest(appel=appel):
                self._echec("delai", appel)
                self._echec("http500", appel)


class _ContratMagasin:
    """Ce que tout magasin doit faire. Les sous-classes fournissent creer() -> (magasin, avancer(secondes))."""

    def creer(self):
        raise NotImplementedError

    def setUp(self):
        self.m, self.avancer = self.creer()

    def test_verrou_libre_puis_tenu(self):
        self.assertIsInstance(self.m.prendre_verrou("prim", 35), str)
        self.assertIsNone(self.m.prendre_verrou("prim", 35))

    def test_liberation_expiration_et_ancien_jeton(self):
        ancien = self.m.prendre_verrou("prim", 35)
        self.m.rendre_verrou("prim", "faux")
        self.assertIsNone(self.m.prendre_verrou("prim", 35))
        self.avancer(36)
        nouveau = self.m.prendre_verrou("prim", 35)
        self.assertIsNotNone(nouveau)
        self.m.rendre_verrou("prim", ancien)
        self.assertIsNone(self.m.prendre_verrou("prim", 35))

    def test_compteur(self):
        self.assertEqual(self.m.lire_compteur("plafond:j"), 0)
        self.assertEqual(self.m.incrementer_compteur("plafond:j"), 1)
        self.assertEqual(self.m.incrementer_compteur("plafond:j"), 2)
        self.assertEqual(self.m.lire_compteur("plafond:j"), 2)

    def test_valeur_json_et_ttl(self):
        self.assertIsNone(self.m.lire("blocage"))
        self.m.ecrire("blocage", {"statut": 429}, ttl_s=900)
        self.assertEqual(self.m.lire("blocage"), {"statut": 429})
        self.avancer(901)
        self.assertIsNone(self.m.lire("blocage"))

    def test_valeur_sans_ttl_persiste(self):
        self.m.ecrire("releve", [1, "a"])
        self.avancer(10 ** 6)
        self.assertEqual(self.m.lire("releve"), [1, "a"])

    def test_aller_retour_compresse(self):
        octets = b"x" * 5000 + "é".encode("utf-8")
        self.m.ecrire_compresse("releve", octets, ttl_s=None)
        self.assertEqual(self.m.lire_compresse("releve"), octets)
        self.assertIsNone(self.m.lire_compresse("absent"))

    def test_lecture_groupee_et_ecriture_groupee(self):
        self.assertEqual(self.m.lire_plusieurs(["a", "b"]), [None, None])
        self.m.ecrire_groupe({"a": {"x": 1}, "b": None})
        self.assertEqual(self.m.lire_plusieurs(["b", "a", "c"]), [None, {"x": 1}, None])

    def test_json_compresse_aller_retour(self):
        valeur = {"trains": [1, "é", None], "v": 1}
        self.m.ecrire("releve", compresser_json(valeur))
        self.assertEqual(decompresser_json(self.m.lire("releve")), valeur)
        with self.assertRaises(ErreurMagasin):
            decompresser_json("pas du base64 !!")


class TestContratLocal(_ContratMagasin, unittest.TestCase):
    def creer(self):
        t = [0.0]

        def avancer(secondes):
            t[0] += secondes

        return MagasinLocal(horloge=lambda: t[0]), avancer


class TestContratFaux(_ContratMagasin, unittest.TestCase):
    def creer(self):
        m = MagasinFaux()
        return m, m.avancer


class TestContratUpstash(_ContratMagasin, unittest.TestCase):
    def creer(self):
        m, _faux, horloge = _magasin()

        def avancer(secondes):
            horloge.t += secondes

        return m, avancer


if __name__ == "__main__":
    unittest.main()
