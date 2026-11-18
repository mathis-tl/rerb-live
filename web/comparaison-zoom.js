// Comparaison avant / après selon le zoom (données INVENTÉES). Réutilise le code de web/tableau-rendu.js (chargé avant) : `calques`, `echelleTrain`, `metresParPixel`.
// Une ligne par niveau de zoom entier (un niveau de plus = échelle ×2) ; dans chaque ligne, deux vues côte à côte : « avant » (carte actuelle) et « après » (proposition).
"use strict";

const ZOOMS = [10, 11, 12, 13, 14, 15, 16, 17];
const CLES = ["en-route", "a-quai", "terminus", "selection"], CLES_PROCHE = ["en-route", "terminus", "selection"];   // de près (zoom 16 et plus) : une seule colonne, les rames sont trop longues pour deux
const lignes = {};   // zoom -> { deck, element }

function theme() { return document.querySelector('input[name="theme"]:checked').value; }
function etatVue(zoom) { return { longitude: LON0, latitude: LAT0, zoom, pitch: 55, bearing: 0, minZoom: 5, maxZoom: 21, maxPitch: 85 }; }

function legendePixels(zoom) {
  const mpp = metresParPixel(zoom), px = (ech, cote) => Math.round(LONGUEUR_RAME_M * ech.longueur / mpp);
  const a = echelleTrain(zoom, true), n = echelleTrain(zoom, false);
  return [`${a.detail ? "détaillé" : "icône"} · rame ≈ ${px(a)} px de long`, `${n.detail ? "détaillé" : "icône"} · rame ≈ ${px(n)} px de long`];
}

function construire() {
  const conteneur = document.getElementById("rangees");
  for (const zoom of ZOOMS) {
    const [textesAvant, textesApres] = legendePixels(zoom);
    const rangee = document.createElement("div");
    rangee.className = "rangee";
    rangee.innerHTML = `<div class="entete"><span>Zoom ${zoom} · AVANT (carte actuelle) : ${textesAvant}</span><span>Zoom ${zoom} · APRÈS (proposition) : ${textesApres}</span></div><div class="vue" id="vue-${zoom}"></div>`;
    conteneur.appendChild(rangee);
    const element = rangee.querySelector(".vue");
    const deck_ = new deck.Deck({
      parent: element, controller: false, layers: [],
      views: [new deck.MapView({ id: "avant", x: 0, width: "50%" }), new deck.MapView({ id: "apres", x: "50%", width: "50%" })],
      viewState: { avant: etatVue(zoom), apres: etatVue(zoom) },
      layerFilter: ({ layer, viewport }) => layer.id.startsWith(viewport.id + "/"),
    });
    lignes[zoom] = { deck: deck_, element };
  }
}

function dessiner(maintenant) {
  const t = theme(), socle = document.getElementById("socle").checked, palette = document.getElementById("palette").checked;
  for (const zoom of ZOOMS) {
    const { deck: d, element } = lignes[zoom];
    element.className = `vue ${t}`;
    const base = { theme: t, zoom, zoom0: zoom, pres: false, cles: zoom >= 16 ? CLES_PROCHE : CLES, colonnes: zoom >= 16 ? 1 : 2 };
    const avant = calques({ ...base, ancien: true, reglages: { contour: true, socle: false, palette: false, incline: true, pulse: false } }, maintenant);
    const apres = calques({ ...base, ancien: false, reglages: { contour: true, socle, palette, incline: true, pulse: false } }, maintenant);
    const marquer = (liste, prefixe) => liste.map(l => l.clone({ id: `${prefixe}/${l.id}` }));
    d.setProps({ layers: [...marquer(avant, "avant"), ...marquer(apres, "apres")] });
  }
}
construire();
(function boucle(maintenant) { dessiner(maintenant); requestAnimationFrame(boucle); })(performance.now());
