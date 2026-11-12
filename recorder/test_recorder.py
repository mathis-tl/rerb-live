import base64
import gzip
import http.server
import json
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

import recorder
from google.transit import gtfs_realtime_pb2 as pb

SECRET = "SECRETKEY123"


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/ok":
            body = gzip.compress(b"bonjour")
            self.send_response(200)
            self.send_header("Content-Encoding", "gzip")
            self.send_header("X-RateLimit-Remaining", "9")
            self.send_header("Set-Cookie", "ne-pas-garder=1")
        elif self.path == "/badgzip":
            body = b"pas du gzip"
            self.send_response(200)
            self.send_header("Content-Encoding", "gzip")
        elif self.path == "/redir":
            body = b""
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:1/ailleurs")
        else:
            body = b"oups"
            self.send_response(500)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def serveur():
    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_port}"


def lire(path):
    return [json.loads(l) for l in gzip.decompress(Path(path).read_bytes()).splitlines()]


def feed(route_ids, with_alert=False):
    m = pb.FeedMessage()
    m.header.gtfs_realtime_version = "2.0"
    m.header.timestamp = 42
    for i, rid in enumerate(route_ids):
        e = m.entity.add()
        e.id = str(i)
        e.trip_update.trip.route_id = rid
    if with_alert:
        e = m.entity.add()
        e.id = "alerte"
        e.alert.cause = 1
    return m


