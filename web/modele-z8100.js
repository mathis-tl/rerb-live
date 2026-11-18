// Modèle 3D de la rame Z 8100 (MI79), validé par Mathis le 17/11/2026 et utilisé par web/carte.js.
//
// Sources (fiche http://fbrisou.free.fr/RAIL21/FicheZ8100.pdf, page 4 et plans pages 4-9, + photo de Mathis, MI79 rénovée) :
//   COTÉ sur la fiche : longueur de la rame 104,050 m ; motrice 26,025 m ; remorque 26,000 m ; largeur 2,798 m ; hauteur 4,170 m ;
//   entraxe des pivots de bogie 18,500 m ; empattement d'un bogie 2,400 m ; composition motrice + 2 remorques + motrice (4 caisses).
//   COTÉ mais rattaché au dessin par supposition : 1,190 m = hauteur du seuil de porte (flèche verticale sous la caisse, page 4).
//   LU SUR LE PLAN (pas coté) : position des portes, fenêtres, grilles, blocs de toit, pantographe, forme du nez.
//     Échelle horizontale : le plan de côté fait toute la longueur de la caisse (2000 px = 26 m). Échelle verticale : deux points
//     connus, seuil à 1,19 m et dessus des équipements de toit à 4,17 m.
//   Diamètres de roue (1,02 m motrice, 0,92 m remorque) : lus dans un texte de la fiche dont la mise en page est brouillée, à confirmer.
//   COULEURS : relevées à l'œil sur la photo (rame 8265, livrée Île-de-France Mobilités), valeurs approximatives.
//
// Repère du modèle (mètres) : x = sens de marche (la tête est en +x), y = travers, z = hauteur depuis le rail. Origine au milieu de la rame, au rail.
// Le résultat est une liste de PIÈCES, une par couleur : { nom, couleur, maillage } avec maillage au format deck.gl (SimpleMeshLayer).
(function (racine) {
  "use strict";

  const COTES = {
    longueurRame: 104.050, longueurMotrice: 26.025, longueurRemorque: 26.000, largeur: 2.798, hauteur: 4.170,
    entraxePivots: 18.500, empattementBogie: 2.400, seuil: 1.190, rouesMotrice: 1.02, rouesRemorque: 0.92,
  };
  const COULEURS = {   // relevées sur la photo de la MI79 (arrondies) ; cadre, intérieur, sièges, vitre, noir, phare : estimées
    blanc: [238, 242, 246], sombre: [52, 56, 62], ardoise: [92, 104, 120], jupe: [62, 80, 78], toit: [122, 136, 144],
    rouge: [205, 45, 40], magenta: [160, 40, 120], vert: [50, 185, 165], cadre: [26, 28, 32], int: [196, 200, 206], sol: [110, 104, 98],
    siege: [52, 72, 128], noir: [30, 33, 37], phare: [240, 236, 214], orange: [255, 150, 40], parebrise: [16, 24, 34], chrome: [200, 205, 210], logo: [30, 70, 150], bleu: [78, 178, 224], disque: [58, 70, 92], vitre: [150, 185, 210],
  };
  // La vitre est la DERNIÈRE pièce : elle est dessinée en dernier, semi-transparente, par-dessus l'intérieur.
  const ORDRE = ["blanc", "sombre", "ardoise", "jupe", "toit", "cadre", "int", "sol", "siege", "rouge", "magenta", "vert", "noir", "phare", "orange", "parebrise", "chrome", "logo", "bleu", "disque", "vitre"];

  // --- outils géométriques -----------------------------------------------------------------------------------------------------
  const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
  const croix = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
  function normaliser(v) { const n = Math.hypot(v[0], v[1], v[2]) || 1; return [v[0] / n, v[1] / n, v[2] / n]; }

  class Maille {
    constructor() { this.p = []; this.n = []; this.i = []; this.pose = (v) => v; }
    quad(a, b, c, d) {   // sommets vus de l'extérieur dans le sens antihoraire ; la pose (rotation) est appliquée avant le calcul de la normale
      const [A, B, C, D] = [a, b, c, d].map(this.pose);
      const n = normaliser(croix(sub(B, A), sub(D, A)));
      const base = this.p.length / 3;
      for (const s of [A, B, C, D]) { this.p.push(s[0], s[1], s[2]); this.n.push(n[0], n[1], n[2]); }
      this.i.push(base, base + 1, base + 2, base, base + 2, base + 3);
    }
    boite(x0, x1, y0, y1, z0, z1) {
      this.quad([x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1]);
      this.quad([x0, y1, z0], [x1, y1, z0], [x1, y0, z0], [x0, y0, z0]);
      this.quad([x1, y0, z0], [x1, y1, z0], [x1, y1, z1], [x1, y0, z1]);
      this.quad([x0, y1, z0], [x0, y0, z0], [x0, y0, z1], [x0, y1, z1]);
      this.quad([x1, y1, z0], [x0, y1, z0], [x0, y1, z1], [x1, y1, z1]);
      this.quad([x0, y0, z0], [x1, y0, z0], [x1, y0, z1], [x0, y0, z1]);
    }
    // Tranche entre deux niveaux horizontaux r0 puis r1 : { z, xa (arrière), xb (avant), y (demi-largeur) } ; quatre faces latérales.
    tranche(r0, r1, laterales = true) {
      this.quad([r0.xb, -r0.y, r0.z], [r0.xb, r0.y, r0.z], [r1.xb, r1.y, r1.z], [r1.xb, -r1.y, r1.z]);
      if (laterales) {
        this.quad([r0.xb, r0.y, r0.z], [r0.xa, r0.y, r0.z], [r1.xa, r1.y, r1.z], [r1.xb, r1.y, r1.z]);
        this.quad([r0.xa, -r0.y, r0.z], [r0.xb, -r0.y, r0.z], [r1.xb, -r1.y, r1.z], [r1.xa, -r1.y, r1.z]);
      }
      this.quad([r0.xa, r0.y, r0.z], [r0.xa, -r0.y, r0.z], [r1.xa, -r1.y, r1.z], [r1.xa, r1.y, r1.z]);
    }
    // Polygone convexe plat (éventail de triangles), orienté vers `dir` (vecteur dans le repère de la caisse).
    fan(pts, dir) {
      let P = pts.map(this.pose); const d0 = this.pose([0, 0, 0]), d1 = this.pose(dir), D = [d1[0] - d0[0], d1[1] - d0[1], d1[2] - d0[2]];
      let n = normaliser(croix(sub(P[1], P[0]), sub(P[2], P[0])));
      if (n[0] * D[0] + n[1] * D[1] + n[2] * D[2] < 0) { P = P.reverse(); n = [-n[0], -n[1], -n[2]]; }
      const base = this.p.length / 3;
      for (const q of P) { this.p.push(q[0], q[1], q[2]); this.n.push(n[0], n[1], n[2]); }
      for (let k = 1; k + 1 < P.length; k++) this.i.push(base, base + k, base + k + 1);
    }
    capHaut(r) { this.quad([r.xa, -r.y, r.z], [r.xb, -r.y, r.z], [r.xb, r.y, r.z], [r.xa, r.y, r.z]); }
    capBas(r) { this.quad([r.xa, r.y, r.z], [r.xb, r.y, r.z], [r.xb, -r.y, r.z], [r.xa, -r.y, r.z]); }
    cylindreY(cx, cz, rayon, y0, y1, n) {   // axe parallèle à y
      for (let k = 0; k < n; k++) {
        const a0 = 2 * Math.PI * k / n, a1 = 2 * Math.PI * (k + 1) / n;
        const p0 = [cx + rayon * Math.cos(a0), cz + rayon * Math.sin(a0)], p1 = [cx + rayon * Math.cos(a1), cz + rayon * Math.sin(a1)];
        this.quad([p0[0], y1, p0[1]], [p1[0], y1, p1[1]], [p1[0], y0, p1[1]], [p0[0], y0, p0[1]]);
        this.quad([cx, y1, cz], [p1[0], y1, p1[1]], [p0[0], y1, p0[1]], [p0[0], y1, p0[1]]);   // couvercle +y (triangle, sommet répété)
        this.quad([cx, y0, cz], [p0[0], y0, p0[1]], [p1[0], y0, p1[1]], [p1[0], y0, p1[1]]);   // couvercle -y
      }
    }
    // Format deck.gl (SimpleMeshLayer) SANS indices : un triangle = trois sommets à la suite. Les maillages indexés s'affichent de travers
    // (triangles tirés entre des faces voisines) ; sans indices le résultat est le même quelle que soit la façon dont deck.gl les lit.
    export() {
      const P = [], N = [];
      for (let t = 0; t < this.i.length; t++) {
        const k = this.i[t];
        P.push(this.p[3 * k], this.p[3 * k + 1], this.p[3 * k + 2]);
        N.push(this.n[3 * k], this.n[3 * k + 1], this.n[3 * k + 2]);
      }
      return { positions: { value: new Float32Array(P), size: 3 }, normals: { value: new Float32Array(N), size: 3 } };
    }
  }

  // --- plans de côté : positions en pixels sur le dessin de 2000 px de large (= longueur de la caisse) ----------------------------
  const PX_PAR_M = 2000 / 26.0;
  const px = (v) => v / PX_PAR_M;
  const Z_SEUIL_PX = 255, Z_TOIT_PX = 15;   // y du seuil et du dessus des équipements, sur le dessin
  const zPlan = (yPx) => Math.min(COTES.hauteur, COTES.seuil + (Z_SEUIL_PX - yPx) * (COTES.hauteur - COTES.seuil) / (Z_SEUIL_PX - Z_TOIT_PX));

  const Z = { basJupe: 0.75, seuil: COTES.seuil, hautPorte: zPlan(100), hautSombre: 3.1, hautVertical: 3.5, toit: zPlan(40) };
  const DEMI_L = COTES.largeur / 2 - 0.025;   // la coque est un peu plus étroite que la cote : portes, vitres et bandes ajoutent 2,5 cm de relief de chaque côté (vitres, bande noire des portes)
  const FENETRE_Z0 = zPlan(190), FENETRE_Z1 = zPlan(112);

  const PORTES = [[238, 360], [660, 782], [1170, 1295], [1685, 1808]];                          // paires de portes, identiques pour tous
  const FENETRES_MOTRICE = [[447, 523], [535, 611], [835, 908], [920, 1035], [1047, 1120], [1348, 1420], [1432, 1547], [1560, 1632], [1860, 1932]];
  const FENETRES_REMORQUE = [[65, 135], [360, 430], [442, 553], [566, 636], [860, 930], [943, 1055], [1067, 1137], [1361, 1431], [1443, 1555], [1567, 1638], [1862, 1932]];
  const GRILLES_MOTRICE = [[1367, 1418], [1430, 1480]];
  const GRILLES_REMORQUE = [[486, 538], [548, 597]];
  const TOIT_MOTRICE = [[150, 225, 18, 0.6], [228, 335, 25, 0.7], [905, 1090, 15, 0.8], [1430, 1555, 12, 0.8], [800, 835, 30, 0.4], [385, 415, 28, 0.4]];
  const TOIT_REMORQUE = [[860, 1050, 18, 0.8], [505, 540, 33, 0.5], [1320, 1370, 30, 0.5], [595, 615, 30, 0.3]];

  // Avant de la motrice : abscisse (depuis le bout de caisse, en x local) de la face avant selon la hauteur (lu sur le plan de côté).
  const NEZ = [[0.75, 0.55], [Z.seuil, 0.25], [2.0, 0.5], [Z.hautSombre, 0.85], [Z.hautVertical, 1.0], [Z.toit, 1.4]];
  function xNez(z) {
    for (let k = 0; k + 1 < NEZ.length; k++) {
      if (z <= NEZ[k + 1][0]) { const t = (z - NEZ[k][0]) / (NEZ[k + 1][0] - NEZ[k][0]); return NEZ[k][1] + t * (NEZ[k + 1][1] - NEZ[k][1]); }
    }
    return NEZ[NEZ.length - 1][1];
  }
  const DEMI_TOIT = 0.95;   // demi-largeur du pan de toit plat (lu sur la vue de face)

  // --- profil de coque : parois droites puis arrondi vers le toit (demi-largeur y selon la hauteur z) ---------------------------------
  // a = angle de la normale (0 = paroi verticale, 90° = toit horizontal) ; c = couleur de la tranche qui MÈNE à ce niveau (vue de la photo :
  // jupe vert-gris, filet blanc, bande ardoise, filet blanc, bande anthracite des vitres, haut blanc, toit gris) ; perce = tranche percée de fenêtres.
  const PAS_ARRONDI = 10, Z_FIN_PAROI = 3.3, Y_CENTRE_TOIT = 0.25, C_PLAN = 0.35, E_BANDE = 0.02, EP_PAROI = 0.06;
  const Z_PORTE_BAS = Z.seuil, Z_PORTE_HAUT = 3.1, Z_ROUGE = 1.9, Z_VIT0 = 2.0, Z_VIT1 = 2.97, Z_SOL = 1.25, Z_PLAFOND = 3.15;
  const PROFIL = (() => {
    const p = [{ z: Z.basJupe, y: 1.33, a: 0, c: "jupe" }, { z: Z.seuil, y: DEMI_L, a: 0, c: "jupe" }, { z: 1.24, y: DEMI_L, a: 0, c: "blanc" },
      { z: 1.85, y: DEMI_L, a: 0, c: "ardoise" }, { z: 1.9, y: DEMI_L, a: 0, c: "blanc" }, { z: Z_VIT0, y: DEMI_L, a: 0, c: "sombre" },
      { z: Z_VIT1, y: DEMI_L, a: 0, c: "sombre", perce: true }, { z: Z_PORTE_HAUT, y: DEMI_L, a: 0, c: "sombre" }, { z: Z_FIN_PAROI, y: DEMI_L, a: 0, c: "blanc" }];
    for (let k = 1; k <= PAS_ARRONDI; k++) {
      const a = (k / PAS_ARRONDI) * Math.PI / 2;
      p.push({ z: Z_FIN_PAROI + (Z.toit - Z_FIN_PAROI) * Math.sin(a), y: Y_CENTRE_TOIT + (DEMI_L - Y_CENTRE_TOIT) * Math.cos(a), a, c: k > PAS_ARRONDI * 0.6 ? "toit" : "blanc" });
    }
    return p;
  })();
  const IDX_DEBUT_RUBAN = PROFIL.findIndex((r) => r.z === Z_PORTE_HAUT);   // au-dessus de la porte
  const IDX_FIN_BANDE = PROFIL.findIndex((r) => r.c === "toit") - 1;       // jusqu'au début du toit gris
  const xFront = (z, y, w) => xNez(z) + C_PLAN * Math.pow(Math.abs(y) / w, 3);   // face avant bombée : les coins reculent de C_PLAN

  function rectArrondi(xa, xb, z0, z1, r, seg) {
    const pts = [];
    for (const [cx, cz, a0] of [[xb - r, z1 - r, 0], [xa + r, z1 - r, 90], [xa + r, z0 + r, 180], [xb - r, z0 + r, 270]])
      for (let s = 0; s <= seg; s++) { const a = (a0 + 90 * s / seg) * Math.PI / 180; pts.push([cx + r * Math.cos(a), cz + r * Math.sin(a)]); }
    return pts;
  }
  function disque(cx, cy, cz, rayon, n) {   // disque dans un plan x = cste
    const pts = []; for (let k = 0; k < n; k++) { const a = 2 * Math.PI * k / n; pts.push([cx, cy + rayon * Math.cos(a), cz + rayon * Math.sin(a)]); }
    return pts;
  }

  // Panneau plat dans un plan y = cste, de x0 à x1 et z0 à z1, avec des trous rectangulaires [xa, xb] qui partagent la même plage de hauteur [h0, h1].
  // `normale` = +1 ou -1 (côté vers lequel la face est tournée).
  function panneauPerce(M, piece, y, normale, x0, x1, z0, z1, trous, h0, h1) {
    const rect = (xa, xb, za, zb) => {
      if (xb - xa < 1e-6 || zb - za < 1e-6) return;
      const A = [xa, y, za], B = [xb, y, za], C = [xb, y, zb], D = [xa, y, zb];
      if (normale > 0) M[piece].quad(A, D, C, B); else M[piece].quad(A, B, C, D);
    };
    if (!trous.length) { rect(x0, x1, z0, z1); return; }
    rect(x0, x1, z0, Math.min(h0, z1)); rect(x0, x1, Math.max(h1, z0), z1);
    const bas = Math.max(h0, z0), haut = Math.min(h1, z1);
    let x = x0;
    for (const [a, b] of [...trous].sort((u, v) => u[0] - v[0])) { rect(x, a, bas, haut); x = b; }
    rect(x, x1, bas, haut);
  }

  // une caisse, dans son repère local : x de 0 (bout, côté tête pour une motrice) à L ; y travers ; z hauteur
  function caisse(M, motrice, L) {
    const x0 = motrice ? 0 : 0.45, x1 = L - 0.45;
    const rings = PROFIL.map((r) => ({ z: r.z, y: r.y, xa: motrice ? xNez(r.z) + C_PLAN : x0, xb: x1 }));
    for (let k = 0; k + 1 < rings.length; k++) M[PROFIL[k + 1].c].tranche(rings[k], rings[k + 1], !PROFIL[k + 1].perce);
    M.jupe.capBas(rings[0]);
    M.toit.capHaut(rings[rings.length - 1]);
    // bouts de caisse (parois pleines) : remorque des deux côtés, motrice côté arrière ; la face avant de la motrice est bombée, plus bas
    const contour = (x) => [...PROFIL.map((r) => [x, r.y, r.z]), ...PROFIL.map((r) => [x, -r.y, r.z]).reverse()];
    if (!motrice) M.sombre.fan(contour(x0), [-1, 0, 0]);
    M.sombre.fan(contour(x1), [1, 0, 0]);
    const soufflet = (xa, xb) => {   // soufflet d'intercirculation : corps sombre, 5 plis en relief, cadre de raccord aux deux bouts
      M.cadre.boite(xa, xb, -1.08, 1.08, 0.92, 3.58);
      for (let k = 0; k < 5; k++) { const u = xa + (xb - xa) * (k + 0.5) / 5; M.noir.boite(u - 0.03, u + 0.03, -1.14, 1.14, 0.88, 3.62); }
      for (const u of [xa, xb - 0.04]) M.noir.boite(u, u + 0.04, -1.2, 1.2, 0.85, 3.65);
    };
    if (!motrice) soufflet(0, 0.45);
    soufflet(L - 0.45, L);

    // trous de la paroi : fenêtres et vitres des portes (dans la bande Z_VIT0..Z_VIT1)
    const fenetres = (motrice ? FENETRES_MOTRICE : FENETRES_REMORQUE).map(([a, b]) => [px(a), px(b)]);
    const vitresPortes = [];
    for (const [a, b] of PORTES) for (const [u0, u1] of [[a + 10, a + 50], [a + 72, a + 112]]) vitresPortes.push([px(u0), px(u1)]);
    const trous = [...fenetres, ...vitresPortes];
    const xd = motrice ? 2.2 : x0, xf = x1;   // l'intérieur de la motrice commence derrière la cabine

    // intérieur : sol, plafond, bouts, parois intérieures percées, sièges
    const yi = DEMI_L - EP_PAROI;
    M.sol.quad([xd, -yi, Z_SOL], [xf, -yi, Z_SOL], [xf, yi, Z_SOL], [xd, yi, Z_SOL]);
    M.int.quad([xd, yi, Z_PLAFOND], [xf, yi, Z_PLAFOND], [xf, -yi, Z_PLAFOND], [xd, -yi, Z_PLAFOND]);
    M.int.quad([xf - 0.05, yi, Z_SOL], [xf - 0.05, -yi, Z_SOL], [xf - 0.05, -yi, Z_PLAFOND], [xf - 0.05, yi, Z_PLAFOND]);
    M.int.quad([xd + 0.05, -yi, Z_SOL], [xd + 0.05, yi, Z_SOL], [xd + 0.05, yi, Z_PLAFOND], [xd + 0.05, -yi, Z_PLAFOND]);
    for (const cote of [1, -1]) {
      panneauPerce(M, "int", cote * yi, -cote, xd, xf, Z_SOL, Z_PLAFOND, trous.filter(([a]) => a > xd), Z_VIT0, Z_VIT1);   // vue de l'intérieur
      for (let k = 0; k + 1 < PORTES.length; k++) {   // banquettes entre deux portes
        const a = px(PORTES[k][1]) + 0.25, b = px(PORTES[k + 1][0]) - 0.25;
        if (b - a < 1 || a < xd) continue;
        M.siege.boite(a, b, Math.min(cote * 0.55, cote * 1.2), Math.max(cote * 0.55, cote * 1.2), Z_SOL, 1.65);
        M.siege.boite(a, b, Math.min(cote * 1.16, cote * 1.24), Math.max(cote * 1.16, cote * 1.24), 1.65, 2.2);
      }
    }

    for (const cote of [1, -1]) {
      const yp = (e) => cote * (DEMI_L + e);
      const bande = (xa, xb, e0, e1, z0, z1, piece) => M[piece].boite(xa, xb, Math.min(yp(e0), yp(e1)), Math.max(yp(e0), yp(e1)), z0, z1);
      // paroi extérieure percée (bande des vitres)
      // La paroi commence derrière le nez de la motrice (sinon elle dépasse devant) ; un trapèze comble l'espace entre la paroi et le nez incliné.
      const xs = motrice ? xNez(Z_VIT1) + C_PLAN : 0;
      panneauPerce(M, "sombre", cote * DEMI_L, cote, xs, L, Z_VIT0, Z_VIT1, trous, Z_VIT0, Z_VIT1);
      if (motrice) M.sombre.fan([[xNez(Z_VIT0) + C_PLAN, cote * DEMI_L, Z_VIT0], [xs, cote * DEMI_L, Z_VIT0], [xs, cote * DEMI_L, Z_VIT1], [xNez(Z_VIT1) + C_PLAN, cote * DEMI_L, Z_VIT1]], [0, cote, 0]);
      // bande de couleur COLLÉE à la coque : suit le profil (paroi puis arrondi du toit), décalée de E_BANDE le long de la normale
      const ruban = (xa, xb, piece) => {
        for (let k = IDX_DEBUT_RUBAN; k < IDX_FIN_BANDE; k++) {
          const r0 = PROFIL[k], r1 = PROFIL[k + 1];
          const A = [xa, cote * (r0.y + E_BANDE * Math.cos(r0.a)), r0.z + E_BANDE * Math.sin(r0.a)], B = [xb, A[1], A[2]];
          const D = [xa, cote * (r1.y + E_BANDE * Math.cos(r1.a)), r1.z + E_BANDE * Math.sin(r1.a)], C = [xb, D[1], D[2]];
          if (cote > 0) M[piece].quad(A, D, C, B); else M[piece].quad(A, B, C, D);
        }
      };
      const cadreFenetre = (xa, xb, z0, z1, barre) => {   // cadre fin autour d'un trou, et traverse horizontale
        const t = 0.04, ye0 = yp(-EP_PAROI), ye1 = yp(0.025);
        const bx = (u0, u1, w0, w1) => M.cadre.boite(u0, u1, Math.min(ye0, ye1), Math.max(ye0, ye1), w0, w1);
        bx(xa - t, xb + t, z0 - t, z0); bx(xa - t, xb + t, z1, z1 + t); bx(xa - t, xa, z0, z1); bx(xb, xb + t, z0, z1);
        if (barre) bx(xa, xb, (z0 + z1) / 2 - 0.02, (z0 + z1) / 2 + 0.02);
      };
      const vitre = (xa, xb) => M.vitre.fan(rectArrondi(xa, xb, Z_VIT0, Z_VIT1, 0.05, 3).map(([x, z]) => [x, yp(0.0), z]), [0, cote, 0]);
      for (const [a, b] of PORTES) {
        const m = (a + b) / 2, xa = px(a), xm = px(m), xb = px(b), ypo = yp(0.02);
        // vantaux : gauche rouge (haut) puis magenta (bas), droit vert d'eau, avec les vitres percées
        const trousG = [[px(a + 10), px(a + 50)]], trousD = [[px(a + 72), px(a + 112)]];
        panneauPerce(M, "magenta", ypo, cote, xa, xm, Z_PORTE_BAS, Z_ROUGE, [], 0, 0);
        panneauPerce(M, "rouge", ypo, cote, xa, xm, Z_ROUGE, Z_PORTE_HAUT, trousG, Z_VIT0, Z_VIT1);
        panneauPerce(M, "vert", ypo, cote, xm, xb, Z_PORTE_BAS, Z_PORTE_HAUT, trousD, Z_VIT0, Z_VIT1);
        bande(xm - 0.015, xm + 0.015, 0.02, 0.025, Z_PORTE_BAS, Z_PORTE_HAUT, "cadre");           // jointure des deux vantaux
        bande(xa - 0.03, xa, 0, 0.025, Z_PORTE_BAS, Z_PORTE_HAUT, "cadre"); bande(xb, xb + 0.03, 0, 0.025, Z_PORTE_BAS, Z_PORTE_HAUT, "cadre");
        bande(xa, xb, 0, 0.025, Z_PORTE_BAS - 0.12, Z_PORTE_BAS, "noir");                           // marchepied
        for (const u of [xa - 0.1, xb + 0.1]) bande(u - 0.02, u + 0.02, 0, 0.022, 1.3, 2.9, "chrome");   // mains courantes verticales
        ruban(xa, xm, "magenta"); ruban(xm, xb, "vert");                                             // bandes au-dessus de la porte
        for (const [u0, u1] of [[px(a + 10), px(a + 50)], [px(a + 72), px(a + 112)]]) { cadreFenetre(u0, u1, Z_VIT0, Z_VIT1, false); vitre(u0, u1); }
      }
      for (const [xa, xb] of fenetres) { cadreFenetre(xa, xb, Z_VIT0, Z_VIT1, true); vitre(xa, xb); }
      for (const [a, b] of (motrice ? GRILLES_MOTRICE : GRILLES_REMORQUE)) bande(px(a), px(b), 0, 0.02, zPlan(245), zPlan(217), "cadre");
    }
    // équipements de toit (clim, coffrets), posés dans l'arrondi ; remorque : pantographe replié
    for (const [a, b, yTop, demi] of (motrice ? TOIT_MOTRICE : TOIT_REMORQUE)) M.toit.boite(px(a), px(b), -demi, demi, Z.toit - 0.2, zPlan(yTop));
    if (!motrice) {
      for (const x of [0.9, 1.6, 2.4, 3.1]) for (const y of [-0.6, 0.6]) M.noir.boite(x - 0.06, x + 0.06, y - 0.06, y + 0.06, Z.toit - 0.05, Z.toit + 0.14);
      for (const y of [-0.8, 0.8]) M.noir.boite(0.8, 4.9, y - 0.03, y + 0.03, Z.toit + 0.12, Z.toit + 0.18);
      M.noir.boite(4.8, 5.1, -0.95, 0.95, Z.toit + 0.24, COTES.hauteur);                                // archet
    }
    // bogies : pivots espacés de 18,5 m, 2 essieux espacés de 2,4 m, roues de 1,02 m (motrice) ou 0,92 m (remorque)
    const rayon = (motrice ? COTES.rouesMotrice : COTES.rouesRemorque) / 2;
    const p1 = (L - COTES.entraxePivots) / 2;
    for (const pivot of [p1, p1 + COTES.entraxePivots]) {
      M.noir.boite(pivot - 1.7, pivot + 1.7, -0.95, 0.95, rayon - 0.05, rayon + 0.2);
      for (const dx of [-COTES.empattementBogie / 2, COTES.empattementBogie / 2]) {
        for (const y of [-0.72, 0.72]) M.noir.cylindreY(pivot + dx, rayon, rayon, y - 0.07, y + 0.07, 24);
        M.noir.cylindreY(pivot + dx, rayon, 0.07, -0.72, 0.72, 8);
      }
    }
    M.noir.boite(p1 + 2.2, p1 + COTES.entraxePivots - 2.2, -0.95, 0.95, 0.45, Z.basJupe + 0.05);        // équipements sous caisse
    if (motrice) {
      // face avant bombée : grille de quadrilatères sur tout le profil (le milieu avance, les coins reculent) ; bas noir, haut blanc comme sur la photo
      const N = 32;
      for (let j = 0; j + 1 < PROFIL.length; j++) {
        const r0 = PROFIL[j], r1 = PROFIL[j + 1];
        const piece = r1.z <= Z_PORTE_HAUT ? "sombre" : r1.c;
        for (let m = 0; m < N; m++) {
          const pt = (r, mm) => { const y = -r.y + 2 * r.y * mm / N; return [xFront(r.z, y, r.y), y, r.z]; };
          M[piece].quad(pt(r0, m), pt(r1, m), pt(r1, m + 1), pt(r0, m + 1));
        }
      }
      // numéro de rame « 8265 » (7 segments) et logo rond sur le haut de caisse, de chaque côté
      const SEG = { 0: "abcdef", 1: "bc", 2: "abdeg", 3: "abcdg", 4: "bcfg", 5: "acdfg", 6: "acdefg", 7: "abc", 8: "abcdefg", 9: "abcdfg" };
      for (const cote of [1, -1]) {
        const yo = (e) => cote * (DEMI_L + e), xs0 = xNez(Z_VIT1) + C_PLAN + 0.7, zb = 3.13, h = 0.075, w = 0.09, t = 0.016;
        const barre = (x0, x1, z0, z1) => M.noir.boite(x0, x1, Math.min(yo(0), yo(0.012)), Math.max(yo(0), yo(0.012)), z0, z1);
        [...String(8265)].forEach((ch, k) => {
          const k2 = cote === 1 ? 3 - k : k, x = xs0 + k2 * (w + 0.05), g = SEG[ch];   // le texte se lit toujours dans le même sens vu de l'extérieur
          if (g.includes("a")) barre(x, x + w, zb + 2 * h - t, zb + 2 * h); if (g.includes("g")) barre(x, x + w, zb + h - t / 2, zb + h + t / 2);
          if (g.includes("d")) barre(x, x + w, zb, zb + t);
          if (g.includes("f")) (cote === 1 ? barre(x + w - t, x + w, zb + h, zb + 2 * h) : barre(x, x + t, zb + h, zb + 2 * h));
          if (g.includes("b")) (cote === 1 ? barre(x, x + t, zb + h, zb + 2 * h) : barre(x + w - t, x + w, zb + h, zb + 2 * h));
          if (g.includes("e")) (cote === 1 ? barre(x + w - t, x + w, zb, zb + h) : barre(x, x + t, zb, zb + h));
          if (g.includes("c")) (cote === 1 ? barre(x, x + t, zb, zb + h) : barre(x + w - t, x + w, zb, zb + h));
        });
        M.logo.cylindreY(cote === 1 ? xs0 - 0.2 : xs0 + 4 * (w + 0.05) + 0.2, 3.21, 0.075, Math.min(yo(0), yo(0.014)), Math.max(yo(0), yo(0.014)), 16);
      }
      M.orange.boite(1.4, 1.62, -0.11, 0.11, Z.toit - 0.01, Z.toit + 0.1);                                // gyrophare
      M.noir.boite(1.38, 1.64, -0.13, 0.13, Z.toit - 0.02, Z.toit + 0.01);
      for (const s of [-1, 1]) M.noir.boite(0, 0.55, s * 0.68, s * 1.29, 0.76, 1.10);                    // pare-chocs
      M.noir.boite(0.05, 0.6, -0.33, 0.33, 0.75, 1.15);                                                  // attelage
      for (const s of [-1, 1]) {   // phare rond (chrome) et feu rond secondaire, avec leurs couronnes sombres
        const xa = xFront(1.6, s * 0.95, DEMI_L) - 0.02, xb = xFront(1.6, s * 0.55, DEMI_L) - 0.02;
        M.cadre.fan(disque(xa, s * 0.95, 1.6, 0.19, 24), [-1, 0, 0]); M.phare.fan(disque(xa - 0.012, s * 0.95, 1.6, 0.15, 24), [-1, 0, 0]);
        M.cadre.fan(disque(xb, s * 0.55, 1.6, 0.14, 24), [-1, 0, 0]); M.noir.fan(disque(xb - 0.012, s * 0.55, 1.6, 0.1, 24), [-1, 0, 0]);
      }
      // pare-brise : deux vitres opaques sombres (gauche étroite, droite large), posées sur la face bombée ; plaque de destination orange
      const pareBrise = (y0, y1, z0, z1) => {
        const NC = 8, NL = 4;
        const pt = (mm, kk) => { const y = y0 + (y1 - y0) * mm / NC, z = z0 + (z1 - z0) * kk / NL; return [xFront(z, y, DEMI_L) - 0.03, y, z]; };
        for (let kk = 0; kk < NL; kk++) for (let mm = 0; mm < NC; mm++) M.parebrise.quad(pt(mm, kk), pt(mm, kk + 1), pt(mm + 1, kk + 1), pt(mm + 1, kk));
      };
      pareBrise(-1.16, -0.56, 2.05, 3.2);
      pareBrise(-0.38, 1.15, 2.05, 3.2);
      const NC = 10, pt = (mm, z) => { const y = -0.2 + 1.2 * mm / NC; return [xFront(z, y, DEMI_L) - 0.045, y, z]; };
      for (let mm = 0; mm < NC; mm++) M.orange.quad(pt(mm, 3.02), pt(mm, 3.14), pt(mm + 1, 3.14), pt(mm + 1, 3.02));
    }
  }

  // --- la rame : motrice + remorque + remorque + motrice, tête en +x ------------------------------------------------------------
  // Version DÉZOOMÉE (rame vue de loin : quelques pixels par caisse) : même coque, mêmes cotes et mêmes couleurs de bandes,
  // mais sans intérieur, sans cadres, sans roues détaillées ; les vitres sont peintes dans la bande sombre.
  function caisseSimple(M, motrice, L) {
    const x0 = motrice ? 0 : 0.45, x1 = L - 0.45;
    const rings = PROFIL.map((r) => ({ z: r.z, y: r.y, xa: motrice ? xNez(r.z) + C_PLAN : x0, xb: x1 }));
    for (let k = 0; k + 1 < rings.length; k++) M[PROFIL[k + 1].c].tranche(rings[k], rings[k + 1]);
    M.jupe.capBas(rings[0]); M.toit.capHaut(rings[rings.length - 1]);
    const contour = (x) => [...PROFIL.map((r) => [x, r.y, r.z]), ...PROFIL.map((r) => [x, -r.y, r.z]).reverse()];
    if (!motrice) M.sombre.fan(contour(x0), [-1, 0, 0]);
    M.sombre.fan(contour(x1), [1, 0, 0]);
    if (!motrice) M.noir.boite(0, 0.45, -1.1, 1.1, 0.9, 3.6);
    M.noir.boite(L - 0.45, L, -1.1, 1.1, 0.9, 3.6);
    for (const cote of [1, -1]) {
      const y0 = cote * DEMI_L, y1 = cote * (DEMI_L + 0.025), lo = Math.min(y0, y1), hi = Math.max(y0, y1);
      for (const [a, b] of PORTES) {
        const xa = px(a), xb = px(b), xm = (xa + xb) / 2;
        M.magenta.boite(xa, xm, lo, hi, Z_PORTE_BAS, 3.3); M.vert.boite(xm, xb, lo, hi, Z_PORTE_BAS, 3.3);
      }
    }
    const rayon = (motrice ? COTES.rouesMotrice : COTES.rouesRemorque) / 2, p1 = (L - COTES.entraxePivots) / 2;
    for (const pivot of [p1, p1 + COTES.entraxePivots]) M.noir.boite(pivot - 1.7, pivot + 1.7, -0.95, 0.95, 0.15, rayon + 0.2);
    for (const [a, b, yTop, demi] of (motrice ? TOIT_MOTRICE : TOIT_REMORQUE)) M.toit.boite(px(a), px(b), -demi, demi, Z.toit - 0.2, zPlan(yTop));
    if (!motrice) M.noir.boite(4.8, 5.1, -0.95, 0.95, Z.toit, COTES.hauteur);
    if (motrice) {
      const N = 8;
      for (let j = 0; j + 1 < PROFIL.length; j++) {
        const r0 = PROFIL[j], r1 = PROFIL[j + 1], piece = r1.z <= Z_PORTE_HAUT ? "sombre" : r1.c;
        for (let m = 0; m < N; m++) {
          const pt = (r, mm) => { const y = -r.y + 2 * r.y * mm / N; return [xFront(r.z, y, r.y), y, r.z]; };
          M[piece].quad(pt(r0, m), pt(r1, m), pt(r1, m + 1), pt(r0, m + 1));
        }
      }
      for (const sg of [-1, 1]) M.noir.boite(0, 0.55, sg * 0.68, sg * 1.29, 0.76, 1.10);
      for (let m = 0; m < 4; m++) {
        const ya = -1.1 + 2.2 * m / 4, yb = -1.1 + 2.2 * (m + 1) / 4, pt = (y, z) => [xFront(z, y, DEMI_L) - 0.03, y, z];
        M.parebrise.quad(pt(ya, 2.05), pt(ya, 3.15), pt(yb, 3.15), pt(yb, 2.05));
      }
    }
  }

  // Version « ICÔNE » (style Waze / Google Maps) : formes pleines et arrondies, peu de couleurs franches, vitres bleues, bas de caisse noir épais,
  // gros phares ronds, roues apparentes. La rame est élargie et rehaussée (ECHELLE_ICONE) mais garde sa vraie longueur ; un disque la pose sur la carte.
  const ECHELLE_ICONE = 1.5;
  const PROFIL_ICONE = (() => {
    const p = [{ z: 0.3, y: 1.2, c: "noir" }, { z: 0.95, y: 1.38, c: "noir" }, { z: 1.0, y: 1.4, c: "blanc" }, { z: 1.9, y: 1.4, c: "blanc" },
      { z: 2.0, y: 1.4, c: "bleu" }, { z: 3.0, y: 1.4, c: "bleu" }, { z: 3.1, y: 1.38, c: "blanc" }, { z: 3.4, y: 1.36, c: "blanc" }];
    for (let k = 1; k <= 8; k++) { const a = (k / 8) * Math.PI / 2; p.push({ z: 3.4 + (Z.toit - 3.4) * Math.sin(a), y: 0.3 + (1.36 - 0.3) * Math.cos(a), c: k > 5 ? "toit" : "blanc" }); }
    return p;
  })();
  function caisseIcone(M, motrice, L) {
    const x0 = motrice ? 0 : 0.3, x1 = L - 0.3, zz = (z) => Math.max(z, 0.75);
    const rings = PROFIL_ICONE.map((r) => ({ z: r.z, y: r.y, xa: motrice ? xNez(zz(r.z)) + C_PLAN : x0, xb: x1 }));
    for (let k = 0; k + 1 < rings.length; k++) M[PROFIL_ICONE[k + 1].c].tranche(rings[k], rings[k + 1]);
    M.noir.capBas(rings[0]); M.toit.capHaut(rings[rings.length - 1]);
    const contour = (x) => [...PROFIL_ICONE.map((r) => [x, r.y, r.z]), ...PROFIL_ICONE.map((r) => [x, -r.y, r.z]).reverse()];
    if (!motrice) M.blanc.fan(contour(x0), [-1, 0, 0]);
    M.blanc.fan(contour(x1), [1, 0, 0]);
    if (!motrice) M.noir.boite(0, 0.3, -1.0, 1.0, 1.1, 3.0);          // raccord entre deux caisses (l'espace de 0,6 m reste visible)
    M.noir.boite(L - 0.3, L, -1.0, 1.0, 1.1, 3.0);
    for (const cote of [1, -1]) {   // portes : gros blocs magenta et vert d'eau
      const y0 = cote * 1.4, y1 = cote * 1.45, lo = Math.min(y0, y1), hi = Math.max(y0, y1);
      for (const [a, b] of PORTES) { const xa = px(a), xb = px(b), xm = (xa + xb) / 2; M.magenta.boite(xa, xm, lo, hi, 1.05, 3.45); M.vert.boite(xm, xb, lo, hi, 1.05, 3.45); }
    }
    const p1 = (L - COTES.entraxePivots) / 2;
    for (const pivot of [p1, p1 + COTES.entraxePivots]) for (const dx of [-1.2, 1.2]) for (const cote of [1, -1]) {
      const ya = cote > 0 ? 1.28 : -1.5, yb = cote > 0 ? 1.5 : -1.28;
      M.noir.cylindreY(pivot + dx, 0.55, 0.5, ya, yb, 20);
      M.chrome.cylindreY(pivot + dx, 0.55, 0.27, cote > 0 ? 1.5 : -1.53, cote > 0 ? 1.53 : -1.5, 14);
    }
    if (!motrice) { M.noir.boite(4.8, 5.1, -0.95, 0.95, Z.toit - 0.05, COTES.hauteur - 0.4); M.toit.boite(0.8, 3.4, -0.7, 0.7, Z.toit - 0.15, Z.toit + 0.12); }
    if (motrice) {
      const N = 14;
      for (let j = 0; j + 1 < PROFIL_ICONE.length; j++) {
        const r0 = PROFIL_ICONE[j], r1 = PROFIL_ICONE[j + 1];
        for (let m = 0; m < N; m++) {
          const pt = (r, mm) => { const y = -r.y + 2 * r.y * mm / N; return [xFront(zz(r.z), y, r.y), y, r.z]; };
          const ym = Math.abs(-1 + 2 * (m + 0.5) / N);
          const piece = r1.z <= 1.0 ? "noir" : (r0.z >= 1.9 && r1.z <= 3.1 && ym < 0.8) ? "bleu" : r1.c === "bleu" ? "blanc" : r1.c;
          M[piece].quad(pt(r0, m), pt(r1, m), pt(r1, m + 1), pt(r0, m + 1));
        }
      }
      for (const sg of [-1, 1]) {
        const xph = xFront(1.5, sg * 0.85, 1.4) - 0.03;
        M.noir.fan(disque(xph, sg * 0.85, 1.5, 0.27, 20), [-1, 0, 0]); M.phare.fan(disque(xph - 0.015, sg * 0.85, 1.5, 0.21, 20), [-1, 0, 0]);
      }
      M.noir.boite(0, 0.5, -1.35, 1.35, 0.4, 0.95);     // pare-chocs épais
    }
  }

  function construire(options) {
    const simple = !!(options && options.simple), icone = !!(options && options.icone), construireCaisse = icone ? caisseIcone : simple ? caisseSimple : caisse;
    const M = {};
    for (const nom of ORDRE) M[nom] = new Maille();
    const total = COTES.longueurRame;
    const voitures = [
      { motrice: true, L: COTES.longueurMotrice, debut: 0, miroir: false },
      { motrice: false, L: COTES.longueurRemorque, debut: COTES.longueurMotrice, miroir: false },
      { motrice: false, L: COTES.longueurRemorque, debut: COTES.longueurMotrice + COTES.longueurRemorque, miroir: true },
      { motrice: true, L: COTES.longueurMotrice, debut: COTES.longueurMotrice + 2 * COTES.longueurRemorque, miroir: true },
    ];
    // options.voiture = 0 (motrice de tête) ou 1 (remorque) : UNE seule caisse, centrée sur elle-même (tête en +x), pour qu'elle suive les courbes sur sa propre position.
    // La remorque 2 est la remorque 1 tournée de 180° autour de z, la motrice de queue est la motrice de tête tournée de 180° : la carte réutilise donc ces deux maillages.
    const une = options && options.voiture != null ? options.voiture : null;
    for (const [rang, v] of voitures.entries()) {
      if (une != null && rang !== une) continue;
      const decalage = une != null ? total / 2 - v.debut - v.L / 2 : 0;   // abscisse du centre de la caisse dans le repère de la rame
      // repère de la caisse (dessin, cabine à gauche) -> repère de la rame (tête en +x) : rotations de 180° autour de z, donc sans miroir
      const pose0 = v.miroir ? ([x, y, z]) => [total / 2 - v.debut - (v.L - x), y, z] : ([x, y, z]) => [total / 2 - v.debut - x, -y, z];
      const pose = ([x, y, z]) => { const q = pose0([x, y, z]); return [q[0] - decalage, q[1], q[2]]; };
      // icône : la rame est élargie et rehaussée (y et z multipliés), la longueur reste vraie
      const poseFinale = icone ? (q) => { const [x, y, z] = pose(q); return [x, y * ECHELLE_ICONE, z * ECHELLE_ICONE]; } : pose;
      for (const nom of ORDRE) M[nom].pose = poseFinale;
      construireCaisse(M, v.motrice, v.L);
    }
    if (icone) {   // disque posé sur le sol, sous toute la rame (repère de la carte : changera de couleur selon l'état du train)
      M.disque.pose = (q) => q;
      const pts = [], R = 3.6, demi = total / 2 + 1.5;
      for (let k = 0; k <= 16; k++) { const a = -Math.PI / 2 + Math.PI * k / 16; pts.push([demi - R + R * Math.cos(a) * 0.9, R * Math.sin(a), 0.03]); }
      for (let k = 0; k <= 16; k++) { const a = Math.PI / 2 + Math.PI * k / 16; pts.push([-demi + R + R * Math.cos(a) * 0.9, R * Math.sin(a), 0.03]); }
      M.disque.fan(pts, [0, 0, 1]);
    }
    return {
      cotes: COTES,
      pieces: ORDRE.map((nom) => ({ nom, couleur: COULEURS[nom], maillage: M[nom].export() })).filter((p) => p.maillage.positions.value.length > 0),
    };
  }

  const API = { COTES, COULEURS, construire, Maille };
  if (typeof module !== "undefined" && module.exports) module.exports = API; else racine.ModeleZ8100 = API;
})(typeof window !== "undefined" ? window : globalThis);
