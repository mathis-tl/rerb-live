"""Étape 3 du plan docs/plan-sejour-quai.md : écart de moins de 30 s toléré, sans saut de position.

Quand PRIM déplace l'heure d'arrivée d'une gare, le train ne saute pas : l'arrivée « adoptée » reste telle quelle si l'écart avec la nouvelle
heure PRIM est d'au plus 30 s, sinon elle s'en rapproche (zone de ±30 s autour de la nouvelle heure) à 0,2 s par seconde au plus.
Décor : origine X (départ 0 s), gare B (arrivée 300 s), terminus Z (arrivée 700 s), 1 000 m par tronçon.

T3 (24/11/2026) : `avancer` est appelé à chaque relevé PRIM (une fois), `appliquer` à chaque instant voulu (lecture seule). Quand une
valeur attendue change par rapport à l'ancien lissage (qui avançait à chaque requête), la différence est signalée dans le test : la rampe
démarre au relevé, et non à la requête précédente (écart de quelques secondes, nul à la cadence de 3 s).
"""
import json
import random
import threading
import unittest
from datetime import datetime, timedelta, timezone

from lissage import BIAIS_ARRIVEE_S, TOLERANCE_S, Lisseur
from position import Etat, Passage, Train, estimer

T0 = datetime(2026, 11, 18, 10, 0, 0, tzinfo=timezone.utc)


def h(s):
    return T0 + timedelta(seconds=s)


def distance(a, b):
    return 1000.0


def train(arr_b, arr_z=700, recorded=0, dep_b=None):
    return Train([Passage("X", None, h(0)), Passage("B", h(arr_b), h(arr_b + 60 if dep_b is None else dep_b)),
                  Passage("Z", h(arr_z), None)], h(recorded))


def lisseur(**reglages):
    """Réglage de référence des tests de mécanique : tolérance 30 s, aucun biais (les réglages livrés ont leurs propres tests plus bas)."""
    return Lisseur(**{"tolerance_s": 30.0, "biais_s": 0.0, **reglages})


def tr(arr_b, recorded=0):
    """Arrivée à B lointaine (le train n'y est pas encore) ; Z 400 s après B."""
    return train(arr_b, arr_z=arr_b + 400, recorded=recorded)


def releve(l, s, **courses):
    """Un relevé PRIM reçu à `s` secondes, avec les courses données (nom=Train). Toutes les courses du relevé doivent être passées ensemble."""
    l.avancer(courses, h(s))


def arrivee(t, gare="B"):
    return next(p.arrivee for p in t.passages if p.gare == gare)


def depart(t, gare="B"):
    return next(p.depart for p in t.passages if p.gare == gare)


