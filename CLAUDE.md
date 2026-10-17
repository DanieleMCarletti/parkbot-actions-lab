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
| `src/parkbot/tokens.py` | refresh del token Cognito (OAuth2 refresh_token, ruota periodicamente ogni ~30gg). `check_token_status()` verifica validità + prova a leggere la scadenza decodificando il refresh_token come JWT (`_decode_jwt_exp`, senza verifica firma, best-effort — torna `None` se il token non è un JWT a 3 segmenti) |
| `src/parkbot/booking.py` | chiamate API al portale Milanofiori Nord. `try_book_now()` tenta una prenotazione immediata (usata da `/park` su Telegram) invece di passare dalla coda — funziona solo se la data è già dentro la `BookingWindow`; include conteggio posti liberi leggendo `GET /parcheggi-disponibili`. ⚠️ Quella risposta ha DUE campi facilmente confondibili: `posti` è il catalogo fisso di tutti i 127 codici del lotto (sempre `len(posti) == n_posti`, include anche i posti già prenotati — **non usarlo come "posti liberi"**, errore fatto e corretto il 2026-10-12), mentre `postiDisponibili` è la lista di quelli davvero liberi in quel momento. `list_window_availability()` ripete la chiamata per ogni giorno tra `min_date` e `max_date` (comando `/spots`). `cancel_confirmed_booking()` e `rebook()` gestiscono la cancellazione reale e il cambio data — vedi nota dedicata sotto |
| `src/parkbot/notify.py` | invio notifiche Telegram (richiede `secrets/telegram.json`) |
| `src/parkbot/places/*` | integrazione ServiceNow Accenture Places (scrivania + parcheggio Assago) — **disabilitata** (`PLACES_ENABLED = False` in `places/config.py`) perché il portale richiede MFA interattivo ogni ~20 min e non è automatizzabile headless |
| `worker/worker.js` | Cloudflare Worker: riceve webhook Telegram → inoltra come `repository_dispatch` a GitHub. Nessuno dei suoi 3 valori (`GITHUB_REPO`, `GITHUB_PAT`, `ALLOWED_CHAT_ID`) va mai impostato a mano su dashboard: li scrive tutti `deploy-worker.yml` ad ogni deploy (vedi sotto) |
| `.github/workflows/midnight-fire.yml` | job notturno: 3 cron sfasati (20:00/20:20/20:40 UTC) per compensare i ritardi di scheduling di GitHub Actions; retry fino a 3 volte; notifica via GitHub Issue (creata e chiusa subito, solo per sfruttare l'email automatica) |
| `.github/workflows/bot.yml` | gestisce i comandi Telegram (`/park`, `/list`, `/cancel`, `/rebook`, `/future`, `/spots`, `/tokenstatus`, `/help`) ricevuti via `repository_dispatch`. `/park` prova `try_book_now()` prima di mettere in coda. `/rebook <data>` richiede un secondo invio con `conferma` in coda prima di cancellare la prenotazione attiva — niente azioni distruttive a un solo comando |
| `.github/workflows/setup.yml` | provisioning one-shot per un nuovo utente: richiede `CF_API_TOKEN`/`CF_ACCOUNT_ID` già presenti come secrets, poi salva `COGNITO_REFRESH_TOKEN`/`TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`/`WORKER_GITHUB_PAT`, calcola da solo l'URL del Worker via API Cloudflare (`GET /accounts/{id}/workers/subdomain`, nessun input manuale per l'URL), registra il webhook Telegram, verifica il token MFN |
| `.github/workflows/probe.yml` | diagnostica sola-lettura (verifica token/API) |
| `.github/workflows/deploy-worker.yml` | pubblica **codice + secrets** del Worker in un colpo solo via `cloudflare/wrangler-action@v3`: `--var GITHUB_REPO:${{ github.repository }}` per il codice, input `secrets:` (che legge da `env.GITHUB_PAT`/`env.ALLOWED_CHAT_ID`, mappati rispettivamente ai secrets GitHub `WORKER_GITHUB_PAT`/`TELEGRAM_CHAT_ID`) per i secrets del Worker. Zero interazione con la dashboard Cloudflare, sia alla prima pubblicazione che ad ogni `git push` successivo su `worker/**` |
| `queue/` | stato reale committato nel repo (il bot è in uso, non solo prototipo) |
| `nsg-rules.txt` | regolamento del gioco di carte Netrunner — estraneo al bot, residuo di un comando `/card` sperimentale poi spostato nel Worker |

## Cose non ovvie da sapere

