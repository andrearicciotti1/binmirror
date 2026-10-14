# BinMirror su Gumroad

Pubblicato il 14/10/2026: https://andrearicciotti1.gumroad.com/l/sbndul · product_id `pJW2s4N5bEWkkSRE50BHgQ==` (in `binmirror/license.py`).
Landing e file si aggiornano con il programma `gumroad` (brew install antiwork/cli/gumroad):
`gumroad products page publish sbndul ./landing/landing_binmirror.html` e
`gumroad products update pJW2s4N5bEWkkSRE50BHgQ== --file ~/Documents/CLAUDE/BinMirror/BinMirror-X.Y.Z.dmg`.

Testi pronti per il prodotto. La landing completa è `landing_binmirror.html` (IT/EN, stesso
schema di DropCut): si incolla nella pagina custom del prodotto.

## Campi del prodotto

- **Nome:** BinMirror per DaVinci Resolve
- **Prezzo proposto:** €5 una tantum (come tutti i plugin di Riccio)
- **File sul prodotto:** `BinMirror-0.4.0.dmg` (caricato il 14/10/2026). Il vecchio 0.3.0 è tolto dal
  contenuto che vedono i clienti; resta solo come allegato in archivio (la CLI non cancella allegati).
  Per sostituire il file: `--file` aggiunge, poi `gumroad products content get/set` per togliere il
  `fileEmbed` vecchio.
- **Riassunto (sotto il bottone):** I bin di DaVinci Resolve e le cartelle sul disco sempre uguali, come in Avid.

**Descrizione breve (IT)**

> Sposti una clip in un altro bin e il file va nella cartella giusta. Riordini il disco nel Finder
> e il Media Pool si riordina da solo, con il relink fatto. Nessun file viene mai cancellato: quello
> che togli finisce in una cartella _CESTINO. Richiede DaVinci Resolve Studio su macOS.

**Short description (EN)**

> Move a clip to another bin and the file moves to the right folder. Reorganize the disk in Finder
> and the Media Pool follows, relink included. No file is ever deleted: whatever you remove goes to
> a _CESTINO (trash) folder. Requires DaVinci Resolve Studio on macOS.

Nella descrizione del prodotto, dal 14/10/2026, c'è anche un paragrafo «Novità 0.4» / «New in 0.4»
(automatico, filtri, sequenze, data di import, timeline, notifica).

## Da fare prima di pubblicare

- **Licenza:** ✅ attiva dalla build caricata il 14/10/2026.
- **Copertina e screenshot:** `docs/anteprima.png` mostra un percorso di prova; meglio uno screenshot
  nuovo dell'app su un progetto vero.
- **Interfaccia solo in italiano:** la landing lo dice anche nella versione inglese.
- **Prodotto incrociato:** il banner in alto porta a DropCut Resolve (`/l/qkheq`). Si può aggiungere
  il link inverso nella landing di DropCut.
