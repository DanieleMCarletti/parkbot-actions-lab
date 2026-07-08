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
- Sostituisci il codice con quello qui sotto:

```javascript
export default {
  async fetch(request, env) {
    if (request.method !== "POST") return new Response("OK");
    const body = await request.json().catch(() => null);
    const message = body?.message;
    if (!message) return new Response("OK");
    const chatId = String(message.chat?.id || "");
    if (chatId !== (env.ALLOWED_CHAT_ID || "NOT_SET")) return new Response("OK");
    const text = (message.text || "").trim();
    const parts = text.split(/\s+/);
    const command = (parts[0] || "").toLowerCase().replace(/@\S+$/, "");
    const args = parts.slice(1).join(" ");
    const headers = {
      "Authorization": `Bearer ${env.GITHUB_PAT}`,
      "Accept": "application/vnd.github+json",
      "Content-Type": "application/json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "parkbot-webhook/1.0",
    };
    await fetch("https://api.github.com/repos/<TUO_USERNAME>/<TUO_REPO>/dispatches", {
      method: "POST", headers,
      body: JSON.stringify({
        event_type: "telegram-command",
        client_payload: { command, args, chat_id: chatId },
      }),
    });
    return new Response("OK");
  },
};
```

> ⚠️ Sostituisci `<TUO_USERNAME>/<TUO_REPO>` con il tuo repo GitHub (es. `MarioRossi/parkbot-actions-lab`)

- Settings → Variables and Secrets → aggiungi:
  - `GITHUB_PAT`: un GitHub PAT classic con scope `repo`+`workflow` (può essere lo stesso del setup o uno nuovo)
  - `ALLOWED_CHAT_ID`: il tuo Telegram Chat ID (numero)
- Deploy

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

## Rinnovo token MFN (~30 giorni)

Quando il bot avvisa che il token è scaduto: [segui questa guida](https://gist.github.com/DanieleMCarletti/f72f1843f08c77a9bf8f96813e57a7dd)

---

## Struttura

```
queue/                          # Prenotazioni pendenti/completate/fallite
src/parkbot/                    # Codice parkbot (da milanofiori_automation)
.github/workflows/
  setup.yml                     # Setup iniziale (eseguire una volta)
  probe.yml                     # Test connettività API
  midnight-fire.yml             # Job notturno (automatico, 00:00)
  bot.yml                       # Gestione comandi Telegram
```
