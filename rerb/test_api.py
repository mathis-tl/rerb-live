"""Fonctions Vercel (T5) : `api/trains.py` et `api/gare.py` (+ `api/sante.py`), configuration par l'environnement, secrets, en-têtes de cache.

AUCUN réseau : faux Upstash (FauxUpstash), appel PRIM injecté (`appeler`), horloge simulée. Données inventées ; URL et jetons fictifs.
"""
import importlib.util
import json
import os
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import prod
from serveur import EN_TETES, PLAFOND_JOUR, URL_PRIM_ET, ErreurPrim
from test_faux_upstash import JETON_FICTIF, FauxUpstash
from test_serveur import A, B, T0, appel, course, livraison

RACINE = Path(__file__).resolve().parent.parent
URL_FICTIVE = "https://fictif-upstash.example.test"
CLE_PRIM_FICTIVE = "cle-prim-fictive-reconnaissable-91c2"
ENV_FICTIF = {"UPSTASH_REDIS_REST_URL": URL_FICTIVE, "UPSTASH_REDIS_REST_TOKEN": JETON_FICTIF, "PRIM_API_KEY": CLE_PRIM_FICTIVE}
ENV_KV = {"KV_REST_API_URL": URL_FICTIVE, "KV_REST_API_TOKEN": JETON_FICTIF, "PRIM_API_KEY": CLE_PRIM_FICTIVE}
ENTETE_TRAINS = "public, s-maxage=5, stale-while-revalidate=10"
ENTETE_GARE = "public, s-maxage=60"
CLES_CORPS_TRAINS = {"maintenant", "demo", "service", "releve_recu", "releve_age_s", "erreur", "bloque", "plafond", "trains"}
NUIT_PARIS = datetime(2026, 11, 17, 1, 0, tzinfo=timezone.utc)   # 02:00 à Paris : coupure de nuit
JOUR_PARIS = "2026-11-16"                                         # date de Paris de T0 (11:00)
REF = "47009"                                                     # gare de Palaiseau (liste blanche, vérifiée dans les tests)


def charger_api(nom):
    """Charge api/<nom>.py comme une copie de fonction neuve (état de module propre à chaque test)."""
    spec = importlib.util.spec_from_file_location(f"api_{nom}_{id(object())}", RACINE / "api" / f"{nom}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Banc:
    """Upstash simulé, PRIM simulé (fonction `appeler` injectée), horloge partagée (secondes depuis `debut`)."""

    def __init__(self, debut=T0):
        self.debut = debut
        self.t = 0.0
        self.faux = FauxUpstash(lambda: self.t)
        self.appels = []       # (clé, url) de chaque appel PRIM
        self.echec = None      # exception levée par le prochain appel PRIM

    def maintenant(self):
        return self.debut + timedelta(seconds=self.t)

    def appeler(self, cle, url, delai=20):
        self.appels.append((cle, url))
        if self.echec is not None:
            raise self.echec
        if "stop-monitoring" in url:
            return {"StopMonitoringDelivery": [{"MonitoredStopVisit": []}]}
        enregistre = int((self.maintenant() - T0).total_seconds())
        return livraison(course("T1", [appel(A, 600, 660), appel(B, 900, 960)], enregistre=enregistre))

    def services(self, env=ENV_FICTIF):
        return prod.Services(env, http=self.faux.http, appeler=self.appeler, horloge=self.maintenant)


class Local:
    """Serveur HTTP local (127.0.0.1, port libre) qui exécute le `handler` d'un module api. Refuse de démarrer si
    PRIM_API_KEY est définie dans l'environnement : les tests n'ont jamais de vraie clé."""

    def __init__(self, handler):
        if "PRIM_API_KEY" in os.environ:
            raise RuntimeError("PRIM_API_KEY définie : refus de démarrer le serveur de test")
        self.serveur = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=self.serveur.serve_forever, daemon=True).start()

    def get(self, chemin):
        requete = urllib.request.Request(f"http://127.0.0.1:{self.serveur.server_address[1]}{chemin}")
        ouvreur = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with ouvreur.open(requete, timeout=5) as r:
                return r.status, dict(r.headers), json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), json.loads(e.read())

    def fermer(self):
        self.serveur.shutdown()
        self.serveur.server_close()


