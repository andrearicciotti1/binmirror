# BinMirror

I bin di **DaVinci Resolve** e le cartelle su disco (anche su un disco esterno) sempre uguali, come in
Avid Media Composer. Sposti una clip da un bin all'altro e il file si sposta nella cartella giusta;
rinomini una cartella nel Finder e il bin si rinomina in Resolve.

Stato: **0.4.0, modalità automatica, filtri, sequenze, timeline**. Il motore 0.1 è verificato sulla
Resolve Studio vera (vedi sotto); le novità 0.3 e 0.4 ⚠️ sono provate solo con la Resolve simulata
(46 scenari).
Il DMG si costruisce solo quando decidiamo che la versione è pronta.

## Cosa fa

| Cosa fai | Dove | Cosa succede dall'altra parte |
|---|---|---|
| Crei un bin | Resolve | nasce la cartella |
| Crei una cartella | disco | nasce il bin |
| Sposti o rinomini un bin | Resolve | stessa cosa alla cartella, le clip vengono ricollegate |
| Sposti o rinomini una cartella | disco | stessa cosa al bin, le clip vengono ricollegate |
| Sposti una clip in un altro bin | Resolve | il file va nella cartella di quel bin (+ relink) |
| Sposti un file in un'altra cartella | disco | la clip va nel bin di quella cartella (+ relink) |
| Metti un file nuovo in una cartella | disco | viene importato nel bin giusto |
| Togli una clip | Resolve | il file va in `_CESTINO` |
| Togli un bin | Resolve | la cartella va in `_CESTINO` |
| Cancelli una cartella vuota | disco | il bin si toglie, solo se è vuoto |

Regole di sicurezza:
- **Nessun file viene mai cancellato.** Quello che sparisce va in `<radice>/_CESTINO/<data_ora>/…`.
- Prima sincronizzazione: si unisce e basta, niente cestino. Se una clip sta in un bin diverso dalla
  cartella del suo file, comanda il disco (si sposta la clip, non il file).
- Se la radice non c'è (disco scollegato) o è vuota quando prima non lo era: **si blocca**.
- Se dovrebbe mettere nel cestino troppi file insieme, o se è aperto un progetto (o scelto un bin)
  diverso dall'ultima volta: si blocca, serve `--forza` (nell'app: «Procedi lo stesso»).
- Clip con il file fuori dalla cartella radice: non vengono toccate.
- Un bin che contiene ancora qualcosa (anche solo timeline) non viene mai tolto.

## Casi limite e avvisi

Tre livelli, dal più grave:

**⛔ Bloccato: non si applica niente.**
| Situazione | Messaggio |
|---|---|
| Disco esterno scollegato | «Il disco X non è collegato» |
| Cartella sparita (spostata/rinominata) | la cartella non c'è più |
| Cartella vuota ma l'ultima volta c'erano file | «il disco è collegato bene?» |
| Disco in sola lettura / senza permessi | non posso scrivere |
| Aperto un progetto (o scelto un bin) diverso dall'ultima volta | *Procedi lo stesso* = prima sincronizzazione, senza cestino |
| Troppi file da mettere nel cestino insieme | *Procedi lo stesso* se è voluto |
| Un'altra sincronizzazione della stessa cartella in corso | aspetta che finisca |
| Tra Analizza e Applica è cambiato qualcosa | mostra la nuova anteprima, non applica quella vecchia |

**❓ Da decidere: c'è già una risposta proposta, la cambi con un clic.**
| Situazione | Scelte (proposta in grassetto) |
|---|---|
| Hai tolto da Resolve un bin la cui cartella contiene media | **cartella nel _CESTINO** · rimetti il bin in Resolve |
| Hai cancellato dal disco una cartella e le sue clip sono ora offline | **lascia il bin** (non viene più chiesto e la cartella non viene ricreata) · togli bin e clip |

**⚠️ Da sapere: lo segnalo e non tocco niente.**
- clip offline (file non trovato), clip con il file fuori dalla cartella, stesso file in due bin;
- file modificati da pochi secondi (forse ancora in copia): importati alla prossima analisi;
- schede camera (.RDC, BDMV, PRIVATE, XDROOT…): niente bin per ogni sottocartella, si importano a mano;
- una sequenza di immagini che sta in un bin diverso dalla sua cartella (le sequenze non le sposto);
- bin con la `/` nel nome o due bin con lo stesso nome nello stesso posto;
- bin e cartella che cambiano solo per le maiuscole (sul Mac sono la stessa cartella).

Casi che si risolvono da soli:
- cartella cancellata ma i file spostati altrove: le clip seguono i file e il bin vuoto si toglie;
- cartella vuota cancellata: il bin vuoto si toglie (un bin con timeline non si toglie mai);
- rinomina verso una cartella che esiste già: le cartelle si uniscono, i doppioni prendono `_1`;
- un Applica interrotto a metà: alla volta dopo la differenza viene vista e completata.

