#!/usr/bin/env python3
"""Enregistreur brut du test 1 : MBTA (Boston), HSL (Helsinki), PRIM (RER B).

Il enregistre seulement des réponses brutes et leurs métadonnées, sans aucune analyse.
Sortie : un fichier .jsonl.gz par flux et par heure UTC (voir README.md).
Aucune clé API n'est écrite dans les fichiers ni dans le journal.

QUI FAIT QUOI (de haut en bas dans ce fichier)
==============================================
Constantes (URL, lignes MBTA, préfixe des trains HSL)
    Tout ce qui est « réglage » est en haut : adresses des flux, liste des 13 lignes
    de Boston, préfixe "300" qui identifie un train à Helsinki.

utc_now(), load_env(), register_secrets(), redact()
    Petits outils : heure UTC, lecture du fichier .env, et masquage des clés dans
    tout texte qui pourrait être écrit (journal, messages d'erreur).

Journal
    Écrit recorder.log (démarrages, erreurs, coupures, compteurs). Ne plante jamais,
    même si le disque est plein : il retombe sur l'écran (stderr).

Writer
    Seul endroit qui écrit les données sur disque. Un fichier par flux et par heure.
    Chaque écriture est un « membre gzip » collé à la suite du fichier. Si une
    écriture échoue à moitié, le fichier est remis dans son état d'avant.

DailyQuota
    Compteur d'appels PRIM par jour UTC, sauvegardé dans un fichier. Bloque les appels
    à 1 450 par jour (plafond de la clé : 1 500), même si on relance le programme.
    Un seul processus à la fois peut utiliser PRIM (verrou sur le fichier du compteur).

fetch()
    Fait UN appel HTTP. Ne lève jamais d'erreur : renvoie le code HTTP, les en-têtes
    utiles, le corps décompressé et l'erreur éventuelle. Ne suit jamais de redirection
    (pour ne pas envoyer une clé à un autre site).

make_record() / filter_trains()
    make_record : met la réponse dans une ligne JSON avec les heures de réception.
    filter_trains : pour Helsinki, garde seulement les trains dans un flux GTFS-RT.

http_job() + poll()
    http_job : fabrique la tâche « un appel + une écriture » pour un flux HTTP.
    poll : répète une tâche toutes les N secondes sans dérive, et survit à ses erreurs.
    Boston, PRIM et les prévisions HSL passent par là.

MqttRecorder
    Flux continu de Helsinki (positions des trains, ~97 messages par seconde).
    Reçoit les messages, les garde en mémoire, les écrit par lots toutes les 10 s.
    Se reconnecte seul. Si l'écriture échoue, les messages restent en mémoire.

disk_guard() / stats_loop() / acquire_lock()
    Garde-fous : arrêt propre si le disque est presque plein ; ligne de stats chaque
    minute (et alerte si le Mac a dormi) ; interdiction de lancer deux enregistreurs.

main()
    Lit les options, vérifie les clés, démarre un fil par flux, puis surveille que
    tous les fils sont vivants jusqu'à l'arrêt (durée écoulée, Ctrl-C ou disque plein).
"""
import argparse
import base64
import fcntl
import gzip
import json
import math
import os
import shutil
import signal
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# ----------------------------------------------------------------------------
# Constantes
# ----------------------------------------------------------------------------
USER_AGENT = "rerb-live-recorder/0.2"
TRAIN_PREFIX = "300"  # route_id des trains HSL (3001*, 3002*) ; bus et trams commencent autrement
MBTA_ROUTES = ["CR-Fairmount", "CR-NewBedford", "CR-Fitchburg", "CR-Worcester",
               "CR-Franklin", "CR-Greenbush", "CR-Haverhill", "CR-Kingston",
               "CR-Lowell", "CR-Needham", "CR-Newburyport", "CR-Providence",
               "CR-Foxboro"]