class TestTrains(unittest.TestCase):
    def setUp(self):
        self.banc = Banc()
        self.module = charger_api("trains")
        self.module._services = self.banc.services()
        self.local = Local(self.module.handler)
        self.addCleanup(self.local.fermer)

    def test_entetes_de_cache_et_de_securite(self):
        code, entetes, _ = self.local.get("/")
        self.assertEqual(code, 200)
        self.assertEqual(entetes["Cache-Control"], ENTETE_TRAINS)
        for cle, valeur in EN_TETES.items():
            if cle != "Cache-Control":
                self.assertEqual(entetes[cle], valeur, cle)

    def test_corps_aux_memes_cles_que_le_serveur_local(self):
        _, _, corps = self.local.get("/")
        self.assertEqual(set(corps), CLES_CORPS_TRAINS)
        self.assertEqual(corps["plafond"]["maximum"], PLAFOND_JOUR)
        self.assertIsNone(corps["service"])
        self.assertIsNotNone(corps["releve_recu"])

    def test_un_appel_prim_avec_la_cle_et_la_bonne_adresse(self):
        self.local.get("/")
        self.assertEqual(self.banc.appels, [(CLE_PRIM_FICTIVE, URL_PRIM_ET)])

    def test_compteur_du_jour_apres_un_appel(self):
        _, _, corps = self.local.get("/")
        self.assertEqual(corps["plafond"], {"utilises": 1, "maximum": PLAFOND_JOUR})

    def test_nuit_aucun_appel_service_nuit(self):
        banc = Banc(debut=NUIT_PARIS)
        module = charger_api("trains")
        module._services = banc.services()
        local = Local(module.handler)
        self.addCleanup(local.fermer)
        _, _, corps = local.get("/")
        self.assertEqual(corps["service"], "nuit")
        self.assertEqual(banc.appels, [])

    def test_magasin_en_panne_aucun_appel(self):
        self.banc.faux.panne = "http500"
        code, _, corps = self.local.get("/")
        self.assertEqual(code, 200)
        self.assertEqual(corps["trains"], [])
        self.assertIn("magasin", corps["erreur"])
        self.assertEqual(self.banc.appels, [])

    def test_stockage_non_configure_aucun_appel(self):
        banc = Banc()
        module = charger_api("trains")
        module._services = banc.services(env={"PRIM_API_KEY": CLE_PRIM_FICTIVE})
        local = Local(module.handler)
        self.addCleanup(local.fermer)
        _, _, corps = local.get("/")
        self.assertEqual(corps["trains"], [])
        self.assertIn("stockage", corps["erreur"])
        self.assertEqual(banc.appels, [])

    def test_cle_prim_absente_aucun_appel(self):
        banc = Banc()
        module = charger_api("trains")
        module._services = banc.services(env={"UPSTASH_REDIS_REST_URL": URL_FICTIVE, "UPSTASH_REDIS_REST_TOKEN": JETON_FICTIF})
        local = Local(module.handler)
        self.addCleanup(local.fermer)
        local.get("/")
        self.assertEqual(banc.appels, [])

    def test_blocage_429_quinze_minutes_puis_rappel(self):
        self.banc.echec = ErreurPrim("HTTP 429", 429)
        _, _, corps = self.local.get("/")
        self.assertTrue(corps["bloque"])
        self.banc.echec = None
        self.banc.t = 600.0
        self.local.get("/")
        self.assertEqual(len(self.banc.appels), 1, "bloqué pendant 15 min")
        self.banc.t = 901.0
        _, _, corps = self.local.get("/")
        self.assertEqual(len(self.banc.appels), 2)
        self.assertFalse(corps["bloque"])

    def test_blocage_401_six_heures(self):
        self.banc.echec = ErreurPrim("HTTP 401", 401)
        self.local.get("/")
        self.banc.echec = None
        self.banc.t = 5 * 3600.0
        self.local.get("/")
        self.assertEqual(len(self.banc.appels), 1)
        self.banc.t = 6 * 3600.0 + 1
        self.local.get("/")
        self.assertEqual(len(self.banc.appels), 2)

    def test_plafond_atteint_aucun_appel(self):
        self.banc.faux.valeurs[f"rerb:plafond:{JOUR_PARIS}"] = (str(PLAFOND_JOUR), None)
        _, _, corps = self.local.get("/")
        self.assertEqual(self.banc.appels, [])
        self.assertIn("plafond", corps["erreur"])

    def test_secrets_absents_des_reponses(self):
        self.banc.echec = ErreurPrim("HTTP 401", 401)
        _, entetes, corps = self.local.get("/")
        self.banc.faux.panne = "json"
        _, entetes2, corps2 = self.local.get("/")
        sortie = json.dumps([corps, corps2]) + json.dumps([entetes, entetes2])
        for secret in (CLE_PRIM_FICTIVE, JETON_FICTIF, URL_FICTIVE):
            self.assertNotIn(secret, sortie)


