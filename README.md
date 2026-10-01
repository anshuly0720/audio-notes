# Audio Notes

Upload an audio file, get back a transcript and a summary.

- **Live app:** https://audio-notes-two.vercel.app
- **How it works (architecture):** https://audio-notes-two.vercel.app/architecture
- **API docs:** https://audio-notes-44g4.onrender.com/docs

The backend runs on a free tier that sleeps when idle, so the first request can take about a minute.

## What it does

- Upload audio of any length (the demo caps files at 500 MB and 60 minutes).
- Transcribes it with Gnani's speech-to-text API, in 10 Indian languages.
- Summarizes it with Gemini: title, summary, key points, action items.
- Shows live progress: upload %, current stage, "part n of N", and the transcript as it arrives.
- Lists past uploads; each can be reopened.
- Reports failures clearly and lets you retry only what failed.

## Stack

| Layer | Choice |
|---|---|
| Frontend | Next.js (App Router, TypeScript, Tailwind) on Vercel |
| Backend | FastAPI in Docker (with ffmpeg) on Render |
| Database | Postgres on Neon (SQLAlchemy async + Alembic) |
| Storage | Backblaze B2 (S3-compatible), private bucket, presigned URLs |
| Background jobs | A `jobs` table in Postgres, claimed with `FOR UPDATE SKIP LOCKED` |
| Speech-to-text | Gnani REST API `/stt/v3` |
| Summary | Gemini, with a fallback model |

## How it works, in short

1. The browser asks the API for a presigned URL and uploads the file **directly to the bucket**.
2. The API verifies the object exists and adds a job to the queue.
3. A worker downloads the file, checks it with ffprobe, converts it to 16 kHz mono,
   and cuts it **at pauses** into parts of at most 27 s (Gnani's REST limit is 30 s).
4. Each part is sent to Gnani one at a time (the API allows about one request per second)
   and saved as soon as it returns. A restart resumes from the first unfinished part.
5. The parts are joined into the transcript and a second job asks Gemini for the summary.
6. The page polls the API every 2 s and renders progress from the database.

The full explanation, measurements and trade-offs are on the
[architecture page](https://audio-notes-two.vercel.app/architecture).
The experiments behind the design are in [`docs/SPIKE.md`](docs/SPIKE.md) and the
decision record is in [`docs/DECISIONS.md`](docs/DECISIONS.md).

## Project layout

```
backend/
  app/
    main.py          FastAPI app, CORS, starts the embedded worker
    config.py        settings from environment variables
    db.py, models.py SQLAlchemy engine and tables (recordings, chunks, jobs)
    schemas.py       request/response models
    storage.py       presigned PUT/GET, HEAD, download (boto3)
    audio.py         ffprobe, ffmpeg, pause detection, chunk planner
    asr.py           paced Gnani client with retry and error classification
    summarizer.py    Gemini summary with model fallback
    api/recordings.py  HTTP endpoints
    worker/          queue.py (claim, heartbeat, reaper), pipeline.py, runner.py
  alembic/           database migrations
  tests/             unit tests
  scripts/           smoke tests used during development
  Dockerfile
frontend/
  app/page.tsx                  upload form + history
  app/recordings/[id]/page.tsx  live progress, transcript, summary
  app/architecture/page.tsx     how the system works
  components/, lib/             UI pieces, API client, polling hook
docs/                SPIKE.md (measurements), DECISIONS.md
spike/               the scripts used to measure Gnani's API
```

## Run it locally

Requirements: Python 3.12+, Node 20+, and **ffmpeg** on your PATH.
You also need a Postgres database (Neon free tier works), an S3-compatible bucket,
a Gnani API key and a Gemini API key.

```bash
# 1. configure
cp .env.example .env          # then fill in the values

# 2. backend
python -m venv .venv
.venv\Scripts\activate        # Windows  (macOS/Linux: source .venv/bin/activate)
pip install -r backend/requirements.txt
cd backend
alembic upgrade head          # create the tables
python scripts/set_bucket_cors.py   # allow browser uploads (edit ORIGINS first)
uvicorn app.main:app --reload       # http://localhost:8000  (worker starts inside it)

# 3. frontend (new terminal)
cd frontend
npm install
echo NEXT_PUBLIC_API_URL=http://localhost:8000 > .env.local
npm run dev                   # http://localhost:3000
```

### Environment variables

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://…?ssl=require` |
| `S3_ENDPOINT`, `S3_KEY_ID`, `S3_SECRET`, `S3_BUCKET` | Storage bucket |
| `GNANI_API_KEY` | Speech-to-text |
| `GEMINI_API_KEY`, `GEMINI_MODELS` | Summary; models are tried in order |
| `CORS_ORIGINS` | Comma-separated frontend origins |
| `EMBED_WORKER` | `true` runs the worker inside the API process |
| `ASR_MIN_GAP_SECONDS` | Minimum gap between Gnani requests (default 1.1) |
| `MAX_UPLOAD_MB`, `MAX_AUDIO_MINUTES`, `UPLOAD_LIMIT_PER_HOUR` | Demo caps |
| `NEXT_PUBLIC_API_URL` | Frontend: where the API lives |

### Tests

```bash
cd backend
python -m pytest -q
```

To run the worker as a separate process instead of inside the API:
set `EMBED_WORKER=false` and run `python -m app.worker` from `backend/`.

## Known limits

- No accounts: everyone sees the same list of recordings.
- English transcripts come back without punctuation (Gnani's output is shown as-is).
- Hindi mode can drop parts of Hindi-English speech; short parts limit the loss.
- The free backend sleeps after 15 minutes without traffic.
- Summaries use Gemini's free tier.