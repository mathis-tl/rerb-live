"""Tâche 5 : relayeur Upstash (une ou deux copies du serveur Vercel), blocage avec durée, purge de la mémoire, garde de taille,
coût en commandes et budget simulé.

AUCUN réseau : FauxUpstash (magasin), FauxPrim (PRIM) et horloge simulée. Données inventées ; les heures sont en UTC.
"""
import functools
import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from magasin import ErreurMagasin, MagasinFaux, compresser_json
from magasin_upstash import MagasinUpstash
from memoire import PURGE_S, TAILLE_MAX_OCTETS, Memoire
from relayeur import Relayeur, prochaine_bascule_nuit
from serveur import (CLE_BLOCAGE, INTERVALLE_S, PLAFOND_JOUR, ErreurPrim, Plafond, Releve, Releveur, calculer_trains,
                     consolider_livraison, est_nuit)
from test_faux_upstash import JETON_FICTIF, FauxUpstash
from test_serveur import A, B, T0, appel, course, distance, livraison, localiser, nom

DONNEES_PALAISEAU = Path.home() / "rerb-data-palaiseau-20261118"
MESURE_UNE_COPIE_VINGT_HEURES = 8292   # commandes Upstash, une copie, 20 h, une requête toutes les 5 s (mesure du 25/11, exploration/budget_upstash.py)
LENT_OK = os.environ.get("RERB_LENT") == "1"   # suite budget : ≈14 min, hors check.sh (lancer avec RERB_LENT=1)
CIBLE_COMMANDES_MOIS = 250_000         # 50 % de 500 000 (offre gratuite), pour 50 visiteurs et 3 copies, 20 h par jour (ticket T5b)
CIBLE_OCTETS_MOIS = 6 * 10**9          # 60 % de 10 Go : même scénario

URL_FICTIVE = "https://fictif-upstash.example.test"
PARIS_NUIT = datetime(2026, 11, 17, 1, 0, tzinfo=timezone.utc)   # 02:00 à Paris : coupure de nuit
PARIS_JOUR = datetime(2026, 11, 16, 10, 0, tzinfo=timezone.utc)  # 11:00 à Paris (T0)


class FauxPrim:
    """PRIM simulé : chaque appel est noté (instant), renvoie un train fabriqué à cet instant, ou lève `erreur`.
    `pendant` : fonction exécutée pendant l'appel (simule une autre copie qui demande en même temps)."""

    def __init__(self, horloge):
        self.horloge = horloge
        self.appels = []
        self.erreur = None
        self.pendant = None

    def __call__(self):
        maintenant = self.horloge()
        self.appels.append(maintenant)
        if self.pendant is not None:
            self.pendant()
        if self.erreur is not None:
            raise self.erreur
        enregistre = int((maintenant - T0).total_seconds())
        return livraison(course("T1", [appel(A, 600, 660), appel(B, 900, 960)], enregistre=enregistre))


class Monde:
    """Un magasin Upstash simulé, une PRIM simulée, une horloge partagée (secondes depuis T0 dans `t`)."""

    def __init__(self):
        self.t = 0.0
        self.faux = FauxUpstash(lambda: self.t)
        self.magasin = MagasinUpstash(URL_FICTIVE, JETON_FICTIF, http=self.faux.http)
        self.prim = FauxPrim(self.maintenant)

    def maintenant(self):
        return T0 + timedelta(seconds=self.t)

    def copie(self, **kw):
        return Relayeur(self.magasin, self.prim, **kw)

    def commandes(self):
        return [c[0] for c in self.faux.commandes]


class TestRelevéAllerRetour(unittest.TestCase):
    def test_exporter_puis_importer_est_identique(self):
        releve = Releve(T0, consolider_livraison(livraison(course("T1", [appel(A, 600, 660), appel(B, 900, 960)])), Memoire()))
        d = json.loads(json.dumps(releve.exporter()))
        self.assertEqual(d["v"], 1)
        self.assertEqual(Releve.importer(d), releve)

    def test_format_inconnu_refuse(self):
        with self.assertRaises(ValueError):
            Releve.importer({"v": 99, "recu": T0.isoformat(), "trains": {}})


