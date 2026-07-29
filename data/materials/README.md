# Study materials

Drop real PDFs / notes here by category, then run `/reindex` in Telegram (or restart the bot if the index was empty).

| Folder | Category |
|--------|----------|
| `placement/` | Placement (BTech CSE) |
| `jee/` | JEE |
| `neet/` | NEET |
| `ncert_11/` | Class 11 (NCERT) |
| `ncert_12/` | Class 12 (NCERT) |
| `ssc_cgl/` | SSC CGL |

**Supported:** `.pdf`, `.txt`, `.md`

Seed `.txt` files are demos so Learn/Test works out of the box. Replace or add official NCERT / coaching PDFs for real prep.

Student uploads from Telegram go to `data/uploads/{user_id}/{category}/` and are merged into that category’s index.

## Migration note

Older folders `jee_neet/` and `ncert/` were split into `jee` + `neet` and `ncert_11` + `ncert_12`. Do not put new files in the old folders — they are unused by the bot.
