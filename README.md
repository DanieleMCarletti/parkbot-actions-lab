# parkbot-actions-lab

Prenotazione automatica del parcheggio Milanofiori Nord via GitHub Actions + Telegram bot. Zero PC acceso a mezzanotte.

## Come funziona

```
/park giovedì  →  Telegram Bot  →  GitHub repo (queue/)
                                         ↓
                              GitHub Actions (00:00 ogni notte)
                                         ↓
                              Portale MFN API  →  Telegram notifica
```

- Il **bot Telegram** riceve i comandi e aggiorna la coda nel repo
- Il **job notturno** gira automaticamente a mezzanotte e prenota
- **Zero infrastruttura** da gestire: tutto su GitHub Actions (gratuito) + Cloudflare Workers (gratuito)

---

## Setup per un nuovo utente

### Prerequisiti (10 minuti)

**1. Crea il tuo repo da questo template**

Clicca **"Use this template"** → **"Create a new repository"** → nome a scelta, **privato**.

**2. Crea un bot Telegram**

- Apri Telegram → cerca `@BotFather` → `/newbot`
- Salva il token (formato `123456789:AABBcc...`)
- Manda `/start` al tuo nuovo bot
- Apri `https://api.telegram.org/bot<TOKEN>/getUpdates` → copia il numero `chat.id`

**3. Cattura il token MFN dal browser**

- Apri Edge sul PC Windows → `https://parcheggimilanofiorinord.it/app/login`
- F12 → Network → Preserve log → fai login con la tua passkey Accenture
- Filtra per `oauth2/token` → Response → copia `refresh_token` (inizia con `eyJ...`)

**4. Crea un GitHub PAT temporaneo per il setup**

- Vai su `https://github.com/settings/tokens` → **Generate new token (classic)**
- Scope: `repo` + `workflow`
- Copia il token (serve solo per il setup, poi puoi cancellarlo)

**5. Crea il Cloudflare Worker**