## Novità 0.4

**Automatico.** Con l'interruttore acceso l'app guarda la cartella ogni 10 secondi (si cambia in
*Impostazioni*) e importa da sola file, sequenze e cartelle nuove nel bin giusto. Fa **solo
aggiunte**: spostamenti, rinomine e rimozioni restano alla sincronizzazione manuale con anteprima,
che li vede ancora come da fare. Resolve viene interrogata solo quando sul disco è cambiato
qualcosa. Si mette **in pausa** da sola (e riparte da sola) se il disco si scollega, se in Resolve
è aperto un altro progetto o se Resolve è chiusa. Serve una prima sincronizzazione manuale.

**Filtri** (*Impostazioni*, salvate in `<radice>/.binmirror/settings.json`, così valgono su ogni Mac):
- estensioni ammesse, es. `mov, mxf, wav` (vuoto = tutte quelle che Resolve legge);
- cartelle ignorate, maiuscole indifferenti; tra slash è una regex (`/_AME/`). Di default
  `untitled folder` e `cartella senza titolo`, così una cartella appena creata nel Finder non
  diventa un bin prima che tu la rinomini. `_CESTINO` e `.binmirror` sono sempre ignorate.
  Le clip con estensioni escluse o in cartelle ignorate non vengono toccate (né cestinate).

**Sequenze di immagini** (`shot_0001.exr`…, almeno 5 fotogrammi): importate come una clip sola nel
bin della loro cartella. Se la sequenza cresce (render in corso) la clip si aggiorna invece di
importarne una seconda; l'ultimo fotogramma si aspetta che sia finito di scrivere. Una sequenza
tolta da Resolve non viene reimportata e i suoi file non vanno nel cestino.

**Data di import** nei metadati del Media Pool, formato `AAAA-MM-GG HH:MM` (ordinabile): di default
nel campo **Comments**, scritta **solo se il campo è vuoto**, così i tuoi commenti non vengono
toccati. Si può scegliere Description, Keywords o nessuno.

**Timeline dalla cartella** (opzione, spenta di default): i file `.drt .xml .fcpxml .otio` diventano
timeline nel bin della loro cartella, una volta sola (se sposti il file non viene reimportato).
Le sorgenti non vengono reimportate: le clip sono già nel Media Pool.

**Notifica** a fine sincronizzazione o import automatico: «3 clip importate in SC_01».

Da riga di comando: `python3 -m binmirror osserva "<radice>" --secondi 10`.

## Velocità

- Tutte le modifiche in Resolve partono in **una sola chiamata**, che restituisce anche la foto
  finale del Media Pool (prima erano tre chiamate).
- Import, spostamenti di clip e relink verso lo stesso bin/cartella diventano **una sola** chiamata
  a Resolve (`ImportMedia`, `MoveClips`, `RelinkClips` accettano liste): 200 file = 1 chiamata.
- Dentro Resolve l'albero si cammina una volta e poi si usano indici, non una volta per azione.
- Un Applica non viene mai ripetuto con un altro Python se si interrompe a metà.
- In automatico a ogni giro si guarda solo il disco; Resolve si interroga solo se è cambiato qualcosa.
- Delle sequenze si legge la data solo dell'ultimo fotogramma, non di tutti.

## Come funziona

Tre foto a confronto: il disco, il Media Pool e lo **stato** dell'ultima sincronizzazione
(`<radice>/.binmirror/state.json`). Senza lo stato non si potrebbe capire chi ha cambiato cosa.

- File e cartelle sono riconosciuti dal loro id sul disco (inode), così uno spostamento non sembra
  "cancellato + nuovo". Sui dischi **exFAT** l'id cambia con lo spostamento: allora si riconoscono
  da dimensione + data di modifica (e, per le cartelle, dal contenuto).
- I bin sono riconosciuti da `Folder.GetUniqueId()`. Se la tua versione di Resolve non lo ha,
  BinMirror non mette mai nel cestino una cartella per un bin sparito (non può distinguerlo da una
  rinomina).
- Resolve **non avvisa** gli script quando cambi qualcosa nel Media Pool: per questo si sincronizza
  su comando (poi arriverà la modalità automatica, che rifà il confronto ogni pochi secondi).
- La parte Resolve gira in un processo separato (`binmirror/resolve_helper.py`), come in DropCut.

## Requisiti

- **DaVinci Resolve Studio** (lo scripting da fuori Resolve nella versione free non è disponibile).
- In Resolve: *Preferences → System → General → External scripting using = Local*, poi riavvia.
- Python 3.8+ (sul Mac va bene quello dei Command Line Tools). Nessuna libreria esterna.

