# parkbot-actions-lab

Repo di sperimentazione per eseguire [parkbot](https://github.com/lsambolino/milanofiori_automation) su GitHub Actions invece che su un PC acceso a mezzanotte.

## Setup (una-tantum)

### 1. Crea il repo su GitHub

```bash
# Dalla cartella parkbot-actions-lab
git init
git add .
git commit -m "init"
gh repo create parkbot-actions-lab --private --source=. --push
```

Se `gh` non è installato, crea il repo manualmente su github.com → Settings → New repository (privato), poi:

```bash
git remote add origin https://github.com/<tuo-username>/parkbot-actions-lab.git
git push -u origin main
```

### 2. Aggiungi i secrets su GitHub

Settings → Secrets and variables → Actions → New repository secret:

| Nome | Valore |
|---|---|
| `COGNITO_REFRESH_TOKEN` | contenuto di `~/.local/share/parkbot/secrets/tokens.json` → campo `refresh_token` |
| `TELEGRAM_BOT_TOKEN` | contenuto di `~/.local/share/parkbot/secrets/telegram.json` → campo `bot_token` |
| `TELEGRAM_CHAT_ID` | contenuto di `~/.local/share/parkbot/secrets/telegram.json` → campo `allowed_chat_id` |

Per leggere i valori in locale:

```bash
cat ~/.local/share/parkbot/secrets/tokens.json
cat ~/.local/share/parkbot/secrets/telegram.json
```

### 3. Fase 0 — testa la connettività

Actions → "Probe — verifica accesso API MFN" → Run workflow

Controlla il log: se vedi `✓ Portale MFN raggiungibile da GitHub Actions` sei a posto.
Se fallisce (IP bloccato, geo-restriction), il progetto si ferma qui.

### 4. Aggiungi una prenotazione di test

Per testare il job notturno manualmente, aggiungi un file nella cartella `queue/`:

```bash
# Crea un file prenotazione (es. per il 2026-07-15)
cat > queue/2026-07-15-parking.json << 'EOF'
{"date": "2026-07-15", "type": "parking", "source": "manual", "queued_at": "2026-07-08T00:00:00"}
EOF
git add queue/
git commit -m "test: aggiungo prenotazione manuale"
git push
```

Poi: Actions → "Midnight Fire" → Run workflow → deseleziona "dry run" → Run.

## Come funziona

1. Il bot Telegram locale (o su Railway, fase 2) aggiunge file in `queue/` e fa push
2. Ogni notte alle 00:00 (ora italiana) GitHub Actions esegue `parkbot fire`:
   - Legge i file in `queue/`
   - Tenta le prenotazioni tramite API Cognito
   - Rinomina i file in `.done.json` o `.failed-*.json`
   - Commita le modifiche alla queue
   - Invia notifica Telegram con il posto assegnato

## Rinnovo token (~30 giorni)

Quando il refresh_token Cognito scade, il job fallisce con `invalid_grant`.

Per rinnovarlo:
1. In locale: `DISPLAY=:0 parkbot bootstrap` (login interattivo con MFA)
2. Leggi il nuovo token: `cat ~/.local/share/parkbot/secrets/tokens.json`
3. Aggiorna il secret su GitHub: Settings → Secrets → `COGNITO_REFRESH_TOKEN` → Update

## Struttura

```
queue/          # Prenotazioni pendenti (.json), completate (.done.json), fallite (.failed-*.json)
.github/
  workflows/
    probe.yml           # Test connettività (esegui una volta per verificare)
    midnight-fire.yml   # Job notturno (gira automaticamente a mezzanotte)
```