- Vai su [dash.cloudflare.com](https://dash.cloudflare.com) → Workers & Pages → Create Worker
- Nome: `parkbot-webhook` → Deploy
- Apri il file [`worker/worker.js`](worker/worker.js) di **questo repo** (quello nel tuo fork, non una copia incollata qui) e sostituisci con quel contenuto il codice del Worker su Cloudflare
  > Copia sempre dal file del repo, non da uno snippet salvato altrove: è l'unica versione aggiornata e viene mantenuta in sync col resto del progetto.
- Settings → Variables and Secrets → **Add variable**, aggiungi questi 3:

  | Nome | Tipo da selezionare | Valore |
  |---|---|---|
  | `GITHUB_PAT` | **Secret** | un GitHub PAT classic con scope `repo`+`workflow` (può essere lo stesso del setup o uno nuovo) |
  | `ALLOWED_CHAT_ID` | **Secret** | il tuo Telegram Chat ID (numero) |
  | `GITHUB_REPO` | **Variable** (non secret) | il tuo repo GitHub nel formato `<utente>/<nome-repo>` (es. `MarioRossi/parkbot-actions-lab`) |

- **Deploy**

> ℹ️ `GITHUB_REPO` qui è solo per far partire il Worker la prima volta. Appena abiliterai il deploy automatico (`deploy-worker.yml`, passo opzionale più sotto), quella Action sovrascrive `GITHUB_REPO` ad ogni deploy con il valore corretto per il tuo repo — quindi anche se te lo dimentichi o lo sbagli qui, si autocorregge al primo `git push`. `GITHUB_PAT` e `ALLOWED_CHAT_ID` invece, essendo **Secret**, non vengono mai toccati dai deploy automatici: restano quelli che hai impostato qui finché non li cambi tu a mano.

---

### Esegui il setup automatico

Vai su **Actions → "Setup — configurazione iniziale parkbot" → Run workflow**

Compila i campi:
| Campo | Valore |
|---|---|
| `cognito_refresh_token` | il `refresh_token` catturato dal browser |
| `telegram_bot_token` | il token del tuo bot Telegram |
| `telegram_chat_id` | il tuo chat ID Telegram |
| `cloudflare_worker_url` | es. `parkbot-webhook.xxx.workers.dev` |
| `setup_pat` | il GitHub PAT temporaneo creato al passo 4 |

Il workflow salva i secrets, registra il webhook e verifica che tutto funzioni.

---

## Riepilogo variabili e secrets

**Secrets del repo GitHub** (Settings → Secrets and variables → Actions):

| Nome | Come si imposta | Sopravvive ai deploy/push? |
|---|---|---|
| `COGNITO_REFRESH_TOKEN` | **automatico** — creato da `setup.yml` | sì, sempre |
| `TELEGRAM_BOT_TOKEN` | **automatico** — creato da `setup.yml` | sì, sempre |
| `TELEGRAM_CHAT_ID` | **automatico** — creato da `setup.yml` | sì, sempre |
| `CF_API_TOKEN` *(opzionale)* | manuale — solo se vuoi il deploy automatico del Worker | sì, sempre |
| `CF_ACCOUNT_ID` *(opzionale)* | manuale — solo se vuoi il deploy automatico del Worker | sì, sempre |

I tre secrets obbligatori **non vanno mai inseriti a mano**: li scrive `setup.yml` durante il setup iniziale, usando il PAT temporaneo. `CF_API_TOKEN`/`CF_ACCOUNT_ID` servono solo per abilitare `deploy-worker.yml` (Worker che si aggiorna da solo ad ogni `git push`, invece di incollare il codice a mano da dashboard): si creano su dash.cloudflare.com → My Profile → API Tokens (permesso "Edit Cloudflare Workers") e → barra laterale sotto il nome account.

**Variabili del Cloudflare Worker** (dashboard → `parkbot-webhook` → Settings → Variables and Secrets):

| Nome | Tipo | Come si imposta | Sopravvive ai deploy automatici? |
|---|---|---|---|
| `GITHUB_PAT` | Secret | manuale, una volta sola | sì — i Secret non vengono mai toccati da `wrangler deploy` |
| `ALLOWED_CHAT_ID` | Secret | manuale, una volta sola | sì — stesso motivo |
| `GITHUB_REPO` | Variable | manuale la prima volta (serve finché non fai il primo deploy via CI) | **no** — da quando abiliti `deploy-worker.yml`, la Action la sovrascrive ad ogni `git push` con `${{ github.repository }}`, sempre corretto per il tuo fork |

Questa distinzione non è un dettaglio: è esattamente il bug che abbiamo scovato durante lo sviluppo — avevamo messo `GITHUB_REPO` come valore fisso prima nel dashboard poi in un file del codice, e un deploy automatico l'avrebbe ogni volta sovrascritta/persa, interrompendo il bot in modo silenzioso. Oggi non serve più preoccuparsene: la Action la imposta da sola.

---

## Comandi Telegram

| Comando | Descrizione |
|---|---|
| `/park <data>` | Aggiungi prenotazione (es. `/park giovedì`, `/park 22/07`) |
| `/list` | Mostra coda e prenotazioni confermate |
| `/future` | Prenotazioni confermate sul portale |
| `/cancel <data>` | Rimuovi dalla coda |
| `/help` | Lista comandi |

Date accettate: `oggi`, `domani`, `dopodomani`, `lunedì`…`domenica`, `gg/mm`, `gg/mm/aaaa`

---

## Lotto di parcheggio (`lot_id`)

Il codice usa un `lot_id` di default (`DEFAULT_LOT_ID` in `src/parkbot/config.py`) che identifica il *lotto/area* di parcheggio ad assegnazione automatica — non uno stallo specifico (quello lo sceglie sempre il portale, come quando prenoti manualmente). Se un nuovo utente ha un profilo associato a un lotto diverso, le prenotazioni falliranno con un errore del portale pur con token valido: in quel caso va catturato il proprio `lotti_parcheggio_id` dal Network tab del browser (stesso procedimento usato per il `refresh_token`, cercando la richiesta `POST /prenotazioni`) e passato come override (`--lot-id`).

## Rinnovo token MFN (~30 giorni)

Quando il bot avvisa che il token è scaduto: [segui questa guida](https://gist.github.com/DanieleMCarletti/f72f1843f08c77a9bf8f96813e57a7dd)

---

## Struttura

```
queue/                          # Prenotazioni pendenti/completate/fallite
src/parkbot/                    # Codice parkbot (da milanofiori_automation)
worker/
  worker.js                     # Cloudflare Worker — webhook Telegram -> GitHub
  wrangler.toml                 # Config del Worker (nessun secret/valore d'ambiente qui)
.github/workflows/
  setup.yml                     # Setup iniziale (eseguire una volta)
  probe.yml                     # Test connettività API
  midnight-fire.yml             # Job notturno (automatico, 00:00)
  bot.yml                       # Gestione comandi Telegram
  deploy-worker.yml             # Deploy automatico del Worker (opzionale, vedi sopra)
```
