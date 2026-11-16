# Bibliothèques tierces servies en local (aucun CDN)

## deck.gl 9.4.1 (`deck.gl-9.4.1.min.js`)

• Source : paquet npm `deck.gl@9.4.1`, fichier `dist.min.js` (copié tel quel, non modifié). Licence MIT (Vis.gl contributors), voir `deck.gl-LICENSE.txt`.
• Récupéré le 16/11/2026 avec `npm pack deck.gl@9.4.1 --ignore-scripts` dans un dossier isolé ; aucun script d'installation (`preinstall`, `install`, `postinstall` : 0).
• Contrôle de provenance : somme de contrôle du paquet `.tgz` identique à celle du registre npm. SHA-1 `be1e4e894bc5bc8f00d9d3ea973af9f4f518cb21`, SHA-512 `sha512-172SlLmFDk9RU9z7FMZba8Qxk/Mv/z66Ak5CAznwmNYCTNptmGL5GjdActUn2lfF04vFNfyLxsQjkC96c5bo7g==`.
• SHA-256 du fichier `dist.min.js` : `08857e84bc2e5478a62ce1dc59b9b5fae9ae0a21104c78503dece316d69a1ca6` (2 078 081 octets).
• Audit du bundle : il contient des adresses externes par défaut, **utilisées seulement si on appelle ces fonctions** : `unpkg.com` (ateliers de chargement loaders.gl, pour certains formats de fichier), `gstatic.com` (décodeur Draco, modèles compressés), géocodeurs Google/Mapbox/OpenCage (widget de recherche d'adresse), 19 occurrences de `fetch(` (chargeur de fichiers). **Règle du projet : on n'appelle aucune de ces fonctions.** Le train provisoire est un maillage écrit à la main (aucun fichier à charger). La page de la carte bloque tout `fetch` et tout `Worker` qui ne vise pas `127.0.0.1` (garde réseau, testée).
• Mise à jour : refaire la même procédure, comparer la somme de contrôle au registre, relire cette liste d'adresses, puis mettre ce fichier à jour.