URL_MBTA_VEHICLES = "https://api-v3.mbta.com/vehicles?filter%5Broute_type%5D=2"
URL_MBTA_PREDICTIONS = "https://api-v3.mbta.com/predictions?filter%5Broute%5D=" + ",".join(MBTA_ROUTES)
URL_HSL_TU = "https://realtime.hsl.fi/realtime/trip-updates/v2/hsl"
URL_HSL_VP = "https://realtime.hsl.fi/realtime/vehicle-positions/v2/hsl"
URL_PRIM_ET = "https://prim.iledefrance-mobilites.fr/marketplace/estimated-timetable?LineRef=STIF%3ALine%3A%3AC01743%3A"
URL_PRIM_SM = "https://prim.iledefrance-mobilites.fr/marketplace/stop-monitoring?MonitoringRef=STIF%3AStopArea%3ASP%3A58774%3A"
URL_PRIM_SM_GARE = "https://prim.iledefrance-mobilites.fr/marketplace/stop-monitoring?MonitoringRef=STIF%3AStopArea%3ASP%3A{}%3A"
GARES_CSV = Path(__file__).resolve().parent.parent / "rerb" / "donnees" / "arrets_rerb_C01743.csv"
GARES_INTERVAL = 30       # la source PRIM ne change que toutes les ~30 s (mesuré le 15/11/2026)
GARES_DAILY_CAP = 300_000  # 47 gares x 2 880 cycles = 135 360 par jour ; plafond PRIM 1 000 000
MQTT_HOST, MQTT_PORT = "mqtt.hsl.fi", 8883
MQTT_TOPIC = "/hfp/v2/journey/ongoing/vp/train/#"  # trains seulement
KEPT_HEADERS = ("date", "last-modified", "content-type", "etag", "age")
PRIM_MIN_INTERVAL = 120   # 2 flux x 720 appels = 1 440/jour, sous le plafond de 1 500
PRIM_DAILY_CAP = 1450     # coupe-circuit : marge de 50 appels sous le plafond
MQTT_MAX_BUFFER = 200_000  # messages gardés en mémoire si le disque refuse d'écrire (~35 min)
CLOCK_GAP_S = 90          # écart d'horloge au-delà duquel on signale une veille du Mac


def utc_now():
    return datetime.now(timezone.utc)


def load_env(path):
    """Lit un fichier .env (CLE=valeur) dans un dict, sans toucher à os.environ."""
    env = {}
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip("'\"")
    return env


# Valeurs secrètes à masquer dans tout texte écrit (journal, champ "error").
_SECRETS = []


def register_secrets(values):
    """Enregistre les clés ; on masque aussi leurs formes échappées (\\n, b'...')."""
    for v in values:
        if not v:
            continue
        for form in {v, repr(v)[1:-1], repr(v.encode())[2:-1]}:
            if form and form not in _SECRETS:
                _SECRETS.append(form)


def redact(text):
    text = str(text)
    for s in _SECRETS:
        text = text.replace(s, "<clé masquée>")
    return text


def valid_header_value(v):
    """Une clé valide = ASCII imprimable, sans retour à la ligne ni caractère de contrôle."""
    return v.isascii() and v.isprintable()


