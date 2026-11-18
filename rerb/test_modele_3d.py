"""Modèle 3D de la rame Z 8100 (web/modele-z8100.js) : cotes de la fiche respectées, format de maillage attendu par deck.gl.

Le modèle (validé le 17/11) est utilisé par web/carte.js. Ces tests tournent avec `node`, comme ceux de test_web.py.
"""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

MODELE = Path(__file__).resolve().parent.parent / "web" / "modele-z8100.js"


@unittest.skipUnless(shutil.which("node"), "node absent")
class TestModele3D(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        code = f"""
        const M = require({json.dumps(str(MODELE))}); const r = M.construire();
        const mn = [1e9, 1e9, 1e9], mx = [-1e9, -1e9, -1e9]; let sommets = 0, indexes = 0, normalesFausses = 0, nan = 0;
        for (const p of r.pieces) {{
          const P = p.maillage.positions.value, N = p.maillage.normals.value;
          if (p.maillage.indices) indexes++;
          sommets += P.length / 3;
          for (let i = 0; i < P.length; i += 3) for (let k = 0; k < 3; k++) {{ if (!Number.isFinite(P[i + k])) nan++; mn[k] = Math.min(mn[k], P[i + k]); mx[k] = Math.max(mx[k], P[i + k]); }}
          for (let i = 0; i < N.length; i += 3) if (Math.abs(Math.hypot(N[i], N[i + 1], N[i + 2]) - 1) > 1e-6) normalesFausses++;
        }}
        console.log(JSON.stringify({{ longueur: mx[0] - mn[0], largeur: mx[1] - mn[1], hauteur: mx[2], bas: mn[2], centre: (mx[0] + mn[0]) / 2,
          sommets, indexes, normalesFausses, nan, pieces: r.pieces.map(p => p.nom), cotes: r.cotes }}));
        """
        sortie = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=60, check=True).stdout
        cls.r = json.loads(sortie)

    def test_dimensions_de_la_fiche(self):
        self.assertAlmostEqual(self.r["longueur"], 104.050, places=3)
        self.assertAlmostEqual(self.r["largeur"], 2.798, places=3)
        self.assertAlmostEqual(self.r["hauteur"], 4.170, places=3)
        self.assertAlmostEqual(self.r["bas"], 0.0, places=3)      # les roues touchent le rail
        self.assertAlmostEqual(self.r["centre"], 0.0, places=3)   # origine au milieu de la rame

    def test_composition_de_la_fiche(self):
        c = self.r["cotes"]
        self.assertAlmostEqual(2 * c["longueurMotrice"] + 2 * c["longueurRemorque"], c["longueurRame"], places=3)

    def test_maillage_sans_indices_et_sain(self):
        # deck.gl ignore `indices` pour un maillage donné en simple objet : on exporte des triangles dépliés (voir modele.js)
        self.assertEqual(self.r["indexes"], 0)
        self.assertEqual(self.r["nan"], 0)
        self.assertEqual(self.r["normalesFausses"], 0)
        self.assertGreater(self.r["sommets"], 1000)

    def test_pieces_attendues(self):
        for nom in ("blanc", "sombre", "ardoise", "jupe", "toit", "vitre", "rouge", "magenta", "vert", "noir", "phare", "int", "siege", "parebrise"):
            self.assertIn(nom, self.r["pieces"])


class TestModeleDezoome(unittest.TestCase):
    """Version dézoomée : mêmes cotes que la version détaillée, très peu de polygones."""

    def test_memes_cotes_et_peu_de_triangles(self):
        code = """
        const M = require('./web/modele-z8100.js');
        const mesure = (o) => { const r = M.construire(o); let n = 0, mn = [1e9,1e9,1e9], mx = [-1e9,-1e9,-1e9], nan = 0;
          for (const p of r.pieces) { const v = p.maillage.positions.value; n += v.length / 9;
            for (let i = 0; i < v.length; i += 3) for (let k = 0; k < 3; k++) { if (!Number.isFinite(v[i+k])) nan++; mn[k] = Math.min(mn[k], v[i+k]); mx[k] = Math.max(mx[k], v[i+k]); } }
          return { n, l: mx[0]-mn[0], w: mx[1]-mn[1], h: mx[2], nan }; };
        console.log(JSON.stringify({ d: mesure({}), s: mesure({ simple: true }) }));
        """
        r = json.loads(subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=60, check=True,
                                      cwd=str(Path(__file__).resolve().parent.parent)).stdout)
        for cle in ("l", "w", "h"):
            self.assertAlmostEqual(r["s"][cle], r["d"][cle], places=3)
        self.assertEqual(r["s"]["nan"], 0)
        self.assertLess(r["s"]["n"], r["d"]["n"] / 5)
        self.assertLess(r["s"]["n"], 5000)


