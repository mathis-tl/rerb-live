"""Tâche 4, J5 : lecteur des messages de perturbation PRIM (`general-message`). AUCUN réseau : réponses fabriquées.

Structure d'après la documentation : profil SIRI France v1.8 (GeneralMessageDelivery > InfoMessage : RecordedAtTime,
ItemIdentifier, InfoMessageIdentifier, InfoChannelRef, ValidUntilTime, Content ; Content de type FrGeneralMessageStructure :
LineRef, StopPointRef, LineSection, Message > MessageText), références enveloppées dans {"value": ...} comme IDFM.
La structure exacte d'une vraie réponse est à confirmer par les appels réels (docs/recap-j5-perturbations.md).
"""
import unittest
from datetime import datetime, timezone

from perturbations import actifs_a, lire_perturbations

B, C = "STIF:Line::C01743:", "STIF:Line::C01727:"


def info(ident, texte, lignes=(B,), gares=(), canal="Perturbation", enregistre="2026-11-16T09:00:00.000Z",
         valide="2026-11-16T12:00:00.000Z", troncon=None, **extra):
    contenu = {"LineRef": [{"value": x} for x in lignes],
               "StopPointRef": [{"value": g} for g in gares],
               "Message": [{"MessageType": "textOnly", "MessageText": {"value": texte, "lang": "FR"}}]}
    if troncon:
        contenu["LineSection"] = [{"FirstStop": {"value": troncon[0]}, "LastStop": {"value": troncon[1]},
                                   "LineRef": {"value": lignes[0]}}]
    contenu.update(extra)
    return {"RecordedAtTime": enregistre, "ItemIdentifier": ident,
            "InfoMessageIdentifier": {"value": ident}, "InfoChannelRef": {"value": canal},
            "ValidUntilTime": valide, "Content": contenu}


def reponse(*messages):
    return {"GeneralMessageDelivery": [{"InfoMessage": list(messages)}]}


class TestLirePerturbations(unittest.TestCase):
    def test_champs_de_base(self):
        r = lire_perturbations(reponse(info("m1", "Trafic ralenti", gares=["STIF:StopPoint:Q:1:", "STIF:StopPoint:Q:2:"])))
        self.assertEqual(len(r), 1)
        m = r[0]
        self.assertEqual((m["id"], m["message"], m["canal"]), ("m1", "Trafic ralenti", "Perturbation"))
        self.assertEqual(m["enregistre"], datetime(2026, 11, 16, 9, 0, tzinfo=timezone.utc))
        self.assertEqual(m["valide_jusqua"], datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc))
        self.assertEqual(m["gares"], ["STIF:StopPoint:Q:1:", "STIF:StopPoint:Q:2:"])
        self.assertEqual(m["lignes"], [B])
        self.assertEqual(m["troncons"], [])
        self.assertIsNone(m["gravite"])           # general-message n'a pas de gravité dans le profil (SX seulement)

    def test_date_sans_fuseau_lue_en_utc_et_comparable(self):
        r = lire_perturbations(reponse(info("m1", "x", enregistre="2026-11-16T09:00:00", valide="2026-11-16T12:00:00")))
        self.assertEqual(r[0]["enregistre"], datetime(2026, 11, 16, 9, 0, tzinfo=timezone.utc))
        self.assertEqual(len(actifs_a(r, datetime(2026, 11, 16, 10, 0, tzinfo=timezone.utc))), 1)

    def test_filtre_ligne(self):
        r = lire_perturbations(reponse(info("m1", "RER B", lignes=(B,)), info("m2", "RER C", lignes=(C,)),
                                       info("m3", "B et C", lignes=(C, B))))
        self.assertEqual([m["id"] for m in r], ["m1", "m3"])

    def test_ligne_none_garde_tout(self):
        r = lire_perturbations(reponse(info("m1", "x", lignes=(C,))), ligne=None)
        self.assertEqual(len(r), 1)

    def test_troncon(self):
        r = lire_perturbations(reponse(info("m1", "Incident", troncon=("STIF:StopPoint:Q:10:", "STIF:StopPoint:Q:11:"))))
        self.assertEqual(r[0]["troncons"], [("STIF:StopPoint:Q:10:", "STIF:StopPoint:Q:11:")])

    def test_references_nues_ou_enveloppees(self):
        m = info("m1", "x")
        m["Content"]["LineRef"] = B                  # chaîne seule
        m["Content"]["StopPointRef"] = {"value": "STIF:StopPoint:Q:1:"}   # objet seul
        r = lire_perturbations(reponse(m))
        self.assertEqual((r[0]["lignes"], r[0]["gares"]), ([B], ["STIF:StopPoint:Q:1:"]))

    def test_gravite_si_presente(self):
        r = lire_perturbations(reponse(info("m1", "x", Severity="severe")))
        self.assertEqual(r[0]["gravite"], "severe")

    def test_fin_absente_et_message_vide(self):
        m = info("m1", "")
        del m["ValidUntilTime"]
        r = lire_perturbations(reponse(m))
        self.assertIsNone(r[0]["valide_jusqua"])
        self.assertEqual(r[0]["message"], "")

    def test_message_multiple_garde_le_premier_textuel(self):
        m = info("m1", "court")
        m["Content"]["Message"].append({"MessageType": "longMessage", "MessageText": {"value": "long"}})
        self.assertEqual(lire_perturbations(reponse(m))[0]["message"], "court")

    def test_type_reel_majuscules_prefere_texte_seul(self):
        """Réponse réelle du 17/11 : SHORT_MESSAGE puis TEXT_ONLY ; on garde le texte complet (TEXT_ONLY)."""
        m = info("m1", "x")
        m["Content"]["Message"] = [{"MessageType": "SHORT_MESSAGE", "MessageText": {"value": "court"}},
                                   {"MessageType": "TEXT_ONLY", "MessageText": {"value": "complet"}}]
        self.assertEqual(lire_perturbations(reponse(m))[0]["message"], "complet")

    def test_reponse_vide_ou_sans_message(self):
        self.assertEqual(lire_perturbations({"GeneralMessageDelivery": [{}]}), [])
        self.assertEqual(lire_perturbations({"GeneralMessageDelivery": [{"InfoMessage": None}]}), [])

    def test_structure_illisible(self):
        for mauvais in (None, {}, {"GeneralMessageDelivery": []}, {"GeneralMessageDelivery": "x"}, [], 3):
            with self.assertRaises(ValueError):
                lire_perturbations(mauvais)

    def test_message_mal_forme_ignore(self):
        r = lire_perturbations(reponse("pas un dict", info("m1", "ok"), {"Content": None}))
        self.assertEqual([m["id"] for m in r], ["m1"])

    def test_actifs_a(self):
        from perturbations import actifs_a
        r = lire_perturbations(reponse(info("m1", "a", valide="2026-11-16T10:00:00.000Z"),
                                       info("m2", "b", valide="2026-11-16T14:00:00.000Z"),
                                       info("m3", "c", enregistre="2026-11-16T13:00:00.000Z",
                                            valide="2026-11-16T14:00:00.000Z")))
        t = datetime(2026, 11, 16, 11, 0, tzinfo=timezone.utc)
        self.assertEqual([m["id"] for m in actifs_a(r, t)], ["m2"])


if __name__ == "__main__":
    unittest.main()