# ----------------------------------------------------------------------------
# Journal
# ----------------------------------------------------------------------------
class Journal:
    """recorder.log + compteurs cumulés par flux. Ne lève jamais d'exception."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.counters = {}

    def log(self, msg):
        line = f"{utc_now().isoformat(timespec='seconds')} {redact(msg)}"
        with self.lock:
            try:
                with open(self.path, "a") as f:
                    f.write(line + "\n")
            except OSError:
                pass  # disque plein ou dossier disparu : on garde au moins l'écran
        try:
            print(line, file=sys.stderr, flush=True)
        except Exception:
            pass

    def count(self, stream, key, n=1):
        with self.lock:
            c = self.counters.setdefault(stream, {})
            c[key] = c.get(key, 0) + n

    def snapshot(self):
        with self.lock:
            return json.dumps(self.counters, sort_keys=True)


# ----------------------------------------------------------------------------
# Écriture sur disque
# ----------------------------------------------------------------------------
class Writer:
    """Un fichier par flux et par heure UTC ; chaque écriture est un membre gzip."""

    def __init__(self, root):
        self.root = Path(root)
        self.lock = threading.Lock()

    def path_for(self, stream, when):
        return (self.root / stream / when.strftime("%Y-%m-%d")
                / f"{stream}_{when.strftime('%Y-%m-%dT%H')}.jsonl.gz")

    def _write(self, f, blob):
        """Écrit tout `blob` sur un fichier SANS tampon (une écriture courte est rejouée)."""
        view = memoryview(blob)
        while view:
            n = f.write(view)
            if not n:
                raise OSError("écriture sans progrès")
            view = view[n:]

    def append(self, stream, lines, when=None):
        """Ajoute des lignes JSON au fichier de l'heure de `when` (heure de RÉCEPTION).

        Si l'écriture échoue en route (disque plein, arrêt), le fichier est remis à sa
        taille d'avant : sinon un membre gzip tronqué rendrait illisible tout ce qui suit.
        """
        when = when or utc_now()
        data = b"".join(l if l.endswith(b"\n") else l + b"\n" for l in lines)
        if not data:
            return
        p = self.path_for(stream, when)
        blob = gzip.compress(data, 6)
        with self.lock:
            p.parent.mkdir(parents=True, exist_ok=True)
            # buffering=0 : aucun tampon caché, donc ce qui est « écrit » l'est vraiment, et
            # os.ftruncate remet le fichier à sa taille d'avant sans re-vider un tampon qui échouerait.
            with open(p, "ab", buffering=0) as f:
                start = os.fstat(f.fileno()).st_size
                try:
                    self._write(f, blob)
                except BaseException:
                    try:
                        os.ftruncate(f.fileno(), start)
                    except OSError:
                        pass
                    raise


class DailyQuota:
    """Compteur d'appels PRIM par jour UTC, persistant, pour ne jamais dépasser le plafond."""

    def __init__(self, path, cap=PRIM_DAILY_CAP):
        self.path, self.cap = Path(path), cap
        self.lock = threading.Lock()
        self.day, self.count = None, 0
        try:
            d = json.loads(self.path.read_text())
            self.day, self.count = d.get("day"), int(d.get("count", 0))
        except (OSError, ValueError):
            pass

    def allow(self, today=None):
        today = today or utc_now().strftime("%Y-%m-%d")
        with self.lock:
            if self.day != today:
                self.day, self.count = today, 0
            if self.count >= self.cap:
                return False
            self.count += 1
            try:
                tmp = self.path.with_suffix(".tmp")
                tmp.write_text(json.dumps({"day": self.day, "count": self.count}))
                os.replace(tmp, self.path)
            except OSError:
                pass  # compteur gardé en mémoire ; le programme continue
            return True


# ----------------------------------------------------------------------------
# Un appel HTTP
# ----------------------------------------------------------------------------
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None  # une redirection devient une erreur 3xx enregistrée, jamais suivie


_OPENER = urllib.request.build_opener(_NoRedirect)


