export default {
  async fetch(request, env) {
    if (request.method !== "POST") return new Response("OK");
    const body = await request.json().catch(() => null);
    const message = body?.message;
    if (!message) return new Response("OK");
    const chatId = String(message.chat?.id || "");
    if (chatId !== (env.ALLOWED_CHAT_ID || "NOT_SET")) {
      console.log(`chat_id mismatch: got "${chatId}", expected "${env.ALLOWED_CHAT_ID || "NOT_SET"}"`);
      return new Response("OK");
    }
    const text = (message.text || "").trim();
    if (!text.startsWith("/")) return new Response("OK");
    const parts = text.split(/\s+/);
    const command = (parts[0] || "").toLowerCase().replace(/@\S+$/, "");
    const args = parts.slice(1).join(" ");

    if (!env.GITHUB_REPO) console.log("GITHUB_REPO is not set");
    if (!env.GITHUB_PAT) console.log("GITHUB_PAT is not set");

    const headers = {
      "Authorization": `Bearer ${env.GITHUB_PAT}`,
      "Accept": "application/vnd.github+json",
      "Content-Type": "application/json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "parkbot-webhook/1.0",
    };
    const ghResp = await fetch(`https://api.github.com/repos/${env.GITHUB_REPO}/dispatches`, {
      method: "POST", headers,
      body: JSON.stringify({
        event_type: "telegram-command",
        client_payload: { command, args, chat_id: chatId },
      }),
    });
    if (!ghResp.ok) {
      console.log(`GitHub dispatch failed: HTTP ${ghResp.status} — ${await ghResp.text()}`);
    } else {
      console.log(`GitHub dispatch OK for command "${command}"`);
    }
    return new Response("OK");
  },

  async scheduled(event, env, ctx) {
    const headers = {
      "Authorization": `Bearer ${env.GITHUB_PAT}`,
      "Accept": "application/vnd.github+json",
      "Content-Type": "application/json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "parkbot-webhook/1.0",
    };
    await fetch(`https://api.github.com/repos/${env.GITHUB_REPO}/actions/workflows/midnight-fire.yml/dispatches`, {
      method: "POST", headers,
      body: JSON.stringify({ ref: "main" }),
    });
  },
};
