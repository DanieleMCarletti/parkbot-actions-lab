---
layout: default
title: Rinnovo token Milanofiori Nord
---

# Come rinnovare il token MFN

Il token Cognito dura circa **30 giorni**. Quando scade, il bot ti avvisa su Telegram e le prenotazioni si bloccano. Segui questi passi per rinnovarlo.

---

## Passaggio 1 — Fai login sul portale dal browser Windows

1. Apri **Edge** sul PC Windows
2. Vai su `https://parcheggimilanofiorinord.it/app/login`
3. Apri **DevTools** (tasto `F12`) → tab **Network** → spunta **Preserve log**
4. Fai login con la tua **passkey Accenture**
5. Nel campo Filter scrivi `oauth2/token`
6. Clicca sulla request → tab **Response**
7. Copia il valore del campo `refresh_token` (inizia con `eyJ...`, è molto lungo)

## Passaggio 2 — Aggiorna il secret su GitHub

1. Vai su [github.com/DanieleMCarletti/parkbot-actions-lab/settings/secrets/actions](https://github.com/DanieleMCarletti/parkbot-actions-lab/settings/secrets/actions)
2. Clicca su **COGNITO_REFRESH_TOKEN** → **Update**
3. Incolla il nuovo valore → **Save**

## Passaggio 3 — Verifica

Vai su **Actions → Probe — verifica accesso API MFN → Run workflow** e controlla che il log mostri `✓ Portale MFN raggiungibile`.

---

Le prenotazioni in coda riprenderanno automaticamente la notte stessa.