def fetch(url, headers=None, timeout=30):
    """GET HTTP ; renvoie status, en-têtes utiles, corps décompressé, erreur, durée. Ne lève rien."""
    headers = headers or {}
    t0 = time.monotonic()
    for name, value in headers.items():
        if not valid_header_value(str(value)):
            return {"status": None, "headers": {}, "body": None,
                    "error": f"en-tête {name} invalide (caractère non imprimable ou non ASCII)",
                    "duration": 0.0}
    req = urllib.request.Request(
        url, headers={"Accept-Encoding": "gzip", "User-Agent": USER_AGENT, **headers})
    status, hdrs, body, err = None, None, None, None
    try:
        with _OPENER.open(req, timeout=timeout) as r:
            status, hdrs, body = r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        status, hdrs, err = e.code, e.headers, f"HTTP {e.code}"
        try:
            body = e.read()
        except Exception:
            body = None
    except Exception as e:  # réseau, délai, TLS...
        err = redact(f"{type(e).__name__}: {e}")
    if body and hdrs is not None and (hdrs.get("content-encoding") or "").lower() == "gzip":
        try:
            body = gzip.decompress(body)
        except Exception as e:
            err = (err or "") + redact(f" gzip: {e}")
    kept = {}
    if hdrs is not None:
        for k, v in hdrs.items():
            kl = k.lower()
            if kl in KEPT_HEADERS or kl.startswith("x-ratelimit"):
                kept[kl] = v
    return {"status": status, "headers": kept, "body": body, "error": err,
            "duration": time.monotonic() - t0}


def make_record(stream, url, res, body, filtered=False, recv=None):
    """Une ligne d'enregistrement : métadonnées + corps en base64 (le corps peut être binaire)."""
    recv = recv or utc_now()
    return {
        "stream": stream,
        "ts_recv_utc": recv.isoformat(timespec="milliseconds"),
        "ts_recv_local": recv.astimezone().isoformat(timespec="milliseconds"),
        "url": url,
        "status": res["status"],
        "headers": res["headers"],
        "duration_s": round(res["duration"], 3),
        "error": res["error"],
        "filtered": filtered,
        "body_b64": base64.b64encode(body).decode() if body else None,
    }


def filter_trains(raw):
    """Garde les entités GTFS-RT dont le route_id commence par 300 (trains HSL)."""
    from google.transit import gtfs_realtime_pb2 as pb
    m = pb.FeedMessage()
    m.ParseFromString(raw)
    out = pb.FeedMessage()
    out.header.CopyFrom(m.header)
    for e in m.entity:
        if e.HasField("trip_update"):
            rid = e.trip_update.trip.route_id
        elif e.HasField("vehicle"):
            rid = e.vehicle.trip.route_id
        else:
            rid = ""
        if rid.startswith(TRAIN_PREFIX):
            out.entity.add().CopyFrom(e)
    return out.SerializeToString()


# ----------------------------------------------------------------------------
# Flux HTTP : une tâche + une boucle régulière
# ----------------------------------------------------------------------------
def http_job(stream, url, headers, writer, journal, transform=None,
             gate=None, witness_stream=None, witness_every=3600):
    """Fabrique la tâche « un appel + une écriture » d'un flux HTTP.

    transform : fonction de filtrage appliquée au corps (HSL). Si elle échoue, on garde le brut.
    gate      : fonction qui répond non quand le quota du jour est épuisé (PRIM).
    witness_stream : toutes les `witness_every` s, copie AUSSI le corps non filtré du même
                     appel dans ce flux (permet de vérifier plus tard que le filtre n'a rien perdu).
    """
    last_witness = [None]

    def job():
        if gate is not None and not gate():
            journal.count(stream, "quota_skip")
            journal.log(f"{stream} appel sauté : quota du jour atteint")
            return
        res = fetch(url, headers)
        recv = utc_now()
        raw, body, filtered = res["body"], res["body"], False
        if transform and raw and res["status"] == 200:
            try:
                body, filtered = transform(raw), True
            except Exception as e:  # on garde le brut plutôt que de perdre le relevé
                journal.log(f"{stream} filtre en échec ({e!r}), brut conservé")
                journal.count(stream, "filter_fail")
        writer.append(stream, [json.dumps(make_record(stream, url, res, body, filtered, recv)).encode()],
                      when=recv)
        if witness_stream and raw:
            now = time.monotonic()
            if last_witness[0] is None or now - last_witness[0] >= witness_every:
                last_witness[0] = now
                writer.append(witness_stream,
                              [json.dumps(make_record(witness_stream, url, res, raw, False, recv)).encode()],
                              when=recv)
        ok = res["status"] == 200 and not res["error"]
        journal.count(stream, "ok" if ok else "err")
        journal.count(stream, "bytes", len(body or b""))
        if not ok:
            journal.log(f"{stream} erreur status={res['status']} {res['error']}")
    return job


