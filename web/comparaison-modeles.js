// Icône contre modèle détaillé (données INVENTÉES) : mêmes échelle, caméra et train ; réutilise web/tableau-rendu.js (chargé avant).
"use strict";
const ANGLES = [
  { titre: "zoom 17,2 (taille réelle), incliné 55°, vu de trois quarts", zoom: 17.2, pitch: 55, bearing: 35 },
  { titre: "zoom 18,7 (taille réelle), incliné 20°, vu de côté (motrice de tête à droite)", zoom: 18.7, pitch: 20, bearing: 0 },
  { titre: "zoom 14,5 (agrandi à ~10 px de large), incliné 55°", zoom: 14.5, pitch: 55, bearing: 20 },
  { titre: "zoom 14,5 (agrandi), de dessus", zoom: 14.5, pitch: 0, bearing: 0 },
  { titre: "zoom 12,5 (très dézoomé), incliné 55°", zoom: 12.5, pitch: 55, bearing: 20 },
  { titre: "zoom 10,5 (le plus dézoomé), incliné 55°", zoom: 10.5, pitch: 55, bearing: 20 },
];
const decksM = [];
function themeM() { return document.querySelector('input[name="theme"]:checked').value; }
function construireM() {
  const conteneur = document.getElementById("rangees");
  for (const a of ANGLES) {
    const rangee = document.createElement("div");
    rangee.className = "rangee";
    rangee.innerHTML = `<div class="entete"><span>ICÔNE · ${a.titre}</span><span>DÉTAILLÉ · ${a.titre}</span></div><div class="vue"></div>`;
    conteneur.appendChild(rangee);
    const element = rangee.querySelector(".vue"), etat = { longitude: LON0, latitude: LAT0, zoom: a.zoom, pitch: a.pitch, bearing: a.bearing, minZoom: 5, maxZoom: 21, maxPitch: 85 };
    decksM.push({ element, deck: new deck.Deck({
      parent: element, controller: false, layers: [],
      views: [new deck.MapView({ id: "icone", x: 0, width: "50%" }), new deck.MapView({ id: "detail", x: "50%", width: "50%" })],
      viewState: { icone: etat, detail: etat }, layerFilter: ({ layer, viewport }) => layer.id.startsWith(viewport.id + "/"),
    }), a });
  }
}
function dessinerM(maintenant) {
  const t = themeM(), palette = document.getElementById("palette").checked;
  for (const { element, deck: d, a } of decksM) {
    element.className = `vue ${t}`;
    const base = { theme: t, zoom: a.zoom, zoom0: a.zoom, pres: false, cles: ["en-route"], colonnes: 1, reglages: { contour: false, socle: false, palette, incline: true, pulse: false } };
    const marquer = (liste, prefixe) => liste.map(l => l.clone({ id: `${prefixe}/${l.id}` }));
    d.setProps({ layers: [...marquer(calques({ ...base, force: "icone" }, maintenant), "icone"), ...marquer(calques({ ...base, force: "detail" }, maintenant), "detail")] });
  }
}
construireM();
(function boucle(maintenant) { dessinerM(maintenant); requestAnimationFrame(boucle); })(performance.now());
