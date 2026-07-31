# RAG Study Telegram Bot

Study from your own materials with **RAG**, then take **MCQ / MSQ / Numerical / Theory** tests — with topics, streaks, mistake practice, session reports, and optional voice.

**Live bot:** [@mock_practice_bot](https://t.me/mock_practice_bot)  
**Repo:** https://github.com/ayushanand27/mock_practice_bot  
**Hosted on:** Azure VM (`104.208.98.207`, East Asia) — systemd service `mock-practice-bot`

---

## What we're building

A **Telegram study bot** that uses **RAG** (retrieval from your uploaded materials) for:

- **Learn** — Q&A grounded in your notes/PDFs  
- **Test** — MCQ, MSQ, numerical, theory (Easy / Medium / Hard)  
- **Progress** — streaks, stats, mistake review, session reports  
- **Voice Learn** — optional spoken answers (Sarvam TTS)

**Categories (7):** Placement (BTech CSE), JEE, NEET, Class 11 (NCERT), Class 12 (NCERT), SSC CGL, UPSC.

Each category → **topic/chapter** → **Learn** / **Test** / **Practice mistakes**.

Upload flow: send PDFs/txt/md via Telegram **Upload**, or drop files under `data/materials/<category>/` on the server and run `/reindex`.

---

## Status: done vs left

| Done | Left (owner) |
|------|----------------|
| Bot features (Learn, Test, topics, progress, voice, upload) | Upload **real study PDFs** (biggest quality win) |
| Azure VM deploy + systemd auto-start | Optional: **rotate** API keys if they were exposed in chat |
| RAG (Chroma + sentence-transformers) + offline fallback | Optional: better LLM (OpenRouter key, higher Workers AI model) for Groq-quality answers |
| Cloudflare Worker LLM proxy deployed | |
| Azure → Worker → **Workers AI** path verified (`X-Relay: workers-ai`) | |
| `LLM_PROXY_URL` set on VM; proxy code on branch `cursor/cloudflare-llm-proxy` | |

---

## Current flow (Learn / Test on Azure today)

When a student asks a Learn question or starts a Test:

1. Student chats with [@mock_practice_bot](https://t.me/mock_practice_bot) on Telegram.  
2. **Azure VM** (`104.208.98.207`) runs `bot.py` via systemd (`mock-practice-bot`).  
3. **RAG** retrieves relevant chunks from uploaded materials (Chroma + sentence-transformers).  
4. For generation, the bot calls **`LLM_PROXY_URL`** → Cloudflare Worker:  
   `https://mock-practice-groq-proxy.ayushanand27-mp.workers.dev/openai/v1`  
5. The Worker tries providers in order: **Workers AI** (primary) → Groq → Gemini.  
6. **Today on Azure:** requests succeed via **Workers AI** (`@cf/meta/llama-3.1-8b-instruct-fp8`).  
   Response header: `X-Relay: workers-ai`.  
7. If all LLM providers fail, the bot falls back to **offline RAG** (answers built from retrieved note chunks).

```
Telegram  →  Azure VM (bot.py)  →  RAG (Chroma)
                      ↓
              LLM_PROXY_URL (Cloudflare Worker)
                      ↓
         Workers AI ✓  |  Groq ✗ (403)  |  Gemini ✗ (geo/quota)
```

---

## Why Groq looked "blocked"

This is **API provider geo/datacenter policy**, not a bug in the bot.

| Observation | Detail |
|-------------|--------|
| Groq from laptop/home IP | Works |
| Groq from **Azure East Asia** VM IP | **HTTP 403 Forbidden** |
| Worker colo when Azure calls in | Often **HKG** (Hong Kong) — Groq still **403** |
| `locationHint: wnam` on Durable Object | Did **not** fix it; Groq still 403 when traffic enters via Azure/HKG |
| Gemini from Azure | Fails (location / quota); Worker Gemini secret often **429** quota |

**Bottom line:** Direct Groq/Gemini from the Azure datacenter IP is blocked. The Worker routes around that by using **Cloudflare Workers AI** as the primary relay, which accepts requests from Azure.

---

## What YOU need to do next (owner checklist)

### 1. Upload real study PDFs (most important)
Quality of answers depends on materials.

**Option A — Telegram (easiest)**  
1. Open [@mock_practice_bot](https://t.me/mock_practice_bot)  
2. Tap **Upload** → pick category  
3. Send `.pdf` / `.txt` / `.md`  
4. Wait for “Indexed…”  

**Option B — folders on your PC, then push / copy to Azure**

| Category | Folder |
|----------|--------|
| Placement | `data/materials/placement/` |
| JEE | `data/materials/jee/` |
| NEET | `data/materials/neet/` |
| Class 11 | `data/materials/ncert_11/` |
| Class 12 | `data/materials/ncert_12/` |
| SSC CGL | `data/materials/ssc_cgl/` |
| UPSC | `data/materials/upsc/` |

Good free sources for notes (examples):  
- NCERT: https://ncert.nic.in  
- OpenStax / other open textbooks (check license)  
- Your own coaching notes (PDF export)

After adding files on the **server**, run `/reindex` in Telegram.

**Azure server path:**  
`/home/azureuser/mock_practice_bot/data/materials/<category>/`

### 2. Keep secrets safe
- Never commit `.env`  
- If API keys were pasted in chat — **rotate** Telegram bot token (BotFather `/revoke`), Groq, Sarvam, Gemini when you can  
- Put new keys only in `.env` on the VM (or update via SSH)

### 3. Optional: stronger LLM
Current path uses Workers AI `llama-3.1-8b` (free, works from Azure). For richer answers:
- Add an **OpenRouter** (or similar cloud-friendly) key and wire it in, or  
- Upgrade the Workers AI model in `deploy/cloudflare-groq-proxy/` and redeploy the Worker.

### 4. Day-to-day ops
- Bot auto-starts on the VM (`systemd`)  
- Redeploy bot after code changes: `bash scripts/deploy-azure.sh`  
- Redeploy LLM proxy: `cd deploy/cloudflare-groq-proxy && npx wrangler deploy`  
- Logs: `sudo journalctl -u mock-practice-bot -f`  
- Stop local `python bot.py` on your laptop so it doesn’t conflict with Azure  

### 5. Share the bot
Send students: https://t.me/mock_practice_bot  
Remind them: Upload their own notes → Learn / Test.

---

## Features (current)

| Feature | How |
|---------|-----|
| Learn (RAG) | Category → topic → ask a question |
| Test | MCQ / MSQ / Numerical / Theory + Easy/Medium/Hard |
| Topics / chapters | After category, pick Physics / DSA / Polity / … |
| Progress / streak | `/stats` or **Progress** |
| Review mistakes | `/review` or **Review** |
| Practice mistakes | Category → **Practice mistakes** |
| Session report | End test → **End + report** |
| Voice Learn | Short answers may auto-speak; or **Hear answer** (Sarvam) |
| Upload | PDF/txt/md → auto reindex |
| Notes / Reminders / Interview | Secondary menu / commands |

---

## Quick start (local)

```bash
cp .env.example .env
# Set BOT_TOKEN, GROQ_API_KEY, SARVAM_API_KEY, optional GEMINI_API_KEY

python -m venv .venv
source .venv/Scripts/activate   # Windows Git Bash
pip install -r requirements.txt
python bot.py
```

On a laptop, Groq often works directly (no proxy needed). On Azure, set `LLM_PROXY_URL` (see below).

---

## Env vars

| Var | Role |
|-----|------|
| `BOT_TOKEN` | Telegram (BotFather) |
| `GROQ_API_KEY` | Groq LLM (works on laptop; blocked from Azure datacenter IPs) |
| `GEMINI_API_KEY` | Google AI Studio key (often blocked / quota-limited from cloud) |
| `SARVAM_API_KEY` | Voice TTS/STT (+ optional chat if enabled) |
| `GROQ_MODEL` | Optional, default `llama-3.3-70b-versatile` |
| `GEMINI_MODEL` | Optional, default `gemini-2.0-flash` |
| `LLM_PROXY_URL` | OpenAI-compatible proxy base URL (required on Azure VM) |

**Azure VM (production):**

```env
LLM_PROXY_URL=https://mock-practice-groq-proxy.ayushanand27-mp.workers.dev/openai/v1
```

Never commit `.env` or API keys.

---

## LLM proxy (Cloudflare Worker)

Code: `deploy/cloudflare-groq-proxy/`  
Deploy: `cd deploy/cloudflare-groq-proxy && npx wrangler deploy`  
Secrets (set via `wrangler secret put`, not in git): `GROQ_API_KEY`, `GEMINI_API_KEY`, optional `PROXY_TOKEN`.

Provider order inside the Worker: **Workers AI** → Groq → Gemini.  
Azure production traffic uses Workers AI today.

---

## Architecture

```
bot.py                 # Wiring, commands, post_init index
config.py              # Categories, topics, paths
keyboards.py           # UI buttons
handlers/study.py      # Learn / Test / topics / mistakes / voice / upload
rag/                   # Chunk → embed → Chroma → retrieve
services/groq_service.py   # LLM client (proxy URL) + offline fallback
services/progress_store.py # Streak, stats, wrong answers
deploy/cloudflare-groq-proxy/  # Cloudflare Worker (LLM relay)
deploy/ + scripts/     # systemd unit + Azure redeploy
```

---

## Deploy to Azure (already set up)

```bash
# From your PC (Git Bash), after pushing to GitHub:
bash scripts/deploy-azure.sh
```

SSH:

```bash
bash scripts/azure-ssh.sh
# or:
ssh -i ~/Downloads/mock-practice-bot_key.pem azureuser@104.208.98.207
```

Service:

```bash
sudo systemctl status mock-practice-bot
sudo journalctl -u mock-practice-bot -f
```

After changing `.env` on the VM: `sudo systemctl restart mock-practice-bot`

---

## Commands

| Command | Action |
|---------|--------|
| `/start` `/study` | Categories → topics → Learn/Test |
| `/stats` | Streak, daily goal, accuracy |
| `/review` | List recent mistakes |
| `/reindex` | Rebuild vector index |
| `/note` `/notes` `/clearnotes` | Notes |
| `/remind` `/reminders` | Reminders |
| `/interview` `/end` | Mock interview |
| `/help` `/ping` | Help / health |

---

## License / notes

Personal study project. Do not redistribute copyrighted PDFs. Prefer NCERT and your own notes.