class TestLisseur(unittest.TestCase):

    def test_premiere_observation_valeur_prim_inchangee(self):
        t = train(300)
        l = lisseur()
        releve(l, 10, C1=t)
        self.assertEqual(l.appliquer("C1", t, h(10)), t)

    def test_ecart_de_20_s_ignore(self):
        l = lisseur()
        releve(l, 10, C1=train(300))
        releve(l, 130, C1=train(320))
        t = l.appliquer("C1", train(320), h(130))
        self.assertEqual(arrivee(t), h(300))                     # on garde l'arrivée adoptée
        self.assertEqual(depart(t), h(360))                      # le départ suit le même décalage (nul)

    def test_ecart_de_30_s_pile_ignore(self):
        l = lisseur()
        releve(l, 10, C1=train(300))
        releve(l, 130, C1=train(330))
        self.assertEqual(arrivee(l.appliquer("C1", train(330), h(130))), h(300))

    def test_ecart_de_90_s_absorbe_en_rampe(self):
        # la rampe part du relevé de 20 s (et non de la requête précédente) : 0,2 s par seconde, plafonnée à la zone (3090 - 30)
        l = lisseur()
        releve(l, 10, C1=tr(3000))
        releve(l, 20, C1=tr(3090))
        self.assertEqual(arrivee(l.appliquer("C1", tr(3090), h(20))), h(3000))   # à l'instant du relevé : rien ne bouge encore
        t = l.appliquer("C1", tr(3090), h(30))
        self.assertEqual(arrivee(t), h(3002))                    # 10 s après le relevé : au plus 2 s de rapprochement
        t = l.appliquer("C1", tr(3090), h(70))                   # 50 s après le relevé : 10 s
        self.assertEqual(arrivee(t), h(3010))
        t = l.appliquer("C1", tr(3090), h(1000))                 # longtemps après : jusqu'au bord de la zone (3090 - 30), pas au-delà
        self.assertEqual(arrivee(t), h(3060))
        t = l.appliquer("C1", tr(3090), h(1100))
        self.assertEqual(arrivee(t), h(3060))

    def test_ecart_negatif_meme_chose_dans_l_autre_sens(self):
        l = lisseur()
        releve(l, 10, C1=tr(3000))
        releve(l, 60, C1=tr(2910))                               # zone [2880, 2940] : 3000 -> 2990 en 50 s après le relevé
        self.assertEqual(arrivee(l.appliquer("C1", tr(2910), h(60))), h(3000))
        self.assertEqual(arrivee(l.appliquer("C1", tr(2910), h(110))), h(2990))
        self.assertEqual(arrivee(l.appliquer("C1", tr(2910), h(2000))), h(2940))

    def test_le_depart_suit_le_meme_decalage_que_l_arrivee(self):
        l = lisseur()
        releve(l, 10, C1=tr(3000))
        releve(l, 60, C1=tr(3090))                               # adoptée 3000 à 60 s, 3010 à 110 s ; PRIM 3090 -> décalage -80 sur l'arrivée ET le départ
        t = l.appliquer("C1", tr(3090), h(110))
        self.assertEqual(arrivee(t), h(3010))
        self.assertEqual(depart(t), h(3150 - 80))

    def test_plusieurs_releves_successifs(self):
        l = lisseur()
        releve(l, 0, C1=tr(3000))
        releve(l, 50, C1=tr(3100))                               # rampe vers 3070 ; +100 s seulement : au-delà de 120 s le train serait retenu (TestRetenue)
        releve(l, 100, C1=tr(2900))                              # au relevé de 100 s : adoptée 3010 (rampe de 50 s) ; PRIM change encore : zone [2870, 2930]
        self.assertEqual(arrivee(l.appliquer("C1", tr(2900), h(100))), h(3010))
        self.assertEqual(arrivee(l.appliquer("C1", tr(2900), h(1000))), h(2930))   # on repart de 3010 vers 2930

    def test_course_disparue_puis_revenue_repart_de_prim(self):
        l = lisseur()
        releve(l, 0, C1=tr(3000))
        releve(l, 20, C1=tr(3090))
        releve(l, 30, AUTRE=tr(3000))                            # C1 n'est plus dans le relevé : oubliée
        releve(l, 40, C1=tr(3090))
        self.assertEqual(arrivee(l.appliquer("C1", tr(3090), h(40))), h(3090))

    def test_purge_ne_touche_que_les_courses_absentes(self):
        l = lisseur()
        releve(l, 0, C1=tr(3000), C2=tr(3000))
        releve(l, 20, C1=tr(3090))                               # C2 n'est plus dans le relevé : oubliée
        self.assertEqual(arrivee(l.appliquer("C1", tr(3090), h(40))), h(3004))   # mémoire conservée (0,2 x 20 s)
        self.assertEqual(arrivee(l.appliquer("C2", tr(3090), h(20))), h(3090))   # mémoire effacée : valeur PRIM

    def test_une_fois_arrive_le_decalage_est_fige(self):
        # arrivée adoptée 300 ; à t = 310 le train est arrivé : un nouveau changement de PRIM ne déplace plus ce passage
        l = lisseur()
        releve(l, 0, C1=train(300))
        releve(l, 310, C1=train(500))
        t = l.appliquer("C1", train(500), h(310))
        self.assertEqual(arrivee(t), h(300))
        releve(l, 2000, C1=train(600))
        t = l.appliquer("C1", train(600), h(2000))
        self.assertEqual(arrivee(t), h(300))
        self.assertEqual(depart(t), h(360))                       # départ suit : 660 - (600 - 300)

    def test_horloge_qui_recule_ne_deplace_rien(self):
        l = lisseur()
        releve(l, 100, C1=train(300))
        self.assertEqual(arrivee(l.appliquer("C1", train(390), h(50))), h(300))

    def test_jamais_d_incoherence_creee_par_le_lissage(self):
        # propriété : PRIM cohérent à chaque relevé (Z au moins 90 s après le départ de B) -> le train lissé n'est jamais « non placé »
        hasard = random.Random(7)
        for graine in range(30):
            l = lisseur()
            b, z = 3000 + hasard.randint(0, 200), 3500 + hasard.randint(0, 200)
            for k in range(40):
                b = max(2000, b + hasard.choice((0, 0, 20, -20, 80, -80, 200, -200)))
                z = max(b + 60 + 90, z + hasard.choice((0, 0, 20, -20, 80, -80, 200, -200)))
                releve(l, 120 * k, C1=train(b, arr_z=z))
                for dt in (0, 30, 60, 119):
                    vu = l.appliquer("C1", train(b, arr_z=z), h(120 * k + dt))
                    e = estimer(vu, h(120 * k + dt), distance)
                    self.assertNotEqual(e.etat, Etat.NON_PLACE, (graine, k, dt, e.motif))

    def test_garde_fou_repousse_une_arrivee_qui_precederait_le_depart_precedent(self):
        # appel direct : B décalée à 3200 (arrêt annoncé 60 s -> départ effectif 3200 + 25 s) alors que Z, non décalée, arrive à 3100 :
        # Z est repoussée au départ EFFECTIF de B (3225), pas à son départ annoncé (3260)
        anciens = [Passage("X", None, h(0)), Passage("B", h(3000), h(3060)), Passage("Z", h(3100), h(3400)), Passage("Y", h(3500), None)]
        nouveaux = [anciens[0], Passage("B", h(3200), h(3260)), anciens[2], anciens[3]]
        sortie = Lisseur._garantir_coherence(anciens, nouveaux)
        self.assertEqual((sortie[1].arrivee, sortie[1].depart), (h(3200), h(3260)))
        self.assertEqual((sortie[2].arrivee, sortie[2].depart), (h(3225), h(3400)))
        self.assertIs(sortie[3], anciens[3])
        # rien à corriger : mêmes objets
        self.assertTrue(all(a is b for a, b in zip(anciens, Lisseur._garantir_coherence(anciens, list(anciens)))))
        # heures manquantes : laissé tel quel (estimer refusera le train)
        vide = [Passage("X", None, None)]
        self.assertEqual(Lisseur._garantir_coherence(vide, vide), vide)

    def test_garde_fou_ne_reecrit_pas_un_chevauchement_fourni_par_prim(self):
        # revue du 18/11 : B part à 08:04:00 selon PRIM et C arrive à 08:03:30 -> chevauchement brut que la règle de séjour (départ 08:03:25) absorbe
        # dans estimer() ; le lisseur ne doit ni le « réparer » ni neutraliser cette règle
        brut = Train([Passage("X", None, h(0)), Passage("B", h(180), h(240)), Passage("C", h(210), None)], h(0))
        l = lisseur()
        for t_s in (10, 130, 250):
            releve(l, t_s, C1=brut)
            self.assertIs(l.appliquer("C1", brut, h(t_s)), brut)
        self.assertEqual(estimer(brut, h(100), distance).etat, Etat.EN_ROUTE)

    def test_passages_sans_arrivee_ignores_et_train_vide(self):
        l = lisseur()
        t = Train([Passage("X", None, h(0))], h(0))
        releve(l, 1, C1=t, C2=Train([], None))
        self.assertEqual(l.appliquer("C1", t, h(1)), t)
        self.assertEqual(l.appliquer("C2", Train([], None), h(1)), Train([], None))

    def test_pas_de_saut_de_position_sur_un_changement_de_90_s(self):
        # PRIM repousse l'arrivée à B de 300 s à 700 s en pleine route : sans lissage la position saute, avec lissage non
        def positions(lisseur):
            pos = []
            for t_s in range(3, 600, 3):
                prim = train(300 if t_s < 100 else 700, arr_z=1100)
                if lisseur:
                    lisseur.avancer({"C1": prim}, h(t_s))             # un relevé toutes les 3 s, comme la cadence de la replay
                    vu = lisseur.appliquer("C1", prim, h(t_s))
                else:
                    vu = prim
                e = estimer(vu, h(t_s), distance)
                pos.append(e.distance_m if e.etat == Etat.EN_ROUTE and e.gare == "X" else None)
            return pos

        def saut_max(pos):
            return max(abs(b - a) for a, b in zip(pos, pos[1:]) if a is not None and b is not None)

        self.assertGreater(saut_max(positions(None)), 3 * 33.0, "le décor doit produire un saut sans lissage")
        self.assertLessEqual(saut_max(positions(lisseur())), 3 * 33.0)

    def test_modifie_et_duree_de_suivi(self):
        l = lisseur()
        releve(l, 0, C1=train(300))
        releve(l, 120, C1=train(300))
        self.assertFalse(l.modifie("C1"))
        self.assertEqual(l.suivi_s("C1"), 120.0)
        releve(l, 240, C1=train(305))
        self.assertTrue(l.modifie("C1"))                         # un changement, même sous la tolérance de 30 s, prouve qu'il y a du temps réel
        self.assertEqual(l.suivi_s("inconnue"), 0.0)
        self.assertFalse(l.modifie("inconnue"))

    def test_suivi_compte_les_releves_prim_pas_les_requetes(self):
        # revue du 18/11 : si PRIM tombe, le même relevé est servi à chaque requête ; 10 min de requêtes ne sont pas 10 min d'observation
        l = lisseur()
        releve(l, 0, C1=train(3000))
        for t_s in range(0, 700, 5):
            l.appliquer("C1", train(3000), h(t_s))               # requêtes sans nouveau relevé : aucun effet sur le suivi
        self.assertEqual(l.suivi_s("C1"), 0.0)
        releve(l, 690, C1=train(3000))
        self.assertEqual(l.suivi_s("C1"), 690.0)

    def test_acces_concurrents_sans_erreur(self):
        l = lisseur()
        erreurs = []

        def boucle(k):
            try:
                for i in range(200):
                    releve(l, i * 3, **{f"C{k}": train(300 + (i % 7) * 40)})
                    l.appliquer(f"C{k}", train(300 + (i % 7) * 40), h(i * 3))
            except Exception as e:  # pragma: no cover
                erreurs.append(e)
        fils = [threading.Thread(target=boucle, args=(k,)) for k in range(4)]
        for f in fils:
            f.start()
        for f in fils:
            f.join()
        self.assertEqual(erreurs, [])

    def test_rampe_de_1_sans_division_par_zero(self):
        # --rampe 1,0 (suit PRIM sans lissage de vitesse) : pas de rampe qui s'arrête, la zone de tolérance borne seule
        l = lisseur(rampe=1.0)
        releve(l, 10, C1=tr(3000))
        releve(l, 20, C1=tr(3090))
        self.assertEqual(arrivee(l.appliquer("C1", tr(3090), h(30))), h(3000 + 10))   # 10 s après le relevé de 20 s, à 1 s/s
        self.assertEqual(arrivee(l.appliquer("C1", tr(3090), h(1000))), h(3060))     # bord de la zone : 3090 - 30


