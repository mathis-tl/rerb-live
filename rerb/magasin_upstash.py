"""Magasin Upstash (T5) : la même interface que MagasinLocal, derrière l'API REST d'Upstash.

Bibliothèque standard seulement (urllib). Chaque commande est un POST JSON `["CMD", argument, ...]` sur l'URL REST, avec
`Authorization: Bearer <jeton>`. Réponse `{"result": ...}`. Toute autre issue (HTTP non 200, réponse illisible, délai,
réseau) lève ErreurMagasin.

# INVARIANT : aucun message d'erreur ni journal ne contient l'URL ni le jeton (le message porte seulement le type d'échec).
# INVARIANT : pas de redirection suivie : le jeton ne part jamais vers un autre hôte.
# Clés : préfixe `rerb:`. Délai de 3 s par commande.
# À VÉRIFIER AVANT LA MISE EN PRODUCTION : la forme EVAL (`["EVAL", script, nombre, clés..., arguments...]`) et la réponse de
# SET NX refusé (`null`) n'ont pas été vérifiées contre la documentation Upstash ; les tests utilisent cette forme.
"""
import json
import urllib.error
import urllib.request
import uuid

from magasin import ErreurMagasin, Magasin

PREFIXE = "rerb:"
DELAI_S = 3
EXPIRATION_COMPTEUR_S = 48 * 3600     # un compteur de jour ne survit pas plus de 48 h

# Libère le verrou seulement si `ARGV[1]` (le jeton de l'appelant) en est encore le détenteur.
SCRIPT_LIBERER = "if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) else return 0 end"
# Ajoute 1 ; à la création du compteur seulement, pose l'expiration `ARGV[1]` (secondes).
SCRIPT_COMPTER = "local n = redis.call('INCR', KEYS[1]) if n == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end return n"


class _SansRedirection(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None   # la réponse 3xx devient une HTTPError, sans suivre


_OUVREUR = urllib.request.build_opener(_SansRedirection)


def _client_urllib(url, corps, entetes, delai_s):
    """-> (statut HTTP, octets). Lève OSError (délai, réseau) : MagasinUpstash ne garde que le type de l'erreur."""
    requete = urllib.request.Request(url, data=corps, headers=entetes, method="POST")
    try:
        with _OUVREUR.open(requete, timeout=delai_s) as reponse:
            return reponse.status, reponse.read()
    except urllib.error.HTTPError as erreur:
        return erreur.code, b""


def _entier(valeur):
    try:
        return int(valeur)
    except (TypeError, ValueError):
        raise ErreurMagasin("Upstash : valeur entière attendue") from None


class MagasinUpstash(Magasin):
    def __init__(self, url, jeton, http=None):
        self._url = url
        self._jeton = jeton
        self._http = http or _client_urllib

    @staticmethod
    def _cle(cle):
        return PREFIXE + cle

    def _commande(self, *args):
        corps = json.dumps(list(args)).encode("utf-8")
        entetes = {"Authorization": f"Bearer {self._jeton}", "Content-Type": "application/json"}
        try:
            statut, octets = self._http(self._url, corps, entetes, DELAI_S)
        except Exception as erreur:   # frontière réseau : le message d'origine peut porter l'URL, on ne le recopie pas
            raise ErreurMagasin(f"Upstash injoignable ({type(erreur).__name__})") from None
        if statut != 200:
            raise ErreurMagasin(f"Upstash : réponse HTTP {statut}") from None
        try:
            return json.loads(octets)["result"]
        except (ValueError, KeyError, TypeError):
            raise ErreurMagasin("Upstash : réponse illisible") from None

    def prendre_verrou(self, nom, duree_s):
        jeton = uuid.uuid4().hex
        resultat = self._commande("SET", self._cle("verrou:" + nom), jeton, "NX", "PX", int(duree_s * 1000))
        if resultat == "OK":
            return jeton
        if resultat is None:
            return None
        raise ErreurMagasin("Upstash : réponse inattendue à SET NX")

    def rendre_verrou(self, nom, jeton):
        self._commande("EVAL", SCRIPT_LIBERER, 1, self._cle("verrou:" + nom), jeton)

    def lire_compteur(self, cle):
        resultat = self._commande("GET", self._cle(cle))
        return 0 if resultat is None else _entier(resultat)

    def incrementer_compteur(self, cle):
        return _entier(self._commande("EVAL", SCRIPT_COMPTER, 1, self._cle(cle), EXPIRATION_COMPTEUR_S))

    def lire(self, cle):
        texte = self._commande("GET", self._cle(cle))
        if texte is None:
            return None
        try:
            return json.loads(texte)
        except (TypeError, ValueError):
            raise ErreurMagasin("Upstash : valeur JSON illisible") from None

    def ecrire(self, cle, valeur, ttl_s=None):
        args = ["SET", self._cle(cle), json.dumps(valeur, ensure_ascii=False)]
        if ttl_s is not None:
            args += ["EX", int(ttl_s)]
        if self._commande(*args) != "OK":
            raise ErreurMagasin("Upstash : écriture non confirmée")

    def lire_plusieurs(self, cles):
        if not cles:
            return []
        resultat = self._commande("MGET", *[self._cle(cle) for cle in cles])
        if not isinstance(resultat, list) or len(resultat) != len(cles):
            raise ErreurMagasin("Upstash : réponse MGET inattendue")
        valeurs = []
        for texte in resultat:
            if texte is None:
                valeurs.append(None)
                continue
            try:
                valeurs.append(json.loads(texte))
            except (TypeError, ValueError):
                raise ErreurMagasin("Upstash : valeur JSON illisible") from None
        return valeurs

    def ecrire_groupe(self, valeurs):
        if not valeurs:
            return
        args = ["MSET"]
        for cle, valeur in valeurs.items():
            args += [self._cle(cle), json.dumps(valeur, ensure_ascii=False)]
        if self._commande(*args) != "OK":
            raise ErreurMagasin("Upstash : écriture groupée non confirmée")