def load_gares(path):
    """Numéros de gares (partie finale de stop_id) lus dans le CSV des arrêts, sans doublon, dans l'ordre."""
    import csv
    out = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            n = row["stop_id"].rsplit(":", 1)[-1]
            if n and n not in out:
                out.append(n)
    return out


def gares_job(stream, url_template, gares, headers, writer, journal, gate=None, pause=0.2):
    """Un cycle = un appel par gare, écrits d'un bloc (une ligne par appel, corps brut).

    Un statut 401, 403 ou 429 arrête le cycle (on ne martèle pas un service qui refuse).
    gate : fonction qui répond non quand le quota du jour est épuisé ; les appels restants sont sautés.
    """
    def job():
        lines, premier = [], None
        for n in gares:
            if gate is not None and not gate():
                journal.count(stream, "quota_skip")
                journal.log(f"{stream} cycle interrompu : quota du jour atteint")
                break
            url = url_template.format(n)
            res = fetch(url, headers)
            recv = utc_now()
            premier = premier or recv
            lines.append(json.dumps(make_record(stream, url, res, res["body"], False, recv)).encode())
            ok = res["status"] == 200 and not res["error"]
            journal.count(stream, "ok" if ok else "err")
            journal.count(stream, "bytes", len(res["body"] or b""))
            if not ok:
                journal.log(f"{stream} erreur gare {n} status={res['status']} {res['error']}")
            if res["status"] in (401, 403, 429):
                journal.count(stream, "limite")
                journal.log(f"{stream} cycle arrêté : status {res['status']}")
                break
            if pause:
                time.sleep(pause)
        if lines:
            writer.append(stream, lines, when=premier)
    return job


def poll(name, interval, job, stop, journal, start_delay=0.0):
    """Appelle job toutes les `interval` s sans dérive ; une erreur n'arrête jamais la boucle.

    Si un appel dure plus que l'intervalle, le créneau raté est journalisé et on repart
    de l'instant présent (pas de rafale de rattrapage : important pour le quota PRIM).
    """
    if start_delay and stop.wait(start_delay):
        return
    nxt = time.monotonic()
    while not stop.is_set():
        try:
            job()
        except Exception as e:
            journal.log(f"{name} exception {e!r}")
            journal.count(name, "exception")
        nxt += interval
        delay = nxt - time.monotonic()
        if delay < 0:
            journal.log(f"{name} créneau(x) manqué(s)")
            journal.count(name, "missed")
            nxt = time.monotonic()
            delay = 0
        if stop.wait(delay):
            break