class TestReglagesLivres(unittest.TestCase):
    """Décisions de Mathis (18/11) : tolérance 15 s ; le train arrive en moyenne plus tard que l'estimé (PRIM : biais −26 s à Palaiseau), +10 s pour commencer."""

    def test_valeurs_par_defaut(self):
        self.assertEqual((TOLERANCE_S, BIAIS_ARRIVEE_S), (15.0, 10.0))
        l = Lisseur()
        self.assertEqual(l.adoptee("C1", "B"), None)

    def test_biais_decale_l_arrivee_et_le_depart_de_chaque_passage_avec_arrivee(self):
        l = Lisseur()
        releve(l, 10, C1=train(300))
        t = l.appliquer("C1", train(300), h(10))
        self.assertEqual((arrivee(t, "B"), depart(t, "B")), (h(310), h(370)))
        self.assertEqual((arrivee(t, "Z"), depart(t, "Z")), (h(710), None))      # terminus : arrivée décalée, pas de départ inventé
        self.assertEqual((t.passages[0].arrivee, t.passages[0].depart), (None, h(0)))   # origine sans arrivée : inchangée

    def test_biais_zero_laisse_prim_inchange(self):
        t = train(300)
        l = Lisseur(biais_s=0.0)
        releve(l, 10, C1=t)
        self.assertEqual(l.appliquer("C1", t, h(10)), t)

    def test_tolerance_15_s_pile_ignoree_16_s_absorbee(self):
        l = Lisseur(biais_s=0.0)
        releve(l, 10, C1=tr(3000))
        releve(l, 130, C1=tr(3015))
        self.assertEqual(arrivee(l.appliquer("C1", tr(3015), h(130))), h(3000))
        releve(l, 131, C1=tr(3016))                              # 16 s : la rampe démarre à ce relevé
        self.assertEqual(arrivee(l.appliquer("C1", tr(3016), h(131))), h(3000))
        self.assertEqual(arrivee(l.appliquer("C1", tr(3016), h(132))), h(3000) + timedelta(seconds=0.2))

    def test_la_tolerance_s_applique_autour_de_l_heure_biaisee(self):
        l = Lisseur()
        releve(l, 10, C1=tr(3000))                               # adoptée 3010
        releve(l, 130, C1=tr(3012))                              # PRIM + 10 = 3022 : écart 12 s, ignoré
        self.assertEqual(arrivee(l.appliquer("C1", tr(3012), h(130))), h(3010))
        releve(l, 140, C1=tr(3100))                              # PRIM + 10 = 3110 : écart 100 s, rampe de 0,2 x 10 s
        self.assertEqual(arrivee(l.appliquer("C1", tr(3100), h(150))), h(3012))

    def test_une_fois_arrive_le_train_n_est_plus_decale(self):
        l = Lisseur()
        releve(l, 0, C1=train(300))                              # adoptée 310
        releve(l, 400, C1=train(500))
        t = l.appliquer("C1", train(500), h(400))
        self.assertEqual(arrivee(t), h(310))

    def test_garde_fou_ne_reecrit_pas_un_chevauchement_fourni_par_prim_meme_avec_le_biais(self):
        brut = Train([Passage("X", None, h(0)), Passage("B", h(180), h(240)), Passage("C", h(210), None)], h(0))
        l = Lisseur()
        for t_s in (10, 130, 250):
            releve(l, t_s, C1=brut)
            vu = l.appliquer("C1", brut, h(t_s))
            self.assertEqual([p.arrivee for p in vu.passages[1:]], [h(190), h(220)])   # simple décalage de 10 s, rien de « réparé »
        self.assertNotEqual(estimer(vu, h(100), distance).etat, Etat.NON_PLACE)

    def test_jamais_d_incoherence_creee_avec_les_reglages_livres(self):
        hasard = random.Random(11)
        for graine in range(30):
            l = Lisseur()
            b, z = 3000 + hasard.randint(0, 200), 3500 + hasard.randint(0, 200)
            for k in range(40):
                b = max(2000, b + hasard.choice((0, 0, 20, -20, 80, -80, 200, -200)))
                z = max(b + 60 + 90, z + hasard.choice((0, 0, 20, -20, 80, -80, 200, -200)))
                releve(l, 120 * k, C1=train(b, arr_z=z))
                for dt in (0, 30, 60, 119):
                    vu = l.appliquer("C1", train(b, arr_z=z), h(120 * k + dt))
                    self.assertNotEqual(estimer(vu, h(120 * k + dt), distance).etat, Etat.NON_PLACE, (graine, k, dt))