- **Idempotenza**: `parkbot fire` è progettato per essere rilanciato più volte senza doppie prenotazioni (per questo i 3 cron sfasati in `midnight-fire.yml` sono sicuri).
- **`DEFAULT_LOT_ID = 54`** in `config.py` identifica un *lotto/area* ad assegnazione automatica (commento: "Assegnazione Giornaliera, 127 spots, auto-assigned"), **non uno stallo specifico** — il posto preciso lo assegna sempre il portale, anche prenotando "da umano". Per un nuovo utente il valore corretto si legge direttamente da `GET /app/api/me` → campo `lotti_parcheggio_default_id` (confermato 2026-10-17, vedi sotto) — non serve più indovinarlo da un errore 404/401.
- **`secrets/` non è mai nel repo** (`.gitignore`), viene ricreato ad ogni run dei workflow a partire dai GitHub Secrets (`COGNITO_REFRESH_TOKEN`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`).
- **Refresh token Cognito**: scade periodicamente (~30gg) e quando ruota va aggiornato manualmente il secret GitHub (il job lo segnala ma non lo scrive da solo per sicurezza) — vedi la guida linkata nel README/`notify.py`.
- **Il repo è pensato come "template"**: README ha una checklist numerata (9 passi) "Setup per un nuovo utente" — account GitHub → bot Telegram → token MFN → PAT GitHub permanente → account Cloudflare → 2 secrets manuali (`CF_API_TOKEN`/`CF_ACCOUNT_ID`) → `setup.yml` → `deploy-worker.yml` → verifica. **Nessun passaggio richiede di scrivere/incollare codice o configurare manualmente il Worker da dashboard** — è tutto pilotato da GitHub Actions.
- **`CF_API_TOKEN`/`CF_ACCOUNT_ID` devono esistere PRIMA di lanciare `setup.yml`**: quel workflow li usa per interrogare l'API Cloudflare e calcolarsi da solo l'URL del Worker (così l'utente non deve mai leggerlo/copiarlo a mano). Se mancano, `setup.yml` fallisce subito con un messaggio esplicito (primo step del job).
- **RISOLTO (2026-10-17) — H2 confermata da dati di configurazione**: `GET /app/api/me` (mai chiamato prima d'ora dal codice, ma catturato dal browser dall'utente) torna il profilo dell'account con `"n_max_prenotazioni_attivabili": 1` e `"n_max_giorni_prenotabili": 3` — **una sola prenotazione attiva alla volta, finestra di 3 giorni**, nero su bianco dalla config dell'account, non più un'ipotesi. Lo stesso campo torna anche `"lotti_parcheggio_default_id": 54`, che conferma da dove viene (e dove si legge per un nuovo utente) il valore hardcoded `DEFAULT_LOT_ID` in `config.py`. Storia di come ci siamo arrivati (lasciata per il contesto): prima affermato come fatto senza prova in `cli._fire_parking_entry`, poi giustamente contestato dall'utente (aveva prenotato lunedì+giovedì insieme) e da me "confutato" con un test debole, poi chiuso definitivamente da questo endpoint. **Lezione**: quando un dato è verificabile da un endpoint reale, va interrogato — non va né affermato né confutato per via indiretta/euristica.
- **`cancel_confirmed_booking()` in `booking.py`**: cancella una prenotazione confermata (`DELETE /app/api/prenotazioni/{id}/delete`, 204 No Content, nessun body). Catturato dal browser dall'utente il 2026-10-17. `rebook()` combina cancellazione + nuova prenotazione in un'unica operazione, con outcome esplicito `"left_without_booking"` se la cancellazione riesce ma la nuova prenotazione fallisce (rischio reale dato `n_max_prenotazioni_attivabili: 1` — non c'è modo di validare la nuova data prima di cancellare la vecchia). Il comando Telegram `/rebook` richiede un secondo messaggio con `conferma` in coda prima di eseguire, mostrando il rischio esplicitamente — non è un'azione a un solo tocco.

## Gap noti per il riuso multi-utente (storico — tutti risolti)

1. ~~URL repo hardcoded in `worker.js`~~ — risolto: `deploy-worker.yml` passa `--var GITHUB_REPO:${{ github.repository }}`, sempre corretto per qualunque fork.
2. `DEFAULT_LOT_ID` — chiarito nel README: quasi certamente non va toccato, da cambiare solo in caso di errore di prenotazione.
3. Il `scheduled()` handler in `worker.js` (ping di `midnight-fire.yml` da Cloudflare Cron Triggers) non ha un corrispondente `[triggers] crons` in `wrangler.toml` — oggi è codice morto, il cron reale è quello GitHub Actions.
4. ~~`CF_API_TOKEN`/`CF_ACCOUNT_ID` non documentati~~ — risolto: sono il punto 6 della checklist, con spiegazione di dove trovarli.
5. ~~Snippet del Worker duplicato e disallineato nel README~~ — risolto: il README non contiene più codice, rimanda sempre al file e ora nemmeno chiede di incollarlo manualmente (lo pubblica `deploy-worker.yml`).
6. ~~`GITHUB_PAT`/`ALLOWED_CHAT_ID` da impostare a mano su dashboard Cloudflare~~ — risolto: anche questi 2 secrets del Worker sono pubblicati da `deploy-worker.yml` (vedi sopra), letti dai secrets GitHub `WORKER_GITHUB_PAT`/`TELEGRAM_CHAT_ID`. **Attenzione**: se un repo esistente (incluso quello originale di Daniele) non ha ancora il secret `WORKER_GITHUB_PAT`, il prossimo deploy del Worker scriverebbe un `GITHUB_PAT` vuoto sovrascrivendo quello buono già impostato a mano — va creato quel secret (valore: lo stesso PAT con scope `repo`+`workflow` usato in origine) PRIMA del prossimo deploy.