class TestPurgeMemoire(unittest.TestCase):
    def test_course_non_vue_depuis_4_h_est_oubliee(self):
        memoire = Memoire()
        consolider_livraison(livraison(course("VIEILLE", [appel(A, 0, 60)], enregistre=0)), memoire)
        consolider_livraison(livraison(course("RECENTE", [appel(A, 0, 60)], enregistre=PURGE_S - 100)), memoire)
        memoire.purger(T0 + timedelta(seconds=PURGE_S + 1))
        d = memoire.exporter()
        self.assertNotIn("VIEILLE", d["trains"])
        self.assertIn("RECENTE", d["trains"])
        self.assertIn("vus", d)

    def test_import_sans_vus_reste_accepte(self):
        memoire = Memoire()
        consolider_livraison(livraison(course("T1", [appel(A, 0, 60)])), memoire)
        d = memoire.exporter()
        del d["vus"]
        Memoire().importer(d)   # ne lève pas

    def test_taille_mesurable_et_seuil_de_2_mo(self):
        self.assertEqual(TAILLE_MAX_OCTETS, 2_000_000)
        memoire = Memoire()
        consolider_livraison(livraison(course("T1", [appel(A, 0, 60)])), memoire)
        self.assertEqual(memoire.taille_export(), len(json.dumps(memoire.exporter(), separators=(",", ":")).encode("utf-8")))


def _a(m, instant):
    """Place la Monde à `instant` (datetime aware, UTC)."""
    m.t = (instant - T0).total_seconds()


class TestCoutParRafraichissement(unittest.TestCase):
    def test_premier_rafraichissement_suit_la_suite_de_commandes_prevue(self):
        m = Monde()
        m.copie().lire(m.maintenant())
        self.assertEqual(m.commandes(), ["MGET", "SET", "MGET", "INCR", "EXPIRE", "MSET", "EVAL"])
        self.assertEqual(len(m.prim.appels), 1)

    def test_rafraichissement_suivant_du_meme_jour_sans_expire(self):
        m = Monde()
        r = m.copie()
        r.lire(m.maintenant())
        avant = len(m.faux.commandes)
        m.t += INTERVALLE_S
        r.lire(m.maintenant())
        self.assertEqual(m.commandes()[avant:], ["MGET", "SET", "MGET", "INCR", "MSET", "EVAL"])
        self.assertEqual(len(m.prim.appels), 2)

    def test_releve_frais_a_59_s_ne_coute_aucune_commande(self):
        m = Monde()
        r = m.copie()
        r.lire(m.maintenant())
        avant = len(m.faux.commandes)
        m.t += INTERVALLE_S - 1
        r.lire(m.maintenant())
        self.assertEqual(len(m.faux.commandes), avant)

    def test_autre_copie_pendant_un_releve_frais_lit_sans_memoire(self):
        m = Monde()
        m.copie().lire(m.maintenant())
        m.t += INTERVALLE_S - 1
        avant = len(m.faux.commandes)
        m.copie().lire(m.maintenant())
        self.assertEqual(m.commandes()[avant:], ["MGET"])
        self.assertNotIn("rerb:memoire", m.faux.commandes[-1])
        self.assertEqual(len(m.prim.appels), 1)

    def test_memoire_lue_seulement_sous_verrou(self):
        m = Monde()
        a, b = m.copie(), m.copie()
        for s in range(0, 300, 5):
            m.t = float(s)
            a.lire(m.maintenant())
            b.lire(m.maintenant())
        avec_memoire = [c for c in m.faux.commandes if c[0] == "MGET" and "rerb:memoire" in c]
        self.assertGreater(len(m.prim.appels), 1)
        self.assertEqual(len(avec_memoire), len(m.prim.appels))

    def test_memoire_non_reecrite_si_inchangee(self):
        m = Monde()
        r = Relayeur(m.magasin, lambda: livraison())   # livraison vide : la mémoire ne change pas
        r.lire(m.maintenant())
        m.t += INTERVALLE_S
        r.lire(m.maintenant())
        msets = [c for c in m.faux.commandes if c[0] == "MSET"]
        self.assertEqual(len(msets), 2)
        for c in msets:
            self.assertNotIn("rerb:memoire", c[1::2])

    def test_memoire_reecrite_quand_elle_change(self):
        m = Monde()
        m.copie().lire(m.maintenant())
        msets = [c for c in m.faux.commandes if c[0] == "MSET"]
        self.assertIn("rerb:memoire", msets[-1][1::2])