# ----------------------------------------------------------------------------
# Flux MQTT de Helsinki
# ----------------------------------------------------------------------------
class MqttRecorder:
    """Abonnement MQTT HSL (trains). Les messages sont écrits par lots toutes les 10 s.

    Chaque message garde son heure de réception ; au passage d'une heure, un lot est
    réparti entre les fichiers des deux heures concernées.
    """

    STREAM = "hsl_mqtt_trains"

    def __init__(self, writer, journal, stop, host=MQTT_HOST, port=MQTT_PORT, tls=True):
        self.writer, self.journal, self.stop = writer, journal, stop
        self.host, self.port, self.tls = host, port, tls
        self.buf, self.lock = [], threading.Lock()  # buf = liste de (heure_reception, ligne)
        self.last_msg = time.monotonic()
        self.client = None

    def start(self):
        import paho.mqtt.client as mqtt
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        c.suppress_exceptions = True  # une erreur dans un rappel ne doit pas tuer le fil réseau de paho
        c.enable_logger()  # ...mais elle reste visible (module logging, sur stderr)
        c.on_connect = self._on_connect
        c.on_disconnect = self._on_disconnect
        c.on_message = self._on_message
        c.reconnect_delay_set(1, 10)  # reconnexion automatique : attente 1 s, doublée jusqu'à 10 s (perte max ~10 s après retour du réseau)
        if self.tls:
            c.tls_set()
        self.client = c
        c.connect_async(self.host, self.port, keepalive=30)
        c.loop_start()
        t = threading.Thread(target=self._flush_loop, daemon=True, name="mqtt_flush")
        t.start()
        return t

    def _on_connect(self, c, u, flags, rc, props=None):
        self.journal.log(f"mqtt connecté rc={rc}")
        c.subscribe(MQTT_TOPIC)  # réabonnement à chaque (re)connexion

    def _on_disconnect(self, c, u, flags, rc, props=None):
        self.journal.log(f"mqtt déconnecté rc={rc}")
        self.journal.count(self.STREAM, "disconnect")

    def _on_message(self, c, u, m):
        recv = utc_now()
        line = json.dumps({"ts_recv_utc": recv.isoformat(timespec="milliseconds"),
                           "topic": m.topic,
                           "payload": m.payload.decode("utf-8", "replace")}).encode()
        with self.lock:
            self.buf.append((recv, line))
        self.last_msg = time.monotonic()

    def flush(self):
        """Écrit le tampon. Si l'écriture échoue, les messages sont remis en mémoire (plafonnés)."""
        with self.lock:
            items, self.buf = self.buf, []
        if not items:
            return
        groups = {}
        for when, line in items:
            groups.setdefault(when.strftime("%Y%m%d%H"), []).append((when, line))
        failed = []
        for batch in groups.values():
            try:
                self.writer.append(self.STREAM, [l for _, l in batch], when=batch[0][0])
                self.journal.count(self.STREAM, "messages", len(batch))
            except Exception as e:
                self.journal.log(f"mqtt écriture en échec ({e!r}), {len(batch)} messages gardés en mémoire")
                self.journal.count(self.STREAM, "write_fail")
                failed += batch
        if failed:
            with self.lock:
                self.buf[:0] = failed
                over = len(self.buf) - MQTT_MAX_BUFFER
                if over > 0:
                    del self.buf[:over]
                    self.journal.log(f"mqtt tampon plein : {over} plus anciens messages perdus")
                    self.journal.count(self.STREAM, "dropped", over)

    def _flush_loop(self):
        warned = 0.0
        while not self.stop.wait(10):
            try:
                self.flush()
                silent = time.monotonic() - self.last_msg
                if silent > 60 and time.monotonic() - warned > 60:
                    self.journal.log(f"mqtt silence depuis {int(silent)} s")
                    warned = time.monotonic()
            except Exception as e:  # ce fil ne doit jamais mourir en silence
                self.journal.log(f"mqtt boucle d'écriture exception {e!r}")
        try:
            self.flush()
        except Exception as e:
            self.journal.log(f"mqtt dernier flush en échec {e!r}")

    def close(self):
        if self.client:
            self.client.loop_stop()
            self.client.disconnect()
        self.flush()


# ----------------------------------------------------------------------------
# Garde-fous
# ----------------------------------------------------------------------------
def disk_guard(out, min_free_gb, stop, journal, failed, every=60):
    """Arrêt propre (code de sortie non nul) quand l'espace libre passe sous le seuil."""
    while not stop.wait(every):
        try:
            free = shutil.disk_usage(out).free / 1e9
            if free < min_free_gb:
                journal.log(f"espace disque {free:.1f} Go < {min_free_gb} Go : arrêt propre")
                failed.set()
                stop.set()
        except Exception as e:
            journal.log(f"disk_guard exception {e!r}")