class TestGare(unittest.TestCase):
    def setUp(self):
        self.banc = Banc()
        self.module = charger_api("gare")
        self.module._services = self.banc.services()
        self.local = Local(self.module.handler)
        self.addCleanup(self.local.fermer)

    def test_liste_blanche_de_47_gares_dont_palaiseau(self):
        gares = self.banc.services().gares
        self.assertEqual(len(gares), 47)
        self.assertIn(REF, gares)

    def test_reference_hors_liste_404_sans_reseau(self):
        for chemin in ("/?ref=0000", "/?ref=", "/", f"/?ref={REF}&ref=47878", f"/?ref={REF}&autre=1"):
            code, entetes, _ = self.local.get(chemin)
            self.assertEqual(code, 404, chemin)
            self.assertEqual(entetes["Cache-Control"], "no-store", chemin)
        self.assertEqual(self.banc.appels, [])
        self.assertEqual(self.banc.faux.commandes, [])

    def test_gare_autorisee_un_appel_et_cache_60_secondes(self):
        code, entetes, corps = self.local.get(f"/?ref={REF}")
        self.assertEqual(code, 200)
        self.assertEqual(entetes["Cache-Control"], ENTETE_GARE)
        self.assertEqual(corps["statut"], "ok")
        self.assertEqual(corps["ref"], REF)
        self.assertEqual(len(self.banc.appels), 1)
        self.assertIn(f"StopArea%3ASP%3A{REF}%3A", self.banc.appels[0][1])

    def test_nuit_aucun_appel(self):
        banc = Banc(debut=NUIT_PARIS)
        module = charger_api("gare")
        module._services = banc.services()
        local = Local(module.handler)
        self.addCleanup(local.fermer)
        _, _, corps = local.get(f"/?ref={REF}")
        self.assertEqual(corps["statut"], "nuit")
        self.assertEqual(banc.appels, [])

    def test_blocage_ecrit_par_trains_aucun_appel(self):
        self.banc.faux.valeurs["rerb:blocage"] = (json.dumps({"statut": 429}), None)
        _, _, corps = self.local.get(f"/?ref={REF}")
        self.assertEqual(corps["statut"], "bloqué")
        self.assertEqual(self.banc.appels, [])

    def test_magasin_en_panne_aucun_appel(self):
        self.banc.faux.panne = "http500"
        _, _, corps = self.local.get(f"/?ref={REF}")
        self.assertEqual(corps["statut"], "magasin")
        self.assertEqual(self.banc.appels, [])

    def test_stockage_non_configure_aucun_appel(self):
        banc = Banc()
        module = charger_api("gare")
        module._services = banc.services(env={"PRIM_API_KEY": CLE_PRIM_FICTIVE})
        local = Local(module.handler)
        self.addCleanup(local.fermer)
        _, _, corps = local.get(f"/?ref={REF}")
        self.assertEqual(corps["statut"], "magasin")
        self.assertEqual(banc.appels, [])

    def test_cle_refusee_ecrit_le_blocage_avec_sa_duree(self):
        self.banc.echec = ErreurPrim("HTTP 401", 401)
        _, _, corps = self.local.get(f"/?ref={REF}")
        self.assertEqual(corps["statut"], "erreur")
        self.assertIsNotNone(self.banc.faux.valeurs["rerb:blocage"][1], "le blocage doit avoir une durée de vie")
        self.banc.echec = None
        self.banc.t = 3600.0
        _, _, corps = self.local.get(f"/?ref={REF}")
        self.assertEqual(corps["statut"], "bloqué")
        self.assertEqual(len(self.banc.appels), 1)


class TestSante(unittest.TestCase):
    def test_renvoie_ok_sans_toucher_au_stockage(self):
        module = charger_api("sante")
        local = Local(module.handler)
        self.addCleanup(local.fermer)
        code, _, corps = local.get("/")
        self.assertEqual((code, corps), (200, {"ok": True}))


class TestConfiguration(unittest.TestCase):
    def test_noms_kv_de_repli_utilises(self):
        banc = Banc()
        module = charger_api("trains")
        module._services = banc.services(env=ENV_KV)
        local = Local(module.handler)
        self.addCleanup(local.fermer)
        _, _, corps = local.get("/")
        self.assertIsNotNone(corps["releve_recu"])
        self.assertTrue(banc.faux.commandes)

    def test_erreur_de_configuration_sans_valeur(self):
        services = Banc().services(env={})
        self.assertIsNone(services.magasin)
        self.assertIn("UPSTASH", services.erreur_configuration)
        self.assertNotIn(URL_FICTIVE, services.erreur_configuration)
        self.assertNotIn(JETON_FICTIF, services.erreur_configuration)

    def test_le_serveur_de_test_refuse_une_vraie_cle(self):
        module = charger_api("trains")
        with mock.patch.dict(os.environ, {"PRIM_API_KEY": CLE_PRIM_FICTIVE}):
            with self.assertRaises(RuntimeError):
                Local(module.handler)


if __name__ == "__main__":
    unittest.main()
