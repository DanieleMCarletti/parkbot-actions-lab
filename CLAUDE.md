# parkbot-actions-lab — guida rapida per Claude

Bot di prenotazione automatica del parcheggio aziendale "Milanofiori Nord"
(Assago), pensato per girare **senza alcun server/PC acceso**: tutto vive su
GitHub Actions (cron + webhook) e un Cloudflare Worker gratuito.

Questo repo è un'**evoluzione architetturale** di un progetto precedente,
`milanofiori_automation` (eseguito localmente via systemd/Task Scheduler +
Playwright/Edge CDP). Il modulo `src/parkbot/` qui dentro è quasi identico
byte-per-byte a quello originale (solo `cli.py` differisce) — quel codice
core è stato sviluppato con l'assistenza di Claude Code (vedi commit
`01815f6` in `milanofiori_automation`, firmato
`Co-Authored-By: Claude Opus 4.8`). La migrazione a GitHub Actions/Cloudflare
fatta in questo repo invece non ha tracce di co-authorship AI nei commit.

## Flusso end-to-end

```
Utente → /park giovedì → Telegram → Cloudflare Worker (worker/worker.js)
                                          │ inoltra come repository_dispatch
                                          ▼
                              GitHub Actions: bot.yml (gestisce comando, aggiorna queue/)
                                          │
                              GitHub Actions: midnight-fire.yml (cron 00:00 ogni notte)
                                          │ esegue `parkbot fire`
                                          ▼
                              API Cognito + portale MFN → prenotazione
                                          │
                              Notifica: Telegram + GitHub Issue (auto-chiusa)
```

## File chiave

| File | Ruolo |
|---|---|
| `src/parkbot/cli.py` | entry point CLI (`parkbot <comando>`); contiene `_cmd_fire`, il cuore della logica notturna |
| `src/parkbot/config.py` | costanti: endpoint Cognito/MFN, `DEFAULT_LOT_ID = 54` (⚠️ hardcoded per l'ufficio di Luigi, vedi sotto) |
| `src/parkbot/queue.py` | coda su filesystem: un file JSON per `data+tipo` in `queue/`, rinominato in `.done.json` / `.failed-*.json` a fine elaborazione |
| `src/parkbot/tokens.py` | refresh del token Cognito (OAuth2 refresh_token, ruota periodicamente ogni ~30gg) |
| `src/parkbot/booking.py` | chiamate API al portale Milanofiori Nord |
| `src/parkbot/notify.py` | invio notifiche Telegram (richiede `secrets/telegram.json`) |
| `src/parkbot/places/*` | integrazione ServiceNow Accenture Places (scrivania + parcheggio Assago) — **disabilitata** (`PLACES_ENABLED = False` in `places/config.py`) perché il portale richiede MFA interattivo ogni ~20 min e non è automatizzabile headless |
| `worker/worker.js` | Cloudflare Worker: riceve webhook Telegram → inoltra come `repository_dispatch` a GitHub. Il repo target è parametrico via `env.GITHUB_REPO`: `deploy-worker.yml` la passa come `--var GITHUB_REPO:${{ github.repository }}` ad ogni deploy via CI (sempre corretta, nessun file da editare per fork/clone); chi pubblica il Worker a mano da dashboard la imposta lì una tantum come Variable |
| `.github/workflows/midnight-fire.yml` | job notturno: 3 cron sfasati (20:00/20:20/20:40 UTC) per compensare i ritardi di scheduling di GitHub Actions; retry fino a 3 volte; notifica via GitHub Issue (creata e chiusa subito, solo per sfruttare l'email automatica) |
| `.github/workflows/bot.yml` | gestisce i comandi Telegram (`/park`, `/list`, `/cancel`, `/future`, `/help`) ricevuti via `repository_dispatch` |
| `.github/workflows/setup.yml` | provisioning one-shot per un nuovo utente: salva i secrets GitHub, registra il webhook Telegram, verifica il token MFN |
| `.github/workflows/probe.yml` | diagnostica sola-lettura (verifica token/API) |
| `.github/workflows/deploy-worker.yml` | CD automatico del Worker su push a `worker/**` (richiede secrets `CF_API_TOKEN`/`CF_ACCOUNT_ID`, non coperti da `setup.yml`) |
| `queue/` | stato reale committato nel repo (il bot è in uso, non solo prototipo) |
| `nsg-rules.txt` | regolamento del gioco di carte Netrunner — estraneo al bot, residuo di un comando `/card` sperimentale poi spostato nel Worker |

## Cose non ovvie da sapere

- **Idempotenza**: `parkbot fire` è progettato per essere rilanciato più volte senza doppie prenotazioni (per questo i 3 cron sfasati in `midnight-fire.yml` sono sicuri).
- **`DEFAULT_LOT_ID = 54`** in `config.py` identifica un *lotto/area* ad assegnazione automatica (commento: "Assegnazione Giornaliera, 127 spots, auto-assigned"), **non uno stallo specifico** — il posto preciso lo assegna sempre il portale, anche prenotando "da umano". Probabilmente è lo stesso per chiunque abbia lo stesso tipo di accesso a Milanofiori Nord; va cambiato solo se un utente ha un profilo associato a un lotto diverso (si scopre da un 404/401 in fase di prenotazione pur con token valido — vedi README).
- **`secrets/` non è mai nel repo** (`.gitignore`), viene ricreato ad ogni run dei workflow a partire dai GitHub Secrets (`COGNITO_REFRESH_TOKEN`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`).
- **Refresh token Cognito**: scade periodicamente (~30gg) e quando ruota va aggiornato manualmente il secret GitHub (il job lo segnala ma non lo scrive da solo per sicurezza) — vedi la guida linkata nel README/`notify.py`.
- **Il repo è pensato come "template"**: README ha una sezione "Setup per un nuovo utente" con l'assunto che ognuno usi il proprio account GitHub + Cloudflare + bot Telegram. Per funzionare davvero per un secondo utente mancano ancora due cose (vedi sotto).

## Gap noti per il riuso multi-utente

1. ~~URL repo hardcoded in `worker.js`~~ — **risolto in due passaggi**: prima spostato in `env.GITHUB_REPO` (ma messo per errore come valore fisso in `wrangler.toml`, lo stesso problema solo spostato — avrebbe rotto il Worker di chiunque clonasse il template e attivasse il deploy automatico). Soluzione finale: `deploy-worker.yml` passa `--var GITHUB_REPO:${{ github.repository }}` a `wrangler deploy`, quindi è sempre corretto per qualunque fork senza editare nulla. `wrangler.toml` non definisce più `GITHUB_REPO` di proposito (vedi commento nel file).
2. `DEFAULT_LOT_ID` — chiarito nel README: quasi certamente non va toccato (vedi sopra), da cambiare solo in caso di errore di prenotazione.
3. Il `scheduled()` handler in `worker.js` (ping di `midnight-fire.yml` da Cloudflare Cron Triggers) non ha un corrispondente `[triggers] crons` in `wrangler.toml` — oggi è codice morto, il cron reale è quello GitHub Actions.
4. `deploy-worker.yml` richiede i secrets `CF_API_TOKEN`/`CF_ACCOUNT_ID`, mai menzionati in `setup.yml` o nel README — senza quei due secrets il deploy automatico del Worker fallisce silenziosamente (resta valida la via manuale "incolla il codice da dashboard").
