# Decisions

Each entry: context, options considered, choice, trade-off.

## 1. How to handle audio longer than the ASR limit

**Context (measured 1 Oct 2026, see SPIKE.md)**
- Gnani REST `/stt/v3` rejects audio > 30 s (`MAX_AUDIO_DURATION_EXCEEDED`), although the docs say 60 s.
- REST rate limit ≈ 1 request/second, concurrency 1 (no Retry-After header).
- REST latency ≈ 0.5–1 s per 25 s part.
- Batch API handles long files in one job; 3 min clip done in 11–21 s.
- On the same 3 min clip both approaches dropped some speech (Batch 341 words, chunked 333 of ~420).
  Chunked losses sat at the fixed 25 s cut points; Batch losses were inside its own segments.
- Batch returned a Hinglish (hi-IN) clip completely; REST dropped segments of it.

**Options**
A. Split audio into ≤30 s parts and call REST sequentially, then stitch.
B. Send the whole file to the Batch API and poll.
C. Hybrid: REST for short files, Batch for long.

**Choice: A**, with parts cut inside pauses (not at fixed times).

**Why**
- The main weakness of A (losses at cut points) is caused by our cutting and fixable in our code.
  Batch's losses are outside our control.
- Real progress (part n of N) and partial transcript while processing.
- A failed part is retried alone; other parts are kept.
- Faster in our test (6.3 s vs 11–21 s for 3 min).
- All 10 REST languages vs 8 on Batch.

**Trade-offs accepted**
- Sequential, paced requests (≥1.1 s apart): 1 h audio ≈ 3 min.
- No word timestamps from REST; we keep part-level timecodes.
- hi-IN on code-mixed speech is weaker on REST than Batch.

**Revisit if**: pause-aligned cuts don't beat Batch's word count on the same clip,
or hi-IN quality matters more than progress UX → add a Batch lane.