def stats_loop(journal, stop, every=60):
    """Une ligne de compteurs cumulés par minute ; signale une veille ou un saut d'horloge."""
    last = time.time()
    while not stop.wait(every):
        try:
            now = time.time()
            gap = now - last - every
            if gap > CLOCK_GAP_S - every:
                journal.log(f"SAUT D'HORLOGE OU VEILLE : {int(now - last)} s entre deux relevés de stats")
            last = now
            journal.log("stats (cumul) " + journal.snapshot())
        except Exception as e:
            journal.log(f"stats_loop exception {e!r}")


def acquire_lock(path):
    """Interdit deux enregistreurs sur le même dossier. Garder l'objet renvoyé vivant."""
    f = open(path, "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        raise RuntimeError(f"un autre enregistreur utilise déjà {Path(path).parent}")
    return f


# ----------------------------------------------------------------------------
# Programme principal
# ----------------------------------------------------------------------------
def build_parser():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=str(Path.home() / "rerb-data"))
    ap.add_argument("--env", default=os.environ.get("RERB_ENV", "/Users/mathistelle/projets/rerb-live/.env"))
    ap.add_argument("--sources", default="mbta,hsl,prim")
    ap.add_argument("--duration", type=int, default=0, help="secondes, 0 = sans fin")
    ap.add_argument("--mbta-interval", type=float, default=12)
    ap.add_argument("--hsl-interval", type=float, default=15)
    ap.add_argument("--prim-interval", type=float, default=PRIM_MIN_INTERVAL)
    ap.add_argument("--prim-daily-cap", type=int, default=PRIM_DAILY_CAP)
    ap.add_argument("--quota-file", default=str(Path.home() / ".rerb-prim-quota.json"),
                    help="compteur d'appels PRIM, partagé entre tous les dossiers de sortie")
    ap.add_argument("--gares-interval", type=float, default=GARES_INTERVAL)
    ap.add_argument("--gares-daily-cap", type=int, default=GARES_DAILY_CAP)
    ap.add_argument("--gares-quota-file", default=str(Path.home() / ".rerb-prim-gares-quota.json"))
    ap.add_argument("--witness-interval", type=float, default=3600)
    ap.add_argument("--min-free-gb", type=float, default=5)
    return ap


