# RAG Study Telegram Bot

Study from your own materials with **RAG**, then take **MCQ / MSQ / Numerical / Theory** tests — with topics, streaks, mistake practice, session reports, and optional voice.

**Live bot:** [@mock_practice_bot](https://t.me/mock_practice_bot)  
**Repo:** https://github.com/ayushanand27/mock_practice_bot  
**Hosted on:** Azure VM (Always Free / Students) — systemd service `mock-practice-bot`

---

## Categories (7)

1. Placement (BTech CSE)  
2. JEE  
3. NEET  
4. Class 11 (NCERT)  
5. Class 12 (NCERT)  
6. SSC CGL  
7. UPSC  

Each category → **topic/chapter** → **Learn** / **Test** / **Practice mistakes**.

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
- You pasted API keys in chat before — **rotate** Telegram bot token (BotFather `/revoke`), Groq, Sarvam, Gemini when you can  
- Put new keys only in `.env` on the VM (or ask the agent to update via SSH)

### 3. Fix Groq / Gemini on Azure (optional but recommended)
See section **[Groq & Gemini not working on Azure](#groq--gemini-not-working-on-azure)** below.  
Until fixed, the bot still works using **offline RAG** (answers/questions from your notes).

### 4. Day-to-day ops
- Bot auto-starts on the VM (`systemd`)  
- After code changes on GitHub: `bash scripts/deploy-azure.sh`  
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

---

## Env vars

| Var | Role |
|-----|------|
| `BOT_TOKEN` | Telegram (BotFather) |
| `GROQ_API_KEY` | Fast LLM (works on laptop; often blocked on cloud IPs) |
| `GEMINI_API_KEY` | Google AI Studio key (often blocked from datacenter IPs) |
| `SARVAM_API_KEY` | Voice TTS/STT (+ optional chat if enabled) |
| `GROQ_MODEL` | Optional, default `llama-3.3-70b-versatile` |
| `GEMINI_MODEL` | Optional, default `gemini-2.0-flash` |
| `LLM_PROXY_URL` | Optional OpenAI-compatible proxy base URL (see below) |

Never commit `.env`.

---

## Groq & Gemini not working on Azure

### What’s wrong?

Your keys work on your **laptop**, but the **Azure VM public IP** (`East Asia` datacenter) is treated as a cloud/server IP:

| Provider | Error from Azure | Meaning |
|----------|------------------|---------|
| **Groq** | `403 Forbidden` | Free / abuse protection — blocks many VPS/cloud IPs |
| **Gemini** | `User location is not supported` / quota | Datacenter / region IP not allowed for API use |

This is a **provider policy**, not a bug in our bot code.

### What the bot does today

1. Try Groq → Gemini  
2. If both fail → **offline RAG mode** (retrieve your notes and build answers/questions locally)  

So Learn/Test still work after you upload PDFs — just less “smart” than a live LLM.

### How to resolve (pick one)

#### Option A — Cloudflare Worker proxy for Groq (best free fix)
Route Groq calls through Cloudflare’s edge so the request does not come from the Azure IP.

1. Create a free [Cloudflare Workers](https://workers.cloudflare.com) account  
2. Deploy a tiny Groq proxy (example projects: search `groq-cf-proxy`)  
3. Set on the Azure VM `.env`:
   ```env
   LLM_PROXY_URL=https://YOUR-WORKER.workers.dev/openai/v1
   GROQ_API_KEY=your_groq_key
   ```
4. Redeploy / restart: `sudo systemctl restart mock-practice-bot`  

*(Proxy support can be wired in code if not already — ask the agent to add `base_url` from `LLM_PROXY_URL`.)*

#### Option B — Use an LLM that allows cloud IPs
Paid or cloud-friendly APIs often work from Azure, e.g.:

- DeepSeek / OpenRouter / Together / Azure OpenAI  

Add the key and ask to wire that provider.

#### Option C — Recreate VM in another region (may still fail)
Student subscription only allows certain regions (yours: Australia East, East Asia, Korea Central, Southeast Asia, Malaysia West).  
Moving region **might** help Gemini sometimes, but **cloud IPs are often still blocked**. Not guaranteed.

#### Option D — Keep offline RAG (simplest)
Upload strong PDFs and stay on note-based answers until you add a proxy or paid API.  
For an exam bot, **good materials > fancy model**.

### Recommended path for you
1. **Upload PDFs now** (biggest quality jump)  
2. Then set up **Cloudflare Groq proxy** (free) **or** OpenRouter/DeepSeek  
3. Rotate exposed keys  

---

## Architecture

```
bot.py                 # Wiring, commands, post_init index
config.py              # Categories, topics, paths
keyboards.py           # UI buttons
handlers/study.py      # Learn / Test / topics / mistakes / voice / upload
rag/                   # Chunk → embed → Chroma → retrieve
services/groq_service.py   # Multi-provider LLM + offline fallback
services/progress_store.py # Streak, stats, wrong answers
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
