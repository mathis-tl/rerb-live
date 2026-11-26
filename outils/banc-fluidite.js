// Banc de mesure de la fluidité de la carte (21/11/2026). À exécuter DANS la page, sur la démo (aucun appel PRIM) :
//   python3 rerb/serveur.py --demo --revisions --port 8768, ouvrir http://127.0.0.1:8768/, cliquer « Voir les trains »,
//   puis coller ce fichier dans la console (ou l'injecter par l'outil de pilotage du navigateur). Il renvoie un JSON et un verdict.
// Seuils : au repos au moins 50 images/s ; en déplacement au moins 45 images/s et au plus 2 tâches longues (> 50 ms).
// Mesuré le 21/11 après correction (fenêtre 1500 x 729, Apple M2) : 51 à 58 images/s partout, au plus 2 tâches longues ; avant : 28 à 37 images/s, jusqu'à 21 tâches longues.
// Les valeurs absolues dépendent de la machine et de la taille de la fenêtre : ne comparer que des mesures faites dans les mêmes conditions.
(async () => {
  const bench = async (nom, dureeMs, pas) => {
    const d = [], longues = []; let dernier = performance.now(); const fin = dernier + dureeMs;
    const po = new PerformanceObserver(l => l.getEntries().forEach(e => longues.push(Math.round(e.duration)))); try { po.observe({ entryTypes: ["longtask"] }); } catch (e) {}
    await new Promise(ok => { const f = () => { const t = performance.now(); d.push(t - dernier); dernier = t; if (pas) pas(1 - (fin - t) / dureeMs); t < fin ? requestAnimationFrame(f) : ok(); }; requestAnimationFrame(f); });
    po.disconnect(); d.shift(); const s = [...d].sort((a, b) => a - b), q = p => +s[Math.min(s.length - 1, Math.floor(s.length * p))].toFixed(1);
    return { nom, fps: +(1000 / (d.reduce((a, b) => a + b, 0) / d.length)).toFixed(1), mediane_ms: q(0.5), p95_ms: q(0.95), p99_ms: q(0.99), max_ms: Math.round(s[s.length - 1]), taches_longues: longues.length, pire_tache_ms: Math.max(0, ...longues) };
  };
  suivi = null; vol = null;
  const via = (vs, i) => deckgl.props.onViewStateChange({ viewState: { ...vs, ...LIMITES_VUE }, interactionState: i });
  const centre = [2.2588, 48.7255], loin = [2.345, 48.85], R = [];
  const pause = ms => new Promise(r => setTimeout(r, ms));
  const poser = (lon, lat, z) => appliquerVue({ longitude: lon, latitude: lat, zoom: z, pitch: 60, bearing: 20 });
  for (const z of [16.5, 18.3]) { poser(centre[0], centre[1], z); await pause(2500); R.push({ ...(await bench("repos zoom " + z, 3000, null)), type: "repos" }); }
  decorMontre.clear(); cellulesDecor.clear(); poser(centre[0], centre[1], 15); await pause(800);
  R.push({ ...(await bench("deplacement 15 km en 10 s, decor a froid", 10000, f => via({ longitude: centre[0] + (loin[0] - centre[0]) * f, latitude: centre[1] + (loin[1] - centre[1]) * f, zoom: 15, pitch: 60, bearing: 20 }, { isDragging: true, isPanning: true }))), type: "mouvement" });
  poser(centre[0], centre[1], 12); await pause(1500);
  R.push({ ...(await bench("zoom 12 vers 19 en 3 s", 3000, f => via({ longitude: centre[0], latitude: centre[1], zoom: 12 + 7 * f, pitch: 60, bearing: 20 }, { isZooming: true }))), type: "mouvement" });
  poser(centre[0], centre[1], 16.5); await pause(1500);
  R.push({ ...(await bench("rotation 360 degres en 4 s", 4000, f => via({ longitude: centre[0], latitude: centre[1], zoom: 16.5, pitch: 60, bearing: 360 * f }, { isDragging: true, isRotating: true }))), type: "mouvement" });
  const cv = document.querySelector("#carte canvas");
  R.push({ ...(await bench("survol a la souris", 3000, f => cv.dispatchEvent(new PointerEvent("pointermove", { clientX: 300 + 600 * f, clientY: 250 + 250 * f, bubbles: true, pointerType: "mouse" })))), type: "repos" });
  // Zoom inertiel de trackpad (21/11) : ~60 molettes par seconde dont le pas décroît (les doigts ont quitté le pavé). Critères : aucune image immobile pendant le geste, aucune inversion de sens.
  const inertiel = async (nom, depart, signe, suivre) => {
    poser(centre[0], centre[1], depart); suivi = null; cibleZoom = null; await pause(1200);
    if (suivre) { const t = placesAffiches(performance.now()).find(x => x.voie); if (t) { suivi = t.course; animer(); await pause(500); } }
    const R0 = cv.getBoundingClientRect(), traj = []; let actif = true; const t0 = performance.now();
    const boucle = () => { if (!actif) return; traj.push(vueCarte.zoom); requestAnimationFrame(boucle); }; boucle();
    let d = 25 * signe, t = 0; const evs = [];
    while (Math.abs(d) > 1 && t < 1800) { evs.push([t, d]); d *= 0.95; t += 16.7 + (Math.random() - 0.5) * 8; }
    for (const [te, dy] of evs) { const att = te - (performance.now() - t0); if (att > 0) await pause(att); cv.dispatchEvent(new WheelEvent("wheel", { deltaY: dy, deltaMode: 0, clientX: R0.left + R0.width * 0.7, clientY: R0.top + R0.height * 0.4, bubbles: true, cancelable: true })); }
    await pause(1200); actif = false; suivi = null;
    const v = []; for (let i = 1; i < traj.length; i++) v.push(traj[i] - traj[i - 1]);
    const sens = Math.sign(traj[traj.length - 1] - traj[0]), duree = Math.floor(evs[evs.length - 1][0] / 16.7);
    return { nom, type: "zoom", images_immobiles: v.slice(2, duree).filter(x => Math.abs(x) < 1e-6).length, inversions: v.filter(x => Math.sign(x) === -sens && Math.abs(x) > 1e-5).length, pas_max: +Math.max(...v.map(Math.abs)).toFixed(3) };
  };
  R.push(await inertiel("zoom inertiel libre (dezoom)", 16, 1, false));
  R.push(await inertiel("zoom inertiel libre (zoom)", 13, -1, false));
  R.push(await inertiel("zoom inertiel avec suivi", 16, 1, true));
  for (const r of R) r.ok = r.type === "zoom" ? (r.images_immobiles <= 2 && r.inversions === 0) : r.type === "repos" ? r.fps >= 50 : (r.fps >= 45 && r.taches_longues <= 2);
  return JSON.stringify({ fenetre: [innerWidth, innerHeight], dpr: devicePixelRatio, tout_ok: R.every(r => r.ok), mesures: R }, null, 1);
})()
