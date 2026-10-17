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

Checklist in ordine, da cima a fondo. Nessun passaggio richiede di scrivere o incollare codice: tutto il codice (Worker incluso) resta nel repo, tu fornisci solo credenziali tramite form di GitHub Actions o pagine di creazione account.

### 1. Account GitHub

- Se non hai un account GitHub, creane uno su [github.com/signup](https://github.com/signup).
- Nella pagina di questo repo, clicca **"Use this template" → "Create a new repository"**, dai un nome a scelta, imposta **privato**, crea.
- D'ora in poi tutti i passi "Actions" e "Settings → Secrets" si riferiscono al **tuo nuovo repo**, non a questo.

### 2. Bot Telegram

- Se non hai Telegram, installalo e crea un account.
- Apri la chat con `@BotFather` → manda `/newbot` → segui le istruzioni → salva il **token** che ti dà (formato `123456789:AABBcc...`).
- Manda `/start` al tuo nuovo bot (cercalo per nome su Telegram).
- Apri nel browser `https://api.telegram.org/bot<TOKEN>/getUpdates` (sostituisci `<TOKEN>`) → copia il numero in `"chat":{"id": ...}` → è il tuo **Chat ID**.

### 3. Token di accesso al portale parcheggi (MFN)

- Apri Edge su un PC Windows → vai su `https://parcheggimilanofiorinord.it/app/login`
- Premi F12 → tab **Network** → spunta **Preserve log** → fai login con la tua passkey Accenture
- Nella lista delle richieste cerca quella con `oauth2/token` → tab **Response** → copia il valore di `refresh_token` (stringa lunga che inizia con `eyJ...`)

### 4. GitHub Personal Access Token (PAT) — permanente

- Vai su [github.com/settings/tokens](https://github.com/settings/tokens) → **Generate new token (classic)**
- Scope da spuntare: `repo` + `workflow`
- Genera e copia il token
- ⚠️ **Non cancellarlo dopo il setup**: oltre a servire per il setup iniziale, resta in uso permanente al Worker Cloudflare per comunicare col tuo repo

### 5. Account Cloudflare

- Se non hai un account Cloudflare, creane uno su [dash.cloudflare.com/sign-up](https://dash.cloudflare.com/sign-up) (gratuito)
- Appena entrato nel dashboard, apri una volta **Workers & Pages** dal menu laterale: la prima volta Cloudflare ti chiede di scegliere un sottodominio `*.workers.dev` — sceglilo e conferma (serve solo questo, non creare manualmente nessun Worker qui)
- Vai su **My Profile → API Tokens → Create Token** → usa il template **"Edit Cloudflare Workers"** → crea → copia il token generato (è il tuo `CF_API_TOKEN`)
- Torna alla dashboard principale → nella barra laterale destra trovi il tuo **Account ID** → copialo (è il tuo `CF_ACCOUNT_ID`)

### 6. Salva i 2 secrets Cloudflare su GitHub

Nel tuo repo: **Settings → Secrets and variables → Actions → New repository secret**, crea:

| Nome secret | Valore |
|---|---|
| `CF_API_TOKEN` | il token creato al punto 5 |
| `CF_ACCOUNT_ID` | l'Account ID copiato al punto 5 |

Questi 2 sono l'**unico** inserimento manuale di secrets GitHub richiesto: tutto il resto lo scrivono i workflow nei prossimi due passi.

### 7. Lancia il workflow di setup

Nel tuo repo: **Actions → "Setup — configurazione iniziale parkbot" → Run workflow**, compila:

| Campo | Valore |
|---|---|
| `cognito_refresh_token` | il `refresh_token` catturato al punto 3 |
| `telegram_bot_token` | il token del bot creato al punto 2 |
| `telegram_chat_id` | il Chat ID copiato al punto 2 |
| `setup_pat` | il PAT permanente creato al punto 4 |

Lancia (**Run workflow**). In automatico: salva tutti i secrets rimanenti, calcola l'URL del tuo Worker Cloudflare e registra il webhook Telegram, verifica che il token MFN funzioni.

### 8. Pubblica il Worker

Nel tuo repo: **Actions → "Deploy Cloudflare Worker" → Run workflow**. Pubblica il codice del Worker e i suoi secrets interamente in automatico — nessun passaggio su dashboard Cloudflare.

### 9. Verifica

Manda `/start` poi `/help` al tuo bot Telegram: se ricevi la lista dei comandi, il setup è completo e funzionante.

---

## Riepilogo variabili e secrets

Utile solo come riferimento una volta fatto il setup — **non è un altro elenco di cose da fare**, i passi 1–9 sopra bastano.

**Secrets del repo GitHub** (Settings → Secrets and variables → Actions):

| Nome | Chi lo crea |
|---|---|
| `CF_API_TOKEN` | tu, manualmente (punto 6) |
| `CF_ACCOUNT_ID` | tu, manualmente (punto 6) |
| `COGNITO_REFRESH_TOKEN` | automatico — `setup.yml` (punto 7) |
| `TELEGRAM_BOT_TOKEN` | automatico — `setup.yml` (punto 7) |
| `TELEGRAM_CHAT_ID` | automatico — `setup.yml` (punto 7) |
| `WORKER_GITHUB_PAT` | automatico — `setup.yml` (punto 7), dal PAT inserito nel form |

**Secrets/variabili sul Cloudflare Worker** (gestiti automaticamente da `deploy-worker.yml`, punto 8 — non toccarli mai a mano su dashboard):

| Nome | Tipo | Da dove arriva |
|---|---|---|
| `GITHUB_PAT` | Secret | secret GitHub `WORKER_GITHUB_PAT` |
| `ALLOWED_CHAT_ID` | Secret | secret GitHub `TELEGRAM_CHAT_ID` |
| `GITHUB_REPO` | Variable | calcolato da `deploy-worker.yml` (`${{ github.repository }}`) |

Nessuno di questi tre va mai impostato o modificato a mano nel dashboard Cloudflare: ad ogni deploy automatico (passo 8, o ogni `git push` su `worker/**`) `deploy-worker.yml` li riscrive da zero coi valori corretti. Impostarli manualmente da dashboard non avrebbe effetto duraturo — verrebbero sovrascritti al deploy successivo.

---

## Comandi Telegram

| Comando | Descrizione |
|---|---|
| `/park <data>` | Prenota — **subito** se la data è già nella finestra prenotabile del portale (risposta immediata con posto assegnato e posti liberi rimasti), altrimenti aggiunta in coda e ritentata ogni notte |
| `/list` | Mostra coda e prenotazioni confermate |
| `/future` | Prenotazioni confermate sul portale |
| `/cancel <data>` | Rimuovi dalla coda (non tocca prenotazioni già confermate sul portale) |
| `/rebook <data>` | Cancella la prenotazione attiva e ne prenota una nuova al suo posto (richiede conferma esplicita, vedi sotto) |
| `/spots` | Posti liberi, giorno per giorno, in tutta la finestra prenotabile attuale |
| `/tokenstatus` | Verifica se il token MFN è ancora valido e, se noto, la data di scadenza esatta — utile prima di partire per le ferie |
| `/help` | Lista comandi |

Date accettate: `oggi`, `domani`, `dopodomani`, `lunedì`…`domenica`, `gg/mm`, `gg/mm/aaaa`

`/park` prova prima la prenotazione immediata perché il portale accetta prenotazioni solo entro una finestra di qualche giorno in avanti (si allarga di un giorno ogni notte): se la data richiesta è già dentro quella finestra, succede tutto all'istante; se è troppo lontana nel tempo, resta in coda — non c'è modo di "bypassare" il limite imposto dal portale stesso.

Il tuo account permette **una sola prenotazione attiva alla volta** (confermato da `GET /app/api/me`, campo `n_max_prenotazioni_attivabili: 1`): se `/park` risponde "nessun posto disponibile", nella maggior parte dei casi è perché hai già una prenotazione confermata per un'altra data. La data resta comunque in coda e viene ritentata ogni notte — usa `/spots` per farti un'idea della finestra, oppure `/rebook` se vuoi spostare subito la prenotazione attiva su una data diversa.

### `/rebook` — cambiare la data della prenotazione attiva

Dato che puoi avere una sola prenotazione attiva, cambiarla richiede **prima cancellarla e poi riprovare a prenotare la nuova data** — non è possibile verificare in anticipo che la nuova data abbia posto prima di rinunciare a quella attuale. Per questo `/rebook` richiede due passaggi:

1. `/rebook <data>` — mostra un avviso con la prenotazione attuale che verrebbe cancellata e il rischio (se la nuova data fallisce, resti senza nessuna prenotazione confermata, anche se quella nuova resta comunque in coda per il ritento notturno)
2. `/rebook <data> conferma` — esegue davvero: cancella quella attuale e prova a prenotare la nuova

Non è un'azione a un solo comando apposta, per evitare cancellazioni accidentali.

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
  deploy-worker.yml             # Pubblica il Worker (codice + secrets) — passo 8 del setup
```