class TestBasculeDeNuit(unittest.TestCase):
    def test_prochaine_bascule_en_heure_de_paris(self):
        fenetre = (1, 5)
        self.assertEqual(prochaine_bascule_nuit(datetime(2026, 11, 16, 10, 0, tzinfo=timezone.utc), fenetre),
                         datetime(2026, 11, 17, 0, 0, tzinfo=timezone.utc))
        self.assertEqual(prochaine_bascule_nuit(datetime(2026, 11, 17, 0, 0, tzinfo=timezone.utc), fenetre),
                         datetime(2026, 11, 17, 4, 0, tzinfo=timezone.utc))
        self.assertEqual(prochaine_bascule_nuit(datetime(2026, 11, 17, 4, 0, tzinfo=timezone.utc), fenetre),
                         datetime(2026, 11, 18, 0, 0, tzinfo=timezone.utc))
        self.assertIsNone(prochaine_bascule_nuit(T0, None))

    def test_cache_s_arrete_a_minuit_utc_et_la_nuit_ne_lit_qu_une_fois(self):
        m = Monde()
        r = m.copie()
        _a(m, datetime(2026, 11, 16, 23, 59, 50, tzinfo=timezone.utc))
        r.lire(m.maintenant())
        avant = len(m.faux.commandes)
        _a(m, datetime(2026, 11, 17, 0, 0, 0, tzinfo=timezone.utc))   # 01:00 à Paris : début de la coupure
        vue = r.lire(m.maintenant())
        self.assertEqual(vue.service, "nuit")
        self.assertEqual(m.commandes()[avant:], ["MGET"])
        for _ in range(4 * 3600 // 5 - 1):   # de 00:00:05 à 03:59:55 UTC : le cache tient jusqu'à 04:00 UTC
            m.t += 5
            r.lire(m.maintenant())
        self.assertEqual(m.commandes()[avant:], ["MGET"])
        self.assertEqual(len(m.prim.appels), 1)

    def test_fin_de_coupure_relance_un_rafraichissement(self):
        m = Monde()
        r = m.copie()
        _a(m, datetime(2026, 11, 16, 23, 59, 50, tzinfo=timezone.utc))
        r.lire(m.maintenant())
        _a(m, datetime(2026, 11, 17, 4, 0, 0, tzinfo=timezone.utc))   # 05:00 à Paris : fin de la coupure
        vue = r.lire(m.maintenant())
        self.assertIsNone(vue.service)
        self.assertEqual(len(m.prim.appels), 2)


class TestDeuxCopies(unittest.TestCase):
    def test_un_appel_prim_par_minute_avec_deux_copies(self):
        m = Monde()
        a, b = m.copie(), m.copie()
        for s in range(0, 600, 3):   # dix minutes, une requête toutes les 3 s, alternativement sur les deux copies
            m.t = float(s)
            (a if (s // 3) % 2 == 0 else b).lire(m.maintenant())
        self.assertEqual(len(m.prim.appels), 10)
        ecarts = [(y - x).total_seconds() for x, y in zip(m.prim.appels, m.prim.appels[1:])]
        self.assertTrue(all(e >= 60 for e in ecarts), ecarts)

    def test_seule_la_copie_qui_rafraichit_ecrit_l_etat(self):
        m = Monde()
        a, b = m.copie(), m.copie()
        for s in range(0, 600, 3):
            m.t = float(s)
            (a if (s // 3) % 2 == 0 else b).lire(m.maintenant())
        self.assertEqual(m.commandes().count("MSET"), len(m.prim.appels))

    def test_pendant_un_appel_l_autre_copie_ne_rappelle_pas_prim(self):
        m = Monde()
        a, b = m.copie(), m.copie()
        vues = []
        m.prim.pendant = lambda: vues.append(b.lire(m.maintenant()))
        a.lire(m.maintenant())
        m.prim.pendant = None
        self.assertEqual(len(m.prim.appels), 1)
        self.assertIsNone(vues[0].releve)

    def test_lissage_identique_sur_les_deux_copies(self):
        m = Monde()
        a, b = m.copie(), m.copie()
        for s in range(0, 600, 3):
            m.t = float(s)
            (a if (s // 3) % 2 == 0 else b).lire(m.maintenant())
        m.t = 700.0
        maintenant = m.maintenant()
        va, vb = a.lire(maintenant), b.lire(maintenant)
        self.assertEqual(va.lisseur.exporter(), vb.lisseur.exporter())
        self.assertEqual(calculer_trains(va.releve, maintenant, distance, localiser, nom, lisseur=va.lisseur),
                         calculer_trains(vb.releve, maintenant, distance, localiser, nom, lisseur=vb.lisseur))


class TestNuitPanneEtPlafond(unittest.TestCase):
    def test_nuit_aucun_appel_prim_et_aucune_ecriture(self):
        m = Monde()
        m.t = (PARIS_NUIT - T0).total_seconds()
        vue = m.copie().lire(m.maintenant())
        self.assertEqual(vue.service, "nuit")
        self.assertEqual(m.prim.appels, [])
        self.assertEqual(set(m.commandes()), {"MGET"})

    def test_coupure_de_nuit_selon_paris_et_l_heure_d_ete(self):
        self.assertTrue(est_nuit(datetime(2026, 7, 1, 23, 30, tzinfo=timezone.utc), (1, 5)))   # 01:30 à Paris, heure d'été
        self.assertFalse(est_nuit(datetime(2026, 7, 1, 22, 30, tzinfo=timezone.utc), (1, 5)))  # 00:30 à Paris
        self.assertFalse(est_nuit(PARIS_JOUR, None))

    def test_panne_du_magasin_sert_le_dernier_releve_sans_appel(self):
        m = Monde()
        r = m.copie()
        r.lire(m.maintenant())
        m.faux.panne = "http500"
        m.t = 120.0
        vue = r.lire(m.maintenant())
        self.assertEqual(len(m.prim.appels), 1)
        self.assertIsNotNone(vue.releve)
        self.assertIn("magasin", vue.erreur)

    def test_panne_sans_cache_local_ne_donne_aucun_train_et_aucun_appel(self):
        m = Monde()
        m.faux.panne = "delai"
        vue = m.copie().lire(m.maintenant())
        self.assertEqual(m.prim.appels, [])
        self.assertIsNone(vue.releve)
        self.assertIn("magasin", vue.erreur)

    def test_plafond_atteint_aucun_appel(self):
        m = Monde()
        cle = Plafond(m.magasin, PLAFOND_JOUR).cle(m.maintenant())
        m.faux.valeurs["rerb:" + cle] = (str(PLAFOND_JOUR), None)
        vue = m.copie().lire(m.maintenant())
        self.assertEqual(m.prim.appels, [])
        self.assertIsNone(vue.releve)
        self.assertIn("plafond", vue.erreur)

    def test_memoire_trop_grosse_ne_s_ecrit_pas_et_garde_l_ancienne_version(self):
        m = Monde()
        trains = {f"C{i}": [{"gare": A, "arrivee": T0.isoformat(), "depart": T0.isoformat(), "annule": False, "reconstruit": False,
                             "repare": False, "retenu_depuis": None, "retenu_le": None}] * 12 for i in range(1000)}
        grosse = {"v": 1, "trains": trains, "vus": {cle: T0.isoformat() for cle in trains}}
        m.faux.valeurs["rerb:memoire"] = (json.dumps(compresser_json(grosse)), None)   # format écrit par MagasinUpstash
        ancienne = m.faux.valeurs["rerb:memoire"]
        vue = m.copie().lire(m.maintenant())
        self.assertIn("mémoire", vue.erreur)
        self.assertEqual(m.faux.valeurs["rerb:memoire"], ancienne)
        self.assertNotIn("rerb:releve", m.faux.valeurs)


class TestBlocage(unittest.TestCase):
    def _bloque(self, statut, ttl):
        m = Monde()
        m.prim.erreur = ErreurPrim(f"HTTP {statut}", statut)
        r = m.copie()
        r.lire(m.maintenant())
        ecritures = [c for c in m.faux.commandes if c[:2] == ["SET", "rerb:blocage"]]
        self.assertEqual(len(ecritures), 1)
        self.assertEqual(ecritures[0][3:], ["EX", ttl])
        self.assertEqual(json.loads(ecritures[0][2])["statut"], statut)
        return m, r

    def test_429_bloque_15_min_puis_un_nouvel_essai(self):
        m, r = self._bloque(429, 900)
        m.prim.erreur = None
        m.t = 899.0
        self.assertEqual(len(m.prim.appels), 1)
        r.lire(m.maintenant())
        self.assertEqual(len(m.prim.appels), 1, "bloqué pendant 15 min : aucun appel")
        m.t = 901.0
        r.lire(m.maintenant())
        self.assertEqual(len(m.prim.appels), 2, "après 15 min : un nouvel essai")

    def test_401_et_403_bloquent_6_h(self):
        for statut in (401, 403):
            with self.subTest(statut=statut):
                m, r = self._bloque(statut, 21600)
                m.prim.erreur = None
                m.t = 21599.0
                r.lire(m.maintenant())
                self.assertEqual(len(m.prim.appels), 1)
                m.t = 21601.0
                r.lire(m.maintenant())
                self.assertEqual(len(m.prim.appels), 2)

    def test_erreur_5xx_ne_bloque_pas(self):
        m = Monde()
        m.prim.erreur = ErreurPrim("HTTP 503", 503)
        r = m.copie()
        r.lire(m.maintenant())
        self.assertNotIn("rerb:blocage", m.faux.valeurs)
        m.prim.erreur = None
        m.t = 61.0
        r.lire(m.maintenant())
        self.assertEqual(len(m.prim.appels), 2)


class TestBlocageLocal(unittest.TestCase):
    """Releveur (serveur local) : même durée de blocage, lue depuis le magasin."""

    def test_429_bloque_15_min_dans_le_serveur_local(self):
        m = MagasinFaux()
        horloge = lambda: T0 + timedelta(seconds=m.t)   # noqa: E731
        appels = []

        def fetch():
            appels.append(horloge())
            if len(appels) == 1:
                raise ErreurPrim("HTTP 429", 429)
            return livraison(course("T1", [appel(A, 600, 660)]))

        releveur = Releveur(fetch, Plafond(m, PLAFOND_JOUR, horloge=horloge), intervalle_s=60)
        releveur.actualiser(horloge())
        self.assertEqual(m.lire(CLE_BLOCAGE), {"statut": 429})
        m.t = 899.0
        self.assertEqual(releveur.actualiser(horloge()), "bloqué")
        m.t = 901.0
        self.assertEqual(releveur.actualiser(horloge()), "ok")


@functools.lru_cache(maxsize=None)
def _simulation(copies, heures):
    """Une simulation par (copies, heures) : rejeu coûteux, mis en cache pour les tests."""
    sys.path.insert(0, str(Path(__file__).resolve().parent / "exploration"))
    import budget_upstash
    return budget_upstash.simuler(copies, heures)


@unittest.skipUnless(LENT_OK and (DONNEES_PALAISEAU / "prim_estimated_timetable").is_dir(), "suite lente : RERB_LENT=1 et données de Palaiseau requises")
class TestBudget(unittest.TestCase):
    """Budget Upstash (exploration/budget_upstash.py), relevés réels de Palaiseau rejoués, une requête toutes les 5 s pendant la
    fenêtre active, copies en tour de rôle. Critère T5b, décidé le 25/11 : base de 10 visiteurs × 30 min (5 h bout à bout), 3 copies,
    au plus 250 000 commandes et 6 Go par mois. Le test de mesure fige la valeur d'une copie sur 20 h."""

    def test_mesure_une_copie_sur_vingt_heures(self):
        self.assertEqual(_simulation(1, 20)["commandes"], MESURE_UNE_COPIE_VINGT_HEURES)

    def test_cible_commandes_par_mois_base_trois_copies(self):
        self.assertLessEqual(_simulation(3, 5)["commandes"] * 30, CIBLE_COMMANDES_MOIS)

    def test_cible_transfert_par_mois_base_trois_copies(self):
        self.assertLessEqual(_simulation(3, 5)["octets"] * 30, CIBLE_OCTETS_MOIS)


class TestEcritureEnPanne(unittest.TestCase):
    def test_echec_d_ecriture_apres_appel_garde_le_releve_localement(self):
        m = Monde()
        r = m.copie()

        def panne(valeurs):
            raise ErreurMagasin("écriture en panne")
        m.magasin.ecrire_groupe = panne
        vue = r.lire(m.maintenant())
        self.assertIsNotNone(vue.releve)
        self.assertIn("non partagé", vue.erreur)
        r._cache = None   # cache local expiré ; relevé local encore frais (59 s), magasin toujours vide
        m.t = INTERVALLE_S - 1.0
        vue = r.lire(m.maintenant())
        self.assertEqual(len(m.prim.appels), 1, "relevé local frais : pas de nouvel appel PRIM")
        self.assertIsNotNone(vue.releve)

    def test_ecriture_retablie_partage_le_releve_au_rafraichissement_suivant(self):
        m = Monde()
        r = m.copie()

        def panne(valeurs):
            raise ErreurMagasin("écriture en panne")
        m.magasin.ecrire_groupe = panne
        r.lire(m.maintenant())
        del m.magasin.ecrire_groupe   # l'écriture revient
        m.t = 61.0
        vue = r.lire(m.maintenant())
        self.assertEqual(len(m.prim.appels), 2)
        self.assertIsNone(vue.erreur)
        self.assertIn("rerb:releve", m.faux.valeurs)


def _decaler(x, suffixe, delta):
    """Copie d'une livraison PRIM : identifiants de course suffixés, horodatages (clés se terminant par « Time ») décalés."""
    if isinstance(x, list):
        return [_decaler(v, suffixe, delta) for v in x]
    if not isinstance(x, dict):
        return x
    sortie = {}
    for cle, v in x.items():
        if cle == "DatedVehicleJourneyRef" and isinstance(v, dict) and isinstance(v.get("value"), str):
            v = {**v, "value": v["value"] + suffixe}   # forme réelle PRIM : {"value": ...}
        elif cle == "DatedVehicleJourneyRef" and isinstance(v, str):
            v = v + suffixe
        elif isinstance(v, str) and cle.endswith("Time"):
            try:
                v = (datetime.fromisoformat(v.replace("Z", "+00:00")) + delta).isoformat()
            except ValueError:
                pass
        else:
            v = _decaler(v, suffixe, delta)
        sortie[cle] = v
    return sortie


@unittest.skipUnless((DONNEES_PALAISEAU / "prim_estimated_timetable").is_dir(), "données de Palaiseau absentes")
class TestVingtHeuresDeRejeu(unittest.TestCase):
    """Relevés réels de Palaiseau (environ 2 h, 59 relevés) répétés dix fois, soit environ 20 h, identifiants de course et
    horodatages décalés à chaque répétition : la purge de 4 h et la garde de 2 Mo doivent tenir."""

    CYCLES = 10

    def test_purge_et_taille_sur_vingt_heures(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent / "exploration"))
        import rejouer_palaiseau
        releves = rejouer_palaiseau.lire_releves(DONNEES_PALAISEAU)
        duree = releves[-1][0] - releves[0][0] + timedelta(minutes=1)
        memoire, pire = Memoire(), 0
        for c in range(self.CYCLES):
            for instant, sd in releves:
                consolider_livraison(_decaler(sd, f"#{c}", duree * c), memoire)
                memoire.purger(instant + duree * c)
                pire = max(pire, memoire.taille_export())
        vus = memoire.exporter()["vus"]
        self.assertLess(pire, TAILLE_MAX_OCTETS, pire)
        self.assertTrue(any(k.endswith(f"#{self.CYCLES - 1}") for k in vus), "la dernière répétition doit être gardée")
        anciens = tuple(f"#{c}" for c in range(self.CYCLES // 2))
        self.assertFalse(any(k.endswith(anciens) for k in vus), "courses non vues depuis plus de 4 h encore gardées")


if __name__ == "__main__":
    unittest.main()