class TestModeleIcone(unittest.TestCase):
    """Version icône : longueur vraie, rame élargie et rehaussée, disque dessous, peu de polygones."""

    def test_icone(self):
        code = """
        const M = require('./web/modele-z8100.js'); const r = M.construire({ icone: true }); let n = 0, nan = 0, mn = [1e9,1e9], mx = [-1e9,-1e9];
        for (const p of r.pieces) { const v = p.maillage.positions.value; n += v.length / 9;
          for (let i = 0; i < v.length; i += 3) { for (let k = 0; k < 3; k++) if (!Number.isFinite(v[i+k])) nan++; }
          if (p.nom !== 'disque') for (let i = 0; i < v.length; i += 3) { mn[0] = Math.min(mn[0], v[i]); mx[0] = Math.max(mx[0], v[i]); } }
        console.log(JSON.stringify({ n, nan, l: mx[0] - mn[0], pieces: r.pieces.map(p => p.nom) }));
        """
        r = json.loads(subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=60, check=True,
                                      cwd=str(Path(__file__).resolve().parent.parent)).stdout)
        self.assertEqual(r["nan"], 0)
        self.assertAlmostEqual(r["l"], 104.050, places=3)   # la longueur reste vraie (position sur la voie juste)
        self.assertIn("disque", r["pieces"])
        self.assertLess(r["n"], 15000)


class TestModeleParCaisse(unittest.TestCase):
    """Une caisse seule, centrée sur elle-même : la carte pose chaque caisse sur la voie pour que la rame se plie dans les courbes."""

    CODE = """
    const M = require('./web/modele-z8100.js');
    const sommets = o => { const out = []; let n = 0, mn = 1e9, mx = -1e9;
      for (const p of M.construire(o).pieces) { const v = p.maillage.positions.value; n += v.length / 9;
        for (let i = 0; i < v.length; i += 3) { mn = Math.min(mn, v[i]); mx = Math.max(mx, v[i]); out.push(p.nom + [v[i], v[i+1], v[i+2]].map(a => a.toFixed(3)).join(',')); } }
      return { out, n, mn, mx }; };
    const tourne = s => s.out.map(l => { const nom = l.replace(/[-0-9.,]+$/, ''); const [x, y, z] = l.slice(nom.length).split(',').map(Number); return nom + [-x, -y, z].map(a => a.toFixed(3)).join(','); }).sort();
    const entier = sommets({}), c = [0, 1, 2, 3].map(k => sommets({ voiture: k }));
    console.log(JSON.stringify({
      n: [entier.n, c.map(s => s.n)], x: c.map(s => [s.mn, s.mx]),
      queue: JSON.stringify(c[3].out.slice().sort()) === JSON.stringify(tourne(c[0])), remorque: JSON.stringify(c[2].out.slice().sort()) === JSON.stringify(tourne(c[1])),
    }));
    """

    def test_caisses_centrees_et_identiques_a_la_rame(self):
        r = json.loads(subprocess.run(["node", "-e", self.CODE], capture_output=True, text=True, timeout=120, check=True,
                                      cwd=str(Path(__file__).resolve().parent.parent)).stdout)
        entier, par_caisse = r["n"]
        self.assertEqual(sum(par_caisse), entier, "les quatre caisses reconstituent exactement la rame")
        for (mn, mx), longueur in zip(r["x"], (26.025, 26.0, 26.0, 26.025)):
            self.assertAlmostEqual(mx - mn, longueur, delta=0.02)
            self.assertAlmostEqual((mx + mn) / 2, 0, delta=0.02, msg="caisse centrée sur elle-même")
        self.assertTrue(r["queue"], "la motrice de queue est la motrice de tête tournée de 180°")
        self.assertTrue(r["remorque"], "la 2e remorque est la 1re tournée de 180°")


if __name__ == "__main__":
    unittest.main()
