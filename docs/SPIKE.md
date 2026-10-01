# Day 0 spike: Gnani ASR behaviour

## Playground (app.gnani.ai/voice/speech-to-text)
Model: Gnani Prisma V2.5 · 10 Indian languages · modes: Speak Live, Record/Upload

| Clip | Language | Time taken | Result |
|------|----------|-----------|--------|
| 25 s English | en-IN | ~?s | All words correct. Lowercase, no punctuation. Numbers/dates normalized |
| 90 s English | en-IN | — | Rejected: "For playground experience upload audio less than 30 seconds" |
| 20 s Hindi/Hinglish | hi-IN | ~?s | Only 1st sentence returned ("नमस्ते, मेरा नाम अंशुल है।"); rest missing |

- Punctuation / casing: en-IN has none (all lowercase, no full stops). hi-IN includes `,` and `।`.
- Script for Hindi: Devanagari. English words inside Hindi: not visible, since output was cut after sentence 1.
- Numbers: normalized in playground ("1st October 2026", "₹2,500"), which looks like ITN is on.
  Over-normalizes: "first, build..." → "1st build". API test of verbatim vs transcribe pending.
- Limits shown in UI: 30 s max per upload/recording. API docs say 60 s max, ≤30 s ideal.



## REST API (checkbox 3)
Endpoint: POST https://api.vachana.ai/stt/v3


### Single requests
- 25 s en-IN: 200 OK, ~1.1–1.3 s round trip, processing_time ~0.4–0.55 s
- format=transcribe returns BOTH `transcript` (ITN: ₹2,500, 1st October 2026)
  and `output.literal` (verbatim). Decision: use transcribe, store both.
- 55 s and 90 s: 400 MAX_AUDIO_DURATION_EXCEEDED —
  "maximum allowed duration of 30 seconds". Docs say 60 s → docs are outdated.
  Decision: parts ≤ 30 s (target ~25–28 s, cut at pauses).
- hi-IN 20 s clip: only first sentence returned (same as playground). Investigating.

### hi-IN segment dropping
- 17.1 s Hinglish clip (5 sentences). silencedetect shows pauses of 0.57–0.81 s.
- hi-IN full clip → only sentence 1. hi-IN from 6.8 s → sentences 3–5 (sentence 2 dropped again).
- en-IN same clip → all 5 sentences, romanized ("audi notes" for "audio notes").
- Conclusion: NOT a pause cut-off. hi-IN silently drops segments of code-mixed speech.
- Mitigation: short pause-aligned parts (limits loss), and a words-per-second
  coverage check that flags suspicious parts in the UI (stretch).
- To compare in Batch spike: send this same clip with hi-IN.

### Rate limit
- Parallel 5 → 1/5 ok, parallel 2 → 1/2, parallel 3 → 1/3 (concurrency = 1).
- Sequential, no gap → 3/5 ok (429 when starts are <~0.7 s apart).
- Sequential, 0.5 s gap → 5/5 ok (starts ~1.05 s apart). 1 s gap → 5/5 ok.
- 429 body: {"detail": {"error_code": "RATE_LIMITED", "message": "Rate limit exceeded"}}.
  No Retry-After / rate-limit headers → client must choose its own backoff.
- Decision: one request at a time, ≥1.1 s between request starts, retry 429 with
  backoff (1 s, 2 s, 4 s + jitter). 1 h audio ≈ 144 parts ≈ 2.5–3 min.


## Batch API (checkbox 4)
## Batch API (checkbox 4)
- Rate limit (~1 req/s) applies to Batch endpoints too: create→start back-to-back
  gave 429. Client needs pacing (≥1.1 s) + 429 retry everywhere.
- Job status includes progress.percent (not in docs).
- 20 s Hinglish, hi-IN: COMPLETED by first poll (≤10.6 s). ALL 5 sentences returned,
  with punctuation + ITN (₹2,500, 3 अक्टूबर). Minor artifacts (extra spaces, "फि िनिश").
  speaker_id = 2 on one segment despite no diarization → speaker labels unreliable.
- 3 min en-IN (diarize on): COMPLETED in 11–21 s. Segments have start/end timestamps.
  DROPPED SPEECH: ~1:25–1:35 ("the second is speed … two hundred milliseconds")
  confirmed spoken at ~1:28 but missing. Also "process the parts, and stitch the
  text back" missing.
- Conclusion: Batch is fast and better on Hinglish, but also loses segments.
  Head-to-head vs chunked REST on the same clip decides.

## Credits (checkbox 5)
- Before: ₹999.04 · After: ₹992.96 → spent ₹6.08
- Audio actually transcribed ≈ 14 min (429/400 responses assumed unbilled)
- Cost ≈ ₹0.43 per audio minute ≈ ₹26 per hour (REST + Batch combined)
- Remaining ≈ 35–38 h of audio
- Demo caps: 60 min per file (≈ ₹26 worst case), 500 MB, 10 uploads/IP/hour

### Findings
1. The 30 s playground cap plus the docs' "ideal ≤30 s" support splitting audio into ≤30 s parts.
2. English output has no punctuation, so the raw transcript is hard to read. Show it as-is;
   the LLM summary gives readability. An LLM "punctuate" pass is a stretch goal.
3. Hindi returned only the first sentence. Possible truncation at a pause. MUST retest via the
   API (checkbox 3). If it truncates there too, parts must be cut at pauses and kept short.
4. Language must be chosen by the user (no auto-detect on REST), so the upload form needs a language picker.

## Head-to-head: same 3 min clip (script ≈ 420 words)
| | Batch | Chunked REST (fixed 25 s cuts) |
|---|---|---|
| End-to-end | 11–21 s | 6.3 s (6 parts, paced 1.1 s) |
| Words returned | 341 | 333 |
| Lost | "the second is speed…" (~1:28), "process the parts…" | text at cut points (~25/50/100/125 s), "the third is handling long audio…" |
- Chunked losses cluster at the fixed cut boundaries → caused by cutting mid-word.
- Fix: cut only inside pauses (silencedetect) → planner tested on this clip on Day 1.
  Success = more words than Batch (341), no drops at boundaries.