## L'app

![BinMirror](docs/anteprima.png)

1. In Resolve: *Workspace → Scripts → BinMirror* (oppure apri `/Applications/BinMirror.app`).
2. Scegli la **cartella su disco**. Se vuoi rispecchiare solo un bin, scrivi il suo nome
   (es. `MATERIALI`); vuoto = tutto il Master.
3. **Analizza** mostra cosa cambierebbe su disco e in Resolve. Non tocca niente.
4. **Applica** esegue. Ricorda cartella e bin per la volta dopo (`~/.binmirror_config.json`).
5. **Automatico** importa da solo i file nuovi; **Impostazioni…** per filtri, data di import,
   timeline e ogni quanti secondi guardare.

Log: `~/Library/Logs/BinMirror.log`.

## Costruire il DMG (sul Mac)

Serve un Python con Tcl/Tk 8.6 (python.org 3.12) e i Command Line Tools. Il DMG finisce in `~/Documents/CLAUDE/BinMirror`:

```bash
cd "$HOME" && { [ -d "$HOME/binmirror/.git" ] || git clone https://github.com/andrearicciotti1/binmirror.git "$HOME/binmirror"; } && cd "$HOME/binmirror" && git pull && ./macos/build_dmg.sh
```

Il .pkg installa `/Applications/BinMirror.app` e il launcher in *Workspace → Scripts*. L'app include
un Python portatile per parlare con Resolve anche sui Mac senza Command Line Tools. Non è firmata:
sul Mac dove la installi si apre lo stesso (il postinstall toglie la quarantena).

## Riga di comando (prova veloce, progetto di prova!)

Crea in Resolve un **progetto nuovo e vuoto**, aprilo, poi nel Terminale:

```bash
cd "$HOME" && { [ -d "$HOME/binmirror/.git" ] || git clone https://github.com/andrearicciotti1/binmirror.git "$HOME/binmirror"; } && cd "$HOME/binmirror" && git pull && mkdir -p "$HOME/Documents/CLAUDE/BinMirror/BINMIRROR_TEST/INTERVISTE" "$HOME/Documents/CLAUDE/BinMirror/BINMIRROR_TEST/MUSICA" && python3 -m binmirror analizza "$HOME/Documents/CLAUDE/BinMirror/BINMIRROR_TEST"
```

Copia qualche file video/audio dentro `~/Documents/CLAUDE/BinMirror/BINMIRROR_TEST/INTERVISTE` e `MUSICA`, poi:

```bash
cd "$HOME/binmirror" && python3 -m binmirror applica "$HOME/Documents/CLAUDE/BinMirror/BINMIRROR_TEST"
```

Mostra l'anteprima e chiede conferma. Opzioni: `--bin NOME` rispecchia solo quel bin invece di
tutto il Master; `--si` applica senza chiedere; `--forza` supera i blocchi superabili (cestino grande, progetto o bin diverso dall'ultima volta).

## Verifica sulla Resolve vera

✅ 14/10/2026, DaVinci Resolve Studio 21.1.1 su macOS, progetto vuoto + `~/Documents/CLAUDE/BinMirror/BINMIRROR_TEST`,
con `/usr/bin/python3` (3.9) dei Command Line Tools: import nei bin, clip spostata tra bin,
file spostato su disco, cartella rinominata su disco, bin spostato dentro un altro bin, clip e bin
tolti da Resolve (finiti in `_CESTINO`). Dopo ogni `applica` le clip restano Online e `analizza`
dice "allineati". Funzionano `Folder.GetUniqueId`, `MoveFolders`, `MoveClips`, `DeleteFolders`,
`RelinkClips`, `GetClipProperty("File Path")`.

Nota: `Folder` non ha `SetName` nelle API, quindi una cartella rinominata su disco diventa in Resolve
un bin nuovo in cui si spostano le clip (il vecchio, vuoto, si toglie).

⚠️ Non ancora provato sulla Resolve vera: rinominare un bin a mano, i dischi exFAT, `DeleteClips`
(scelta «togli bin e clip»), le domande e i blocchi della 0.3, e tutta la 0.4. In particolare:
l'import di sequenze con `ImportMedia([{"FilePath", "StartIndex", "EndIndex"}])`, l'aggiornamento
di una sequenza cresciuta con `RelinkClips` (se Resolve non prende i fotogrammi nuovi lo dice e va
reimportata a mano), `SetMetadata` e `ImportTimelineFromFile`.

## Test

```bash
python3 -m unittest discover -s tests -v
```

`tests/fake_fusionscript.py` simula Resolve; `tests/test_mirror.py` contiene gli scenari.
