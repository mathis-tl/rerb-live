"""Étape 3 du plan docs/plan-sejour-quai.md : écart de moins de 30 s toléré, sans saut de position.

Quand PRIM déplace l'heure d'arrivée d'une gare, le train ne saute pas : l'arrivée « adoptée » reste telle quelle si l'écart avec la nouvelle
heure PRIM est d'au plus 30 s, sinon elle s'en rapproche (zone de ±30 s autour de la nouvelle heure) à 0,2 s par seconde au plus.
Décor : origine X (départ 0 s), gare B (arrivée 300 s), terminus Z (arrivée 700 s), 1 000 m par tronçon.
"""
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


def arrivee(t, gare="B"):
    return next(p.arrivee for p in t.passages if p.gare == gare)


def depart(t, gare="B"):
    return next(p.depart for p in t.passages if p.gare == gare)


class TestLisseur(unittest.TestCase):

    def test_premiere_observation_valeur_prim_inchangee(self):
        t = train(300)
        self.assertEqual(lisseur().appliquer("C1", t, h(10)), t)

    def test_ecart_de_20_s_ignore(self):
        l = lisseur()
        l.appliquer("C1", train(300), h(10))
        t = l.appliquer("C1", train(320), h(130))
        self.assertEqual(arrivee(t), h(300))                     # on garde l'arrivée adoptée
        self.assertEqual(depart(t), h(360))                      # le départ suit le même décalage (nul)

    def test_ecart_de_30_s_pile_ignore(self):
        l = lisseur()
        l.appliquer("C1", train(300), h(10))
        self.assertEqual(arrivee(l.appliquer("C1", train(330), h(130))), h(300))

    def test_ecart_de_90_s_absorbe_en_rampe(self):
        l = lisseur()
        l.appliquer("C1", tr(3000), h(10))
        t = l.appliquer("C1", tr(3090), h(20))                   # 10 s plus tard : au plus 2 s de rapprochement
        self.assertEqual(arrivee(t), h(3002))
        t = l.appliquer("C1", tr(3090), h(70))                   # 50 s de plus : 10 s
        self.assertEqual(arrivee(t), h(3012))
        t = l.appliquer("C1", tr(3090), h(1000))                 # longtemps après : jusqu'au bord de la zone (3090 - 30), pas au-delà
        self.assertEqual(arrivee(t), h(3060))
        t = l.appliquer("C1", tr(3090), h(1100))
        self.assertEqual(arrivee(t), h(3060))

    def test_ecart_negatif_meme_chose_dans_l_autre_sens(self):
        l = lisseur()
        l.appliquer("C1", tr(3000), h(10))
        t = l.appliquer("C1", tr(2910), h(60))                   # zone [2880, 2940] : 3000 -> 2990 en 50 s
        self.assertEqual(arrivee(t), h(2990))
        self.assertEqual(arrivee(l.appliquer("C1", tr(2910), h(2000))), h(2940))

    def test_le_depart_suit_le_meme_decalage_que_l_arrivee(self):
        l = lisseur()
        l.appliquer("C1", tr(3000), h(10))
        t = l.appliquer("C1", tr(3090), h(60))                   # adoptée 3010 ; PRIM 3090 -> décalage -80 sur l'arrivée ET le départ
        self.assertEqual(arrivee(t), h(3010))
        self.assertEqual(depart(t), h(3150 - 80))

    def test_plusieurs_releves_successifs(self):
        l = lisseur()
        l.appliquer("C1", tr(3000), h(0))
        l.appliquer("C1", tr(3100), h(50))                       # adoptée 3010 (vers 3070) ; +100 s seulement : au-delà de 120 s le train serait retenu (TestRetenue)
        t = l.appliquer("C1", tr(2900), h(100))                  # PRIM change encore : zone [2870, 2930], on repart de 3010 vers 2930
        self.assertEqual(arrivee(t), h(3000))                    # 3010 - 0,2 x 50
        t = l.appliquer("C1", tr(2900), h(1000))
        self.assertEqual(arrivee(t), h(2930))

    def test_course_disparue_puis_revenue_repart_de_prim(self):
        l = lisseur()
        l.appliquer("C1", tr(3000), h(0))
        l.appliquer("C1", tr(3090), h(20))
        l.purger({"AUTRE"})
        self.assertEqual(arrivee(l.appliquer("C1", tr(3090), h(40))), h(3090))

    def test_purge_ne_touche_que_les_courses_absentes(self):
        l = lisseur()
        l.appliquer("C1", tr(3000), h(0))
        l.appliquer("C2", tr(3000), h(0))
        l.purger({"C1"})
        self.assertEqual(arrivee(l.appliquer("C1", tr(3090), h(20))), h(3004))   # mémoire conservée (0,2 x 20 s)
        self.assertEqual(arrivee(l.appliquer("C2", tr(3090), h(20))), h(3090))   # mémoire effacée : valeur PRIM

    def test_une_fois_arrive_le_decalage_est_fige(self):
        # arrivée adoptée 300 ; à t = 310 le train est arrivé : un nouveau changement de PRIM ne déplace plus ce passage
        l = lisseur()
        l.appliquer("C1", train(300), h(0))
        t = l.appliquer("C1", train(500), h(310))
        self.assertEqual(arrivee(t), h(300))
        t = l.appliquer("C1", train(600), h(2000))
        self.assertEqual(arrivee(t), h(300))
        self.assertEqual(depart(t), h(360))                       # départ suit : 660 - (600 - 300)

    def test_horloge_qui_recule_ne_deplace_rien(self):
        l = lisseur()
        l.appliquer("C1", train(300), h(100))
        self.assertEqual(arrivee(l.appliquer("C1", train(390), h(50))), h(300))

    def test_jamais_d_incoherence_creee_par_le_lissage(self):
        # propriété : PRIM cohérent à chaque relevé (Z au moins 90 s après le départ de B) -> le train lissé n'est jamais « non placé »
        import random
        hasard = random.Random(7)
        for graine in range(30):
            l = lisseur()
            b, z = 3000 + hasard.randint(0, 200), 3500 + hasard.randint(0, 200)
            for k in range(40):
                b = max(2000, b + hasard.choice((0, 0, 20, -20, 80, -80, 200, -200)))
                z = max(b + 60 + 90, z + hasard.choice((0, 0, 20, -20, 80, -80, 200, -200)))
                vu = l.appliquer("C1", train(b, arr_z=z), h(120 * k))
                for dt in (0, 30, 60, 119):
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
            self.assertIs(l.appliquer("C1", brut, h(t_s), recu=h(t_s)), brut)
        self.assertEqual(estimer(brut, h(100), distance).etat, Etat.EN_ROUTE)

    def test_passages_sans_arrivee_ignores_et_train_vide(self):
        l = lisseur()
        t = Train([Passage("X", None, h(0))], h(0))
        self.assertEqual(l.appliquer("C1", t, h(1)), t)
        self.assertEqual(l.appliquer("C2", Train([], None), h(1)), Train([], None))

    def test_pas_de_saut_de_position_sur_un_changement_de_90_s(self):
        # PRIM repousse l'arrivée à B de 300 s à 700 s en pleine route : sans lissage la position saute, avec lissage non
        def positions(lisseur):
            pos = []
            for t_s in range(3, 600, 3):
                prim = train(300 if t_s < 100 else 700, arr_z=1100)
                vu = lisseur.appliquer("C1", prim, h(t_s)) if lisseur else prim
                e = estimer(vu, h(t_s), distance)
                pos.append(e.distance_m if e.etat == Etat.EN_ROUTE and e.gare == "X" else None)
            return pos

        def saut_max(pos):
            return max(abs(b - a) for a, b in zip(pos, pos[1:]) if a is not None and b is not None)

        self.assertGreater(saut_max(positions(None)), 3 * 33.0, "le décor doit produire un saut sans lissage")
        self.assertLessEqual(saut_max(positions(lisseur())), 3 * 33.0)

    def test_modifie_et_duree_de_suivi(self):
        l = lisseur()
        l.appliquer("C1", train(300), h(0), recu=h(0))
        l.appliquer("C1", train(300), h(120), recu=h(120))
        self.assertFalse(l.modifie("C1"))
        self.assertEqual(l.suivi_s("C1"), 120.0)
        l.appliquer("C1", train(305), h(240), recu=h(240))
        self.assertTrue(l.modifie("C1"))                         # un changement, même sous la tolérance de 30 s, prouve qu'il y a du temps réel
        self.assertEqual(l.suivi_s("inconnue"), 0.0)
        self.assertFalse(l.modifie("inconnue"))

    def test_suivi_compte_les_releves_prim_pas_les_requetes(self):
        # revue du 18/11 : si PRIM tombe, le même relevé est servi à chaque requête ; 10 min de requêtes ne sont pas 10 min d'observation
        l = lisseur()
        for t_s in range(0, 700, 5):
            l.appliquer("C1", train(3000), h(t_s), recu=h(0))
        self.assertEqual(l.suivi_s("C1"), 0.0)
        l.appliquer("C1", train(3000), h(700), recu=h(690))
        self.assertEqual(l.suivi_s("C1"), 690.0)

    def test_acces_concurrents_sans_erreur(self):
        l = lisseur()
        erreurs = []

        def boucle(k):
            try:
                for i in range(200):
                    l.appliquer(f"C{k}", train(300 + (i % 7) * 40), h(i * 3))
                    l.purger({f"C{k}"})
            except Exception as e:  # pragma: no cover
                erreurs.append(e)
        fils = [threading.Thread(target=boucle, args=(k,)) for k in range(4)]
        for f in fils:
            f.start()
        for f in fils:
            f.join()
        self.assertEqual(erreurs, [])


