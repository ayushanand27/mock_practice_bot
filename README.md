# RAG Study Telegram Bot

Study from your own materials with **RAG** (retrieve → Groq answer), then take **MCQ / MSQ / Numerical / Theory** tests.

Bot: [@mock_practice_bot](https://t.me/mock_practice_bot)

## Categories

1. Placement (BTech CSE)
2. JEE
3. NEET
4. Class 11 (NCERT)
5. Class 12 (NCERT)
6. SSC CGL

Each category supports **Learn** (concept Q&A from RAG materials) and **Test**.

## Quick start

```bash
cp .env.example .env
# Set BOT_TOKEN, GROQ_API_KEY; optionally SARVAM_API_KEY

python -m venv .venv
source .venv/Scripts/activate   # Windows Git Bash
# Windows CMD: .venv\Scripts\activate
pip install -r requirements.txt
python bot.py
```

On startup the bot indexes `data/materials/{category}/` into a local Chroma store (`data/chroma/`) using `sentence-transformers` embeddings.

## Exact user flows

### Learn (concept Q&A)

1. Open Telegram → `/start`
2. Tap a category (e.g. Placement)
3. Tap **Learn**
4. Ask in plain language, e.g. `What is ACID in DBMS?` or `Explain projectile range`
5. Bot retrieves top chunks from that category’s materials and answers with Groq
6. Use inline **Switch to Test** or **Change category** anytime

### Test (MCQ / MSQ / Numerical / Theory)

1. `/start` or `/study` → pick category → **Test**
2. Choose question type
3. Bot generates a question from retrieved materials
4. Reply with your answer:
   - MCQ: one letter `A`–`D`
   - MSQ: letters e.g. `A C` or `AC`
   - Numerical: a number
   - Theory: a short written answer (LLM-graded 0–10; ≥7 counts as correct)
5. See feedback + running score; tap **Next question**, **Change type**, or **End test**

### Upload materials

1. Tap **Upload** (or send a file after picking a category)
2. Choose category
3. Send a `.pdf` / `.txt` / `.md`
4. Bot saves under `data/uploads/{your_id}/{category}/` and reindexes that category
5. Continue with Learn or Test

### Reindex everything

- `/reindex` — rebuilds the vector index for all categories (seed materials + all uploads)

### Secondary features

| Command / button | Action |
|------------------|--------|
| `/note` `/notes` `/clearnotes` | Personal notes |
| `/remind` `/reminders` | Timed reminders |
| `/interview` `/end` | Optional mock interview (Groq; Sarvam TTS/STT if keyed) |
| `/help` `/ping` | Help / health |

## How to add real PDFs

1. Drop files under the matching folder:
   - `data/materials/placement/`
   - `data/materials/jee/`
   - `data/materials/neet/`
   - `data/materials/ncert_11/` — e.g. Class 11 NCERT chapter PDFs from [ncert.nic.in](https://ncert.nic.in)
   - `data/materials/ncert_12/` — e.g. Class 12 NCERT chapter PDFs
   - `data/materials/ssc_cgl/`
2. Supported: `.pdf`, `.txt`, `.md`
3. In Telegram run `/reindex` (or restart if the index was empty)
4. Prefer many focused chapter PDFs over one giant scan if extraction quality matters

Seed `.txt` notes ship per category so demo Learn/Test works before you add real books. See `data/materials/README.md`.

## Architecture

```
bot.py                 # Wiring, /start study-first, post_init index
config.py              # Categories, paths, chunk settings
keyboards.py           # Category / Learn-Test / Test-type keyboards
handlers/
  common.py            # start, help
  study.py             # Learn RAG, Test, upload, reindex
  notes.py / reminders.py / interview.py   # secondary
rag/
  chunking.py          # PDF/txt/md → chunks
  store.py             # sentence-transformers + Chroma
  pipeline.py          # reindex, retrieve, answer
services/
  groq_service.py      # Answers + test generate/grade
  study_state.py       # Per-user Learn/Test session
  sarvam_service.py    # Optional TTS/STT (interview voice)
data/materials/…       # Seed + your PDFs
data/uploads/{user}/…  # Student uploads
data/chroma/           # Vector DB (local)
```

**Flow:** category → Learn (free-text → retrieve top chunks → Groq) or Test (generate question from context → grade).

## Env

| Var | Role |
|-----|------|
| `BOT_TOKEN` | Telegram |
| `GROQ_API_KEY` | Answers + tests (`llama-3.3-70b-versatile`) |
| `SARVAM_API_KEY` | Optional voice for interviews |
| `GROQ_MODEL` | Optional override |

Never commit `.env`.