class TestRetenue(unittest.TestCase):
    """Décision du 21/11 (idée de Mathis, « pleine voie ») : PRIM repousse l'arrivée de plus de 120 s alors que le train roule -> le train
    continue à sa vitesse jusqu'à ~150 m de la gare, y attend, puis repart pour arriver à la nouvelle heure PRIM. Voir position.estimer."""

    def servi(self, ls, arr_b, maintenant):
        t = train(arr_b, arr_z=arr_b + 400)
        releve(ls, maintenant, C1=t)
        return ls.appliquer("C1", t, h(maintenant))

    def etat(self, t, s):
        return estimer(t, h(s), distance)

    def test_revision_de_plus_de_120_s_vers_plus_tard_retient_le_train(self):
        ls = lisseur(tolerance_s=15.0)
        self.servi(ls, 300, 100)
        t = self.servi(ls, 520, 110)
        self.assertEqual(arrivee(t), h(520), "l'arrivée adoptée suit PRIM tout de suite (le train attend, pas de ralentissement progressif)")
        p = next(p for p in t.passages if p.gare == "B")
        self.assertEqual((p.retenu_depuis, p.retenu_le), (h(300), h(110)))

    def test_le_train_roule_a_sa_vitesse_puis_attend_en_voie_avant_la_gare(self):
        ls = lisseur(tolerance_s=15.0)
        self.servi(ls, 300, 100)
        t = self.servi(ls, 520, 110)
        e = self.etat(t, 200)
        self.assertEqual(e.etat, Etat.EN_ROUTE)
        self.assertAlmostEqual(e.fraction, 200 / 300, places=3, msg="même vitesse qu'avant la révision")
        self.assertFalse(e.retenu)
        for s in (270, 350, 440):
            e = self.etat(t, s)
            self.assertAlmostEqual(e.distance_m, 850.0, delta=0.5, msg="arrêté à 150 m de la gare")
            self.assertTrue(e.retenu)

    def test_il_repart_pour_arriver_a_la_nouvelle_heure_sans_saut(self):
        ls = lisseur(tolerance_s=15.0)
        self.servi(ls, 300, 100)
        t = self.servi(ls, 520, 110)
        precedent = None
        for s in range(111, 521):
            e = self.etat(t, s)
            if e.etat == Etat.EN_ROUTE:
                self.assertGreaterEqual(e.distance_m, precedent or 0, "le train ne recule jamais")
                self.assertLess(e.distance_m - (precedent or e.distance_m), 40, "pas de saut")
                precedent = e.distance_m
        self.assertEqual(self.etat(t, 521).etat, Etat.A_QUAI)
        self.assertFalse(self.etat(t, 521).retenu)

    def test_revision_de_120_s_ou_moins_garde_la_rampe(self):
        ls = lisseur(tolerance_s=15.0)
        self.servi(ls, 300, 100)
        t = self.servi(ls, 400, 110)
        p = next(p for p in t.passages if p.gare == "B")
        self.assertIsNone(p.retenu_depuis)
        self.assertLess(arrivee(t), h(400), "rampe : 0,2 s par seconde")
        self.assertFalse(self.etat(t, 290).retenu)

    def test_revision_vers_plus_tot_ne_retient_pas(self):
        ls = lisseur(tolerance_s=15.0)
        self.servi(ls, 600, 100)
        t = self.servi(ls, 300, 110)
        self.assertIsNone(next(p for p in t.passages if p.gare == "B").retenu_depuis)

    def test_train_deja_arrive_n_est_jamais_retenu(self):
        ls = lisseur(tolerance_s=15.0)
        self.servi(ls, 300, 100)
        t = self.servi(ls, 700, 310)
        self.assertIsNone(next(p for p in t.passages if p.gare == "B").retenu_depuis)

    def test_la_retenue_est_conservee_aux_releves_suivants(self):
        ls = lisseur(tolerance_s=15.0)
        self.servi(ls, 300, 100)
        self.servi(ls, 520, 110)
        t = self.servi(ls, 520, 140)
        p = next(p for p in t.passages if p.gare == "B")
        self.assertEqual((p.retenu_depuis, p.retenu_le), (h(300), h(110)))
        t = self.servi(ls, 530, 150)   # écart dans la tolérance : rien ne bouge
        self.assertEqual(arrivee(t), h(520))

    def test_revision_deja_depassee_au_moment_de_la_detection_arrete_le_train_la_ou_il_est(self):
        ls = lisseur(tolerance_s=15.0)
        self.servi(ls, 300, 100)
        t = self.servi(ls, 600, 290)   # le train a déjà passé les 150 m d'avant la gare (fraction 0,97)
        for s in (295, 400, 500):
            e = self.etat(t, s)
            self.assertGreaterEqual(e.distance_m, 850.0)
            self.assertLess(e.distance_m, 1000.0)
        self.assertTrue(self.etat(t, 400).retenu)