class TestOutils(unittest.TestCase):
    def test_load_env(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".env"
            p.write_text("# c\nA=1\nB='x y'\n\nmauvaise ligne\n")
            self.assertEqual(recorder.load_env(p), {"A": "1", "B": "x y"})

    def test_redact_masque_aussi_les_formes_echappees(self):
        recorder._SECRETS.clear()
        recorder.register_secrets(["abc\ndef"])
        self.assertNotIn("abc", recorder.redact("erreur b'abc\\ndef' et abc\ndef"))
        recorder._SECRETS.clear()


class TestWriter(unittest.TestCase):
    def test_rotation_par_heure_et_membres_gzip(self):
        with tempfile.TemporaryDirectory() as d:
            w = recorder.Writer(d)
            h1 = datetime(2026, 11, 12, 10, 59, tzinfo=timezone.utc)
            h2 = datetime(2026, 11, 12, 11, 0, tzinfo=timezone.utc)
            w.append("s", [b'{"a":1}'], h1)
            w.append("s", [b'{"a":2}', b'{"a":3}'], h1)
            w.append("s", [b'{"a":4}'], h2)
            p1, p2 = w.path_for("s", h1), w.path_for("s", h2)
            self.assertNotEqual(p1, p2)
            self.assertEqual([r["a"] for r in lire(p1)], [1, 2, 3])
            self.assertEqual(gzip.decompress(p2.read_bytes()), b'{"a":4}\n')

    def test_ecriture_interrompue_laisse_le_fichier_lisible(self):
        class Casse(recorder.Writer):
            fail = True

            def _write(self, f, blob):
                if Casse.fail:
                    f.write(blob[: len(blob) // 2])  # moitié de membre gzip écrite
                    f.flush()
                    raise OSError("disque plein simulé")
                super()._write(f, blob)

        with tempfile.TemporaryDirectory() as d:
            w = Casse(d)
            h = datetime(2026, 11, 12, 10, 0, tzinfo=timezone.utc)
            Casse.fail = False
            w.append("s", [b'{"a":1}'], h)
            Casse.fail = True
            with self.assertRaises(OSError):
                w.append("s", [b'{"a":2}'], h)
            Casse.fail = False
            w.append("s", [b'{"a":3}'], h)
            self.assertEqual([r["a"] for r in lire(w.path_for("s", h))], [1, 3])


class TestEcritureBasNiveau(unittest.TestCase):
    def test_ecriture_courte_est_rejouee(self):
        class Faux:
            def __init__(self):
                self.data = b""

            def write(self, view):
                n = min(3, len(view))  # n'accepte que 3 octets à la fois
                self.data += bytes(view[:n])
                return n

        f = Faux()
        recorder.Writer("/nonexistent")._write(f, b"0123456789")
        self.assertEqual(f.data, b"0123456789")

    def test_ecriture_sans_progres_leve_une_erreur(self):
        class Bloque:
            def write(self, view):
                return 0

        with self.assertRaises(OSError):
            recorder.Writer("/nonexistent")._write(Bloque(), b"abc")

    def test_echec_au_milieu_est_annule_sans_tampon(self):
        """Fichier sans tampon : la moitié écrite est vraiment sur disque, puis retirée."""
        class Casse(recorder.Writer):
            def _write(self, f, blob):
                f.write(blob[: len(blob) // 2])
                raise OSError("disque plein simulé")

        with tempfile.TemporaryDirectory() as d:
            h = datetime(2026, 11, 12, 10, 0, tzinfo=timezone.utc)
            recorder.Writer(d).append("s", [b'{"a":1}'], h)
            p = recorder.Writer(d).path_for("s", h)
            avant = p.stat().st_size
            with self.assertRaises(OSError):
                Casse(d).append("s", [b'{"a":2}'], h)
            self.assertEqual(p.stat().st_size, avant)
            self.assertEqual([r["a"] for r in lire(p)], [1])


class TestQuota(unittest.TestCase):
    def test_plafond_persistant_et_changement_de_jour(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "q.json"
            q = recorder.DailyQuota(p, cap=3)
            self.assertEqual([q.allow("2026-11-12") for _ in range(4)], [True, True, True, False])
            q2 = recorder.DailyQuota(p, cap=3)  # redémarrage : le compteur est rechargé
            self.assertFalse(q2.allow("2026-11-12"))
            self.assertTrue(q2.allow("2026-11-13"))


class TestFiltre(unittest.TestCase):
    def test_garde_300_seulement(self):
        m = feed(["3001Z", "1010", "3002A", "2550"])
        v = m.entity.add()
        v.id = "v"
        v.vehicle.trip.route_id = "3001K"
        out = pb.FeedMessage()
        out.ParseFromString(recorder.filter_trains(m.SerializeToString()))
        self.assertEqual(out.header.timestamp, 42)
        self.assertEqual(sorted(e.id for e in out.entity), ["0", "2", "v"])

    def test_route_id_vide_et_alerte_sont_ecartes(self):
        m = feed(["", "3001Z"], with_alert=True)
        out = pb.FeedMessage()
        out.ParseFromString(recorder.filter_trains(m.SerializeToString()))
        self.assertEqual([e.id for e in out.entity], ["1"])


class TestFetch(unittest.TestCase):
    def test_gzip_en_tetes_et_erreur_http(self):
        srv, base = serveur()
        try:
            r = recorder.fetch(base + "/ok")
            self.assertEqual((r["status"], r["body"], r["error"]), (200, b"bonjour", None))
            self.assertEqual(r["headers"]["x-ratelimit-remaining"], "9")
            self.assertIn("date", r["headers"])
            self.assertNotIn("set-cookie", r["headers"])
            r = recorder.fetch(base + "/ko")
            self.assertEqual((r["status"], r["body"], r["error"]), (500, b"oups", "HTTP 500"))
        finally:
            srv.shutdown()
        r = recorder.fetch("http://127.0.0.1:1/", timeout=2)
        self.assertIsNone(r["status"])
        self.assertTrue(r["error"])

    def test_gzip_corrompu_garde_le_corps_brut_et_signale(self):
        srv, base = serveur()
        try:
            r = recorder.fetch(base + "/badgzip")
        finally:
            srv.shutdown()
        self.assertEqual(r["body"], b"pas du gzip")
        self.assertIn("gzip", r["error"])

    def test_redirection_non_suivie(self):
        srv, base = serveur()
        try:
            r = recorder.fetch(base + "/redir", {"x-api-key": SECRET})
        finally:
            srv.shutdown()
        self.assertEqual(r["status"], 302)
        self.assertEqual(r["error"], "HTTP 302")

    def test_cle_invalide_ne_fuit_pas_dans_l_erreur(self):
        recorder._SECRETS.clear()
        recorder.register_secrets([SECRET + "\nabc"])
        r = recorder.fetch("http://127.0.0.1:1/", {"x-api-key": SECRET + "\nabc"})
        self.assertIsNone(r["status"])
        self.assertNotIn(SECRET, json.dumps(r["error"]))
        recorder._SECRETS.clear()

    def test_cle_absente_des_fichiers_apres_erreur_reseau(self):
        recorder._SECRETS.clear()
        recorder.register_secrets([SECRET])
        with tempfile.TemporaryDirectory() as d:
            j, w = recorder.Journal(Path(d) / "l.log"), recorder.Writer(d)
            job = recorder.http_job("s", "http://127.0.0.1:1/", {"x-api-key": SECRET}, w, j)
            job()
            j.log(f"erreur avec {SECRET}")
            tout = b"".join(gzip.decompress(p.read_bytes()) for p in Path(d).rglob("*.jsonl.gz"))
            tout += (Path(d) / "l.log").read_bytes()
            self.assertNotIn(SECRET.encode(), tout)
        recorder._SECRETS.clear()


class TestHttpJob(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.j = recorder.Journal(Path(self.d.name) / "l.log")
        self.w = recorder.Writer(self.d.name)
        self.srv, self.base = serveur()

    def tearDown(self):
        self.srv.shutdown()
        self.d.cleanup()

    def test_filtre_en_echec_garde_le_brut(self):
        def boom(_):
            raise ValueError("x")
        recorder.http_job("s", self.base + "/ok", None, self.w, self.j, boom)()
        rec = lire(next(Path(self.d.name).rglob("s_*.jsonl.gz")))[0]
        self.assertFalse(rec["filtered"])
        self.assertEqual(base64.b64decode(rec["body_b64"]), b"bonjour")
        self.assertEqual(self.j.counters["s"]["filter_fail"], 1)
        self.assertEqual(self.j.counters["s"]["ok"], 1)

    def test_erreur_http_est_enregistree_et_comptee(self):
        recorder.http_job("s", self.base + "/ko", None, self.w, self.j)()
        rec = lire(next(Path(self.d.name).rglob("s_*.jsonl.gz")))[0]
        self.assertEqual((rec["status"], rec["error"]), (500, "HTTP 500"))
        self.assertEqual(self.j.counters["s"]["err"], 1)

    def test_temoin_ecrit_le_corps_non_filtre_du_meme_appel_une_fois(self):
        job = recorder.http_job("s", self.base + "/ok", None, self.w, self.j,
                                lambda b: b"filtre", witness_stream="w", witness_every=3600)
        job()
        job()
        s = lire(next(Path(self.d.name).rglob("s_*.jsonl.gz")))
        w = lire(next(Path(self.d.name).rglob("w_*.jsonl.gz")))
        self.assertEqual(len(s), 2)
        self.assertEqual(len(w), 1)
        self.assertEqual(base64.b64decode(w[0]["body_b64"]), b"bonjour")
        self.assertEqual(base64.b64decode(s[0]["body_b64"]), b"filtre")

    def test_quota_epuise_saute_l_appel(self):
        recorder.http_job("s", self.base + "/ok", None, self.w, self.j, gate=lambda: False)()
        self.assertEqual(list(Path(self.d.name).rglob("s_*.jsonl.gz")), [])
        self.assertEqual(self.j.counters["s"]["quota_skip"], 1)


class TestPoll(unittest.TestCase):
    def test_s_arrete(self):
        stop, n = threading.Event(), []
        with tempfile.TemporaryDirectory() as d:
            j = recorder.Journal(Path(d) / "l.log")

            def job():
                n.append(1)
                if len(n) == 3:
                    stop.set()
            recorder.poll("t", 0.01, job, stop, j)
        self.assertEqual(len(n), 3)

    def test_une_exception_n_arrete_pas_la_boucle_et_un_creneau_rate_est_compte(self):
        stop, n = threading.Event(), []
        with tempfile.TemporaryDirectory() as d:
            j = recorder.Journal(Path(d) / "l.log")

            def job():
                n.append(1)
                if len(n) == 1:
                    raise RuntimeError("boum")
                if len(n) == 2:
                    time.sleep(0.15)  # plus long que l'intervalle de 0.05 s
                if len(n) == 3:
                    stop.set()
            recorder.poll("t", 0.05, job, stop, j)
        self.assertEqual(len(n), 3)
        self.assertEqual(j.counters["t"]["exception"], 1)
        self.assertGreaterEqual(j.counters["t"]["missed"], 1)


class TestMqtt(unittest.TestCase):
    def _rec(self, d, writer=None):
        j = recorder.Journal(Path(d) / "l.log")
        return recorder.MqttRecorder(writer or recorder.Writer(d), j, threading.Event()), j

    @staticmethod
    def _msg(rec, when, texte):
        rec.buf.append((when, json.dumps({"p": texte}).encode()))

    def test_lot_reparti_selon_l_heure_de_reception(self):
        with tempfile.TemporaryDirectory() as d:
            rec, _ = self._rec(d)
            a = datetime(2026, 11, 12, 10, 59, 58, tzinfo=timezone.utc)
            b = datetime(2026, 11, 12, 11, 0, 2, tzinfo=timezone.utc)
            self._msg(rec, a, "a")
            self._msg(rec, b, "b")
            rec.flush()
            self.assertEqual(lire(rec.writer.path_for(rec.STREAM, a))[0]["p"], "a")
            self.assertEqual(lire(rec.writer.path_for(rec.STREAM, b))[0]["p"], "b")

    def test_echec_d_ecriture_garde_les_messages_puis_les_reecrit(self):
        class Casse(recorder.Writer):
            fail = True

            def append(self, *a, **k):
                if Casse.fail:
                    raise OSError("disque plein simulé")
                return super().append(*a, **k)

        with tempfile.TemporaryDirectory() as d:
            rec, j = self._rec(d, Casse(d))
            t = datetime(2026, 11, 12, 10, 0, tzinfo=timezone.utc)
            self._msg(rec, t, "x")
            rec.flush()
            self.assertEqual(len(rec.buf), 1)
            self.assertEqual(j.counters[rec.STREAM]["write_fail"], 1)
            Casse.fail = False
            rec.flush()
            self.assertEqual(rec.buf, [])
            self.assertEqual(lire(rec.writer.path_for(rec.STREAM, t))[0]["p"], "x")

    def test_tampon_plafonne(self):
        class Casse(recorder.Writer):
            def append(self, *a, **k):
                raise OSError("x")

        with tempfile.TemporaryDirectory() as d:
            rec, j = self._rec(d, Casse(d))
            t = datetime(2026, 11, 12, 10, 0, tzinfo=timezone.utc)
            old = recorder.MQTT_MAX_BUFFER
            recorder.MQTT_MAX_BUFFER = 3
            try:
                for i in range(5):
                    self._msg(rec, t, str(i))
                rec.flush()
            finally:
                recorder.MQTT_MAX_BUFFER = old
            self.assertEqual(len(rec.buf), 3)
            self.assertEqual(j.counters[rec.STREAM]["dropped"], 2)


class TestGardeFous(unittest.TestCase):
    def test_disk_guard_arrete_et_signale_l_echec(self):
        with tempfile.TemporaryDirectory() as d:
            j = recorder.Journal(Path(d) / "l.log")
            stop, failed = threading.Event(), threading.Event()
            recorder.disk_guard(d, 10 ** 9, stop, j, failed, every=0.01)
            self.assertTrue(stop.is_set() and failed.is_set())

    def test_verrou_refuse_deux_enregistreurs(self):
        with tempfile.TemporaryDirectory() as d:
            premier = recorder.acquire_lock(Path(d) / ".lock")
            with self.assertRaises(RuntimeError):
                recorder.acquire_lock(Path(d) / ".lock")
            premier.close()
            recorder.acquire_lock(Path(d) / ".lock").close()

    def test_deuxieme_processus_prim_refuse(self):
        with tempfile.TemporaryDirectory() as d:
            env = Path(d) / ".env"
            env.write_text("PRIM_API_KEY=def\n")
            q = str(Path(d) / "q.json")
            premier = recorder.acquire_lock(q + ".lock")  # simule un autre enregistreur PRIM en cours
            try:
                with self.assertRaises(SystemExit):
                    recorder.main(["--out", str(Path(d) / "o"), "--env", str(env), "--sources", "prim",
                                   "--quota-file", q, "--duration", "1"])
            finally:
                premier.close()

    def test_options_invalides_refusees(self):
        with tempfile.TemporaryDirectory() as d:
            env = Path(d) / ".env"
            env.write_text("MBTA_API_KEY=abc\nPRIM_API_KEY=def\n")
            base = ["--out", str(Path(d) / "o"), "--env", str(env), "--duration", "1"]
            for extra in (["--prim-interval", "60"], ["--prim-interval", "nan"]):
                with self.assertRaises(SystemExit):
                    recorder.main(base + extra)
            env.write_text("PRIM_API_KEY=def\n")  # MBTA_API_KEY absente
            with self.assertRaises(SystemExit):
                recorder.main(base + ["--sources", "mbta"])
            env.write_text("MBTA_API_KEY=café\n")  # non ASCII
            with self.assertRaises(SystemExit):
                recorder.main(base + ["--sources", "mbta"])


if __name__ == "__main__":
    unittest.main()