def main(argv=None):
    ap = build_parser()
    a = ap.parse_args(argv)

    sources = {s.strip() for s in a.sources.split(",") if s.strip()}
    if "prim" in sources and not (a.prim_interval >= PRIM_MIN_INTERVAL):  # écrit ainsi pour refuser aussi nan
        ap.error(f"--prim-interval minimum {PRIM_MIN_INTERVAL} s (quota PRIM)")
    if "primgares" in sources and not (a.gares_interval >= 20):
        ap.error("--gares-interval minimum 20 s")
    env = load_env(a.env) if Path(a.env).exists() else {}
    for src, key in (("mbta", "MBTA_API_KEY"), ("prim", "PRIM_API_KEY"), ("primgares", "PRIM_API_KEY")):
        if src in sources:
            if not env.get(key):
                ap.error(f"{key} absente de {a.env}")
            if not valid_header_value(env[key]):
                ap.error(f"{key} contient un caractère non imprimable ou non ASCII")
    register_secrets([env.get("MBTA_API_KEY"), env.get("PRIM_API_KEY")])

    out = Path(a.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    locks = []  # objets à garder vivants : le verrou dure tant que le programme tourne
    try:
        locks.append(acquire_lock(out / ".lock"))
        if "prim" in sources or "primgares" in sources:  # un seul processus à la fois peut appeler PRIM (compteur de quota partagé)
            locks.append(acquire_lock(str(a.quota_file) + ".lock"))
    except RuntimeError as e:
        ap.error(str(e))
    journal = Journal(out / "recorder.log")
    writer = Writer(out)
    stop, failed = threading.Event(), threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, lambda *_: stop.set())
    journal.log(f"démarrage sources={sorted(sources)} out={out}")

    threads, mqtt_rec = [], None  # threads = liste de (nom, fil) surveillée par la boucle finale

    def add(name, interval, job, delay=0.0):
        t = threading.Thread(target=poll, args=(name, interval, job, stop, journal, delay),
                             daemon=True, name=name)
        t.start()
        threads.append((name, t))

    if "mbta" in sources:
        h = {"x-api-key": env["MBTA_API_KEY"]}
        add("mbta_vehicles", a.mbta_interval,
            http_job("mbta_vehicles", URL_MBTA_VEHICLES, h, writer, journal))
        add("mbta_predictions", a.mbta_interval,
            http_job("mbta_predictions", URL_MBTA_PREDICTIONS, h, writer, journal))
    if "hsl" in sources:
        add("hsl_trip_updates", a.hsl_interval,
            http_job("hsl_trip_updates", URL_HSL_TU, None, writer, journal, filter_trains,
                     witness_stream="hsl_witness_trip_updates", witness_every=a.witness_interval))
        add("hsl_witness_vehicle_positions", a.witness_interval,
            http_job("hsl_witness_vehicle_positions", URL_HSL_VP, None, writer, journal))
        mqtt_rec = MqttRecorder(writer, journal, stop)
        threads.append(("mqtt_flush", mqtt_rec.start()))
    if "prim" in sources:
        h = {"apikey": env["PRIM_API_KEY"]}
        quota = DailyQuota(a.quota_file, a.prim_daily_cap)
        add("prim_estimated_timetable", a.prim_interval,
            http_job("prim_estimated_timetable", URL_PRIM_ET, h, writer, journal, gate=quota.allow))
        add("prim_stop_monitoring", a.prim_interval,
            http_job("prim_stop_monitoring", URL_PRIM_SM, h, writer, journal, gate=quota.allow),
            delay=a.prim_interval / 2)  # décalé d'une demi-période pour étaler les appels
    if "primgares" in sources:
        gares = load_gares(GARES_CSV)
        if not gares:
            ap.error(f"aucune gare lue dans {GARES_CSV}")
        gq = DailyQuota(a.gares_quota_file, a.gares_daily_cap)
        add("prim_stop_monitoring_gares", a.gares_interval,
            gares_job("prim_stop_monitoring_gares", URL_PRIM_SM_GARE, gares, {"apikey": env["PRIM_API_KEY"]},
                      writer, journal, gate=gq.allow),
            delay=a.gares_interval / 3)
        journal.log(f"primgares : {len(gares)} gares toutes les {a.gares_interval:g} s")
    for name, target, args in (("disk_guard", disk_guard, (out, a.min_free_gb, stop, journal, failed)),
                               ("stats_loop", stats_loop, (journal, stop))):
        t = threading.Thread(target=target, args=args, daemon=True, name=name)
        t.start()
        threads.append((name, t))

    # Boucle de surveillance : si un fil meurt, on le dit dans le journal et le code de sortie sera 1.
    deadline = time.monotonic() + a.duration if a.duration else None
    dead = set()
    while not stop.is_set():
        timeout = 30 if deadline is None else min(30, deadline - time.monotonic())
        if timeout <= 0:
            break
        stop.wait(timeout)
        for name, t in threads:
            if not stop.is_set() and not t.is_alive() and name not in dead:
                dead.add(name)
                failed.set()
                journal.log(f"THREAD MORT {name}")
                journal.count("watchdog", "thread_mort")
    stop.set()
    for _, t in threads:
        t.join(timeout=35)
    if mqtt_rec:
        mqtt_rec.close()
    journal.log(("ARRÊT AVEC PROBLÈME" if failed.is_set() else "arrêt propre")
                + ", stats finales " + journal.snapshot())
    for lk in locks:
        lk.close()
    return 1 if failed.is_set() else 0


if __name__ == "__main__":
    sys.exit(main())