class TestReglagesLivres(unittest.TestCase):
    """Décisions de Mathis (18/11) : tolérance 15 s ; le train arrive en moyenne plus tard que l'estimé (PRIM : biais −26 s à Palaiseau), +10 s pour commencer."""

    def test_valeurs_par_defaut(self):
        self.assertEqual((TOLERANCE_S, BIAIS_ARRIVEE_S), (15.0, 10.0))
        l = Lisseur()
        self.assertEqual(l.adoptee("C1", "B"), None)

    def test_biais_decale_l_arrivee_et_le_depart_de_chaque_passage_avec_arrivee(self):
        t = Lisseur().appliquer("C1", train(300), h(10))
        self.assertEqual((arrivee(t, "B"), depart(t, "B")), (h(310), h(370)))
        self.assertEqual((arrivee(t, "Z"), depart(t, "Z")), (h(710), None))      # terminus : arrivée décalée, pas de départ inventé
        self.assertEqual((t.passages[0].arrivee, t.passages[0].depart), (None, h(0)))   # origine sans arrivée : inchangée

    def test_biais_zero_laisse_prim_inchange(self):
        t = train(300)
        self.assertEqual(Lisseur(biais_s=0.0).appliquer("C1", t, h(10)), t)

    def test_tolerance_15_s_pile_ignoree_16_s_absorbee(self):
        l = Lisseur(biais_s=0.0)
        l.appliquer("C1", tr(3000), h(10))
        self.assertEqual(arrivee(l.appliquer("C1", tr(3015), h(130))), h(3000))
        self.assertEqual(arrivee(l.appliquer("C1", tr(3016), h(131))), h(3000) + timedelta(seconds=0.2))

    def test_la_tolerance_s_applique_autour_de_l_heure_biaisee(self):
        l = Lisseur()
        l.appliquer("C1", tr(3000), h(10))                        # adoptée 3010
        self.assertEqual(arrivee(l.appliquer("C1", tr(3012), h(130))), h(3010))   # PRIM + 10 = 3022 : écart 12 s, ignoré
        t = l.appliquer("C1", tr(3100), h(140))                  # PRIM + 10 = 3110 : écart 100 s, rampe de 0,2 x 10 s
        self.assertEqual(arrivee(t), h(3012))

    def test_une_fois_arrive_le_train_n_est_plus_decale(self):
        l = Lisseur()
        l.appliquer("C1", train(300), h(0))                       # adoptée 310
        t = l.appliquer("C1", train(500), h(400))
        self.assertEqual(arrivee(t), h(310))

    def test_garde_fou_ne_reecrit_pas_un_chevauchement_fourni_par_prim_meme_avec_le_biais(self):
        brut = Train([Passage("X", None, h(0)), Passage("B", h(180), h(240)), Passage("C", h(210), None)], h(0))
        l = Lisseur()
        for t_s in (10, 130, 250):
            vu = l.appliquer("C1", brut, h(t_s), recu=h(t_s))
            self.assertEqual([p.arrivee for p in vu.passages[1:]], [h(190), h(220)])   # simple décalage de 10 s, rien de « réparé »
        self.assertNotEqual(estimer(vu, h(100), distance).etat, Etat.NON_PLACE)

    def test_jamais_d_incoherence_creee_avec_les_reglages_livres(self):
        import random
        hasard = random.Random(11)
        for graine in range(30):
            l = Lisseur()
            b, z = 3000 + hasard.randint(0, 200), 3500 + hasard.randint(0, 200)
            for k in range(40):
                b = max(2000, b + hasard.choice((0, 0, 20, -20, 80, -80, 200, -200)))
                z = max(b + 60 + 90, z + hasard.choice((0, 0, 20, -20, 80, -80, 200, -200)))
                vu = l.appliquer("C1", train(b, arr_z=z), h(120 * k))
                for dt in (0, 30, 60, 119):
                    self.assertNotEqual(estimer(vu, h(120 * k + dt), distance).etat, Etat.NON_PLACE, (graine, k, dt))


if __name__ == "__main__":
    unittest.main()


class TestRetenue(unittest.TestCase):
    """Décision du 21/11 (idée de Mathis, « pleine voie ») : PRIM repousse l'arrivée de plus de 120 s alors que le train roule -> le train
    continue à sa vitesse jusqu'à ~150 m de la gare, y attend, puis repart pour arriver à la nouvelle heure PRIM. Voir position.estimer."""

    def servi(self, ls, arr_b, maintenant):
        return ls.appliquer("C1", train(arr_b, arr_z=arr_b + 400), h(maintenant))

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