class TestRejeuRelevesEtRequetes(unittest.TestCase):
    """T3 : le résultat ne dépend ni de la cadence, ni de l'ordre, ni du nombre des requêtes (seulement des relevés)."""

    @staticmethod
    def relevés():
        # 40 relevés toutes les 30 s, avec des révisions de l'arrivée à B (parfois de plus de 120 s)
        return [(k * 30, train(3000 + (k * 37) % 200 - 100 + (300 if k % 9 == 4 else 0), arr_z=3900)) for k in range(40)]

    @staticmethod
    def rejouer(instants, releves):
        """Un lisseur nourri des relevés reçus avant chaque instant ; -> {instant: arrivée à B adoptée}, et le lisseur."""
        l, sortie, i, courant = Lisseur(), {}, 0, None
        for t in instants:
            while i < len(releves) and releves[i][0] <= t:
                courant = releves[i][1]
                l.avancer({"C1": courant}, h(releves[i][0]))
                i += 1
            if courant is not None:
                sortie[t] = arrivee(l.appliquer("C1", courant, h(t)))
        return sortie, l

    def test_meme_arrivee_quelle_que_soit_la_cadence_des_requetes(self):
        releves = self.relevés()
        suites = {
            "chaque seconde": list(range(0, 1200)),          # référence : couvre tous les instants des autres suites
            "toutes les 3 s": list(range(0, 1200, 3)),
            "toutes les 10 s, décalées": list(range(1, 1200, 10)),
            "par rafales": [t for t in range(0, 1200) if t % 50 < 2],
            "toutes les 7 s, décalées": list(range(5, 1200, 7)),
        }
        resultats = {nom: self.rejouer(suite, releves) for nom, suite in suites.items()}
        reference, _ = resultats["chaque seconde"]
        for nom, (sortie, _) in resultats.items():
            communs = set(sortie) & set(reference)
            self.assertGreater(len(communs), 20, nom)
            for t in communs:
                self.assertEqual(sortie[t], reference[t], (nom, t))

    def test_appliquer_ne_modifie_jamais_l_etat(self):
        releves = self.relevés()
        _, l = self.rejouer(range(0, 1200, 3), releves)
        avant = json.dumps(l.exporter(), sort_keys=True)
        for t in range(0, 1200, 2):
            l.appliquer("C1", releves[min(len(releves) - 1, t // 30)][1], h(t))
        self.assertEqual(json.dumps(l.exporter(), sort_keys=True), avant)


class TestSauvegardeLissage(unittest.TestCase):
    """T3 : exporter / importer (JSON pur, versionné) redonnent un lisseur au comportement identique."""

    def etat_riche(self):
        l = lisseur(tolerance_s=15.0)
        releve(l, 100, C1=train(300), C2=train(900))
        releve(l, 110, C1=train(520), C2=train(905))             # C1 : retenue ; C2 : rampe
        releve(l, 140, C1=train(520), C2=train(905), C3=train(400))
        releve(l, 400, C1=train(520), C2=train(905), C3=train(400))   # C1 arrivée passée : figée
        return l

    def test_exporter_est_du_json_pur(self):
        d = self.etat_riche().exporter()
        self.assertEqual(json.loads(json.dumps(d)), d)
        self.assertEqual(d["v"], 1)

    def test_round_trip_donne_le_meme_comportement(self):
        original = self.etat_riche()
        copie = Lisseur()
        copie.importer(json.loads(json.dumps(original.exporter())))
        self.assertEqual(copie.exporter(), original.exporter())
        for t in range(90, 700, 7):
            for ref, tt in (("C1", train(520)), ("C2", train(905)), ("C3", train(400))):
                self.assertEqual(copie.appliquer(ref, tt, h(t)), original.appliquer(ref, tt, h(t)), (ref, t))
        self.assertEqual(copie.modifie("C1"), original.modifie("C1"))
        self.assertEqual(copie.suivi_s("C2"), original.suivi_s("C2"))

    def test_importer_refuse_un_format_inconnu(self):
        with self.assertRaises(ValueError):
            Lisseur().importer({"v": 99})


if __name__ == "__main__":
    unittest.main()
