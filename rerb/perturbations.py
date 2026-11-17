"""Tâche 4, J5 : lecteur des messages de perturbation PRIM (`general-message`, SIRI Lite, profil France).

• lire_perturbations : réponse (ServiceDelivery) -> messages concernant une ligne (RER B par défaut) : texte, canal,
                       enregistrement, fin de validité, gares (StopPointRef), tronçons (LineSection), gravité si présente.
• actifs_a           : messages valides à un instant donné (enregistré avant, valide jusqu'après).
Le profil general-message ne définit pas de gravité (elle existe dans Situation Exchange) : `gravite` vaut None sauf si
le champ `Severity` figure dans le contenu. Bibliothèque standard seulement ; aucun réseau ici.
"""
from datetime import datetime, timezone

LIGNE = "STIF:Line::C01743:"


def _valeur(x):
    return x.get("value") if isinstance(x, dict) else x


def _liste(x):
    if x is None:
        return []
    return x if isinstance(x, list) else [x]


def _refs(x):
    return [v for v in (_valeur(e) for e in _liste(x)) if isinstance(v, str) and v]


def _heure(texte):
    if not isinstance(texte, str) or not texte:
        return None
    try:
        d = datetime.fromisoformat(texte.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)   # sans fuseau : UTC (comparable à un instant aware)


def _texte(contenu):
    """Message texte seul (type `textOnly` du profil, `TEXT_ONLY` dans les réponses réelles, ou sans type), sinon le premier."""
    messages = [m for m in _liste(contenu.get("Message")) if isinstance(m, dict)]
    for m in messages:
        type_ = m.get("MessageType")
        if type_ is None or str(type_).replace("_", "").lower() == "textonly":
            t = _valeur(m.get("MessageText"))
            if isinstance(t, str):
                return t
    for m in messages:
        t = _valeur(m.get("MessageText"))
        if isinstance(t, str):
            return t
    return ""


def _troncons(contenu):
    sortie = []
    for s in _liste(contenu.get("LineSection")):
        if isinstance(s, dict):
            a, b = _valeur(s.get("FirstStop")), _valeur(s.get("LastStop"))
            if a and b:
                sortie.append((a, b))
    return sortie


def lire_perturbations(reponse, ligne=LIGNE):
    """Réponse `general-message` (ServiceDelivery) -> liste de messages (dicts) ; `ligne=None` : toutes les lignes.
    Lève ValueError si la structure n'est pas celle d'une réponse general-message."""
    try:
        livraisons = reponse["GeneralMessageDelivery"]
        if not isinstance(livraisons, list) or not livraisons:
            raise TypeError
        messages = _liste(livraisons[0].get("InfoMessage"))
    except (KeyError, TypeError, AttributeError):
        raise ValueError("réponse general-message illisible") from None
    sortie = []
    for m in messages:
        if not isinstance(m, dict) or not isinstance(m.get("Content"), dict):
            continue
        c = m["Content"]
        lignes = _refs(c.get("LineRef"))
        if ligne is not None and ligne not in lignes:
            continue
        gravite = _valeur(c.get("Severity"))
        sortie.append({
            "id": _valeur(m.get("InfoMessageIdentifier")) or m.get("ItemIdentifier"),
            "canal": _valeur(m.get("InfoChannelRef")),
            "enregistre": _heure(m.get("RecordedAtTime")),
            "valide_jusqua": _heure(m.get("ValidUntilTime")),
            "message": _texte(c),
            "lignes": lignes,
            "gares": _refs(c.get("StopPointRef")),
            "troncons": _troncons(c),
            "gravite": gravite if isinstance(gravite, str) else None,
        })
    return sortie


def actifs_a(messages, instant):
    """Messages enregistrés au plus tard à `instant` et valides après (fin absente = sans limite connue)."""
    return [m for m in messages
            if (m["enregistre"] is None or m["enregistre"] <= instant)
            and (m["valide_jusqua"] is None or m["valide_jusqua"] > instant)]
