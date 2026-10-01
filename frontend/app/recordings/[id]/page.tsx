"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import {
  ApiError, LANGUAGES, RecordingDetail, formatBytes, formatDuration, getAudioUrl, getRecording, retryRecording,
} from "@/lib/api";
import { usePolling } from "@/lib/usePolling";


const STEPS = ["Uploaded", "Checking audio", "Splitting", "Transcribing", "Writing summary", "Done"];

// Errors where retrying the same file can't help (mirrors NOT_RETRYABLE in the backend)
const NEEDS_NEW_FILE = ["DECODE_FAILED", "NO_AUDIO_STREAM", "TOO_LONG", "FILE_TOO_LARGE", "NO_SPEECH"];

/** Which step is active, derived from status + stage. */
function activeStep(r: RecordingDetail): number {
  if (r.status === "completed") return 5;
  if (r.status === "queued") return 0;
  switch (r.stage) {
    case "probing": return 1;
    case "chunking": return 2;
    case "transcribing": return 3;
    case "summarizing": return 4;
    default: return 0;
  }
}

function statusLine(r: RecordingDetail): string {
  if (r.status === "queued") return "Queued — waiting for the worker";
  if (r.status === "completed") return "Done";
  if (r.status === "failed") return "Failed";
  switch (r.stage) {
    case "probing": return "Checking audio";
    case "chunking": return "Splitting at pauses into short parts";
    case "transcribing": {
      const retrying = r.chunks.find((c) => c.status === "pending" && c.last_error);
      const base = `Transcribing ${r.chunks_done} of ${r.chunks_total}`;
      return retrying ? `${base} · part ${retrying.idx + 1}: ${retrying.last_error}` : base;
    }
    case "summarizing": return "Writing summary";
    default: return "Processing";
  }
}

function timecode(sec: number): string {
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

/** Seconds since `from`, ticking every second while `running`. */
function useElapsed(from: string | undefined, until: string | null | undefined, running: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!running) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [running]);
  if (!from) return 0;
  const end = until ? new Date(until).getTime() : now;
  return Math.max(0, Math.round((end - new Date(from).getTime()) / 1000));
}

export default function RecordingPage() {
  const { id } = useParams<{ id: string }>();
  const { data: rec, error, restart } = usePolling(
    () => getRecording(id),
    (r) => r.status === "completed" || r.status === "failed",
  );

  const running = !!rec && rec.status !== "completed" && rec.status !== "failed";
  const elapsed = useElapsed(rec?.created_at, rec?.completed_at, running);

  // short-lived playback URL, fetched once we know the recording exists
  const [audioUrl, setAudioUrl] = useState<string | null>(null);
  const exists = !!rec;
  useEffect(() => {
    if (!exists) return;
    getAudioUrl(id).then((r) => setAudioUrl(r.url)).catch(() => setAudioUrl(null));
  }, [id, exists]);

  const [copied, setCopied] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const [retryError, setRetryError] = useState<string | null>(null);

  async function retry() {
    setRetrying(true);
    setRetryError(null);
    try {
      await retryRecording(id);
      restart(); // polling had stopped on the terminal state
    } catch (err) {
      setRetryError(err instanceof ApiError ? err.message : "Retry failed. Please try again.");
    } finally {
      setRetrying(false);
    }
  }

  const retryButton = (label: string) => (
    <button
      onClick={retry}
      disabled={retrying}
      className="mt-2 rounded-md bg-blue-600 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
    >
      {retrying ? "Retrying…" : label}
    </button>
  );

  // ---------- states before we have data ----------
  if (!rec) {
    const notFound = error instanceof ApiError && error.status === 404;
    return (
      <main className="mx-auto max-w-3xl space-y-4 px-4 py-10">
        <Link href="/" className="text-sm underline">← All recordings</Link>
        {notFound ? (
          <p>This recording doesn&apos;t exist or was deleted.</p>
        ) : error ? (
          <p className="rounded-md bg-amber-50 px-3 py-2 text-amber-800 dark:bg-amber-950/40 dark:text-amber-200">
            Starting the server. Free hosting sleeps when idle; this takes about a minute. Retrying…
          </p>
        ) : (
          <p className="text-zinc-500">Loading…</p>
        )}
      </main>
    );
  }

  const step = activeStep(rec);
  const language = LANGUAGES.find((l) => l.code === rec.language_code)?.label ?? rec.language_code;
  const percent = rec.chunks_total ? Math.round((rec.chunks_done / rec.chunks_total) * 100) : 0;

  return (
    <main className="mx-auto max-w-3xl space-y-6 px-4 py-10">
      <Link href="/" className="text-sm underline">← All recordings</Link>

      <header>
        <h1 className="break-words text-2xl font-semibold">{rec.summary?.title || rec.filename}</h1>
        <p className="text-sm text-zinc-500">
          {rec.filename} · {language} · {formatDuration(rec.duration_sec)} · {formatBytes(rec.size_bytes)}
        </p>
      </header>

      {/* connection lost while we already have data: keep showing it, say what's happening */}
      {error != null && (
        <p className="rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:bg-amber-950/40 dark:text-amber-200">
          Lost contact with the server. Retrying…
        </p>
      )}

      {/* ---------- progress ---------- */}
      {rec.status !== "failed" && (
        <section className="rounded-xl border border-zinc-200 p-5 dark:border-zinc-800" aria-live="polite">
          <ol className="flex flex-wrap gap-x-4 gap-y-1 text-sm">
            {STEPS.map((label, i) => (
              <li key={label} className={i < step ? "text-green-600" : i === step ? "font-semibold" : "text-zinc-400"}>
                {i < step ? "✓ " : i === step && running ? "● " : ""}{label}
              </li>
            ))}
          </ol>
          {running && (
            <>
              <div className="mt-4 h-2 overflow-hidden rounded bg-zinc-200 dark:bg-zinc-800">
                <div
                  className="h-full bg-blue-600 transition-[width] duration-500"
                  style={{ width: `${rec.stage === "summarizing" ? 100 : percent}%` }}
                />
              </div>
              <p className="mt-2 text-sm">{statusLine(rec)} · {timecode(elapsed)} elapsed</p>
            </>
          )}
          {rec.status === "completed" && (
            <p className="mt-2 text-sm text-zinc-500">Finished in {timecode(elapsed)}.</p>
          )}
        </section>
      )}

      {/* ---------- failed: say why, and offer the right next action ---------- */}
      {rec.status === "failed" && (
        <section className="rounded-xl border border-red-300 bg-red-50 p-5 dark:border-red-900 dark:bg-red-950/30">
          <h2 className="font-semibold text-red-800 dark:text-red-200">We couldn&apos;t process this recording</h2>
          <p className="mt-1 text-sm text-red-700 dark:text-red-300">{rec.error_message}</p>
          <p className="mt-1 text-xs text-red-600/70">Error code: {rec.error_code}</p>
          {NEEDS_NEW_FILE.includes(rec.error_code ?? "") ? (
            <Link href="/" className="mt-3 inline-block text-sm underline">Upload a different file</Link>
          ) : (
            retryButton("Try again")
          )}
          {retryError && <p className="mt-2 text-sm text-red-700">{retryError}</p>}
        </section>
      )}

      {/* ---------- some parts failed: transcript is shown, failed parts can be retried alone ---------- */}
      {rec.error_code === "PARTIAL_TRANSCRIPT" && rec.status === "completed" && (
        <div className="rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:bg-amber-950/40 dark:text-amber-200">
          <p>{rec.error_message}</p>
          {retryButton("Retry failed parts")}
          {retryError && <p className="mt-1">{retryError}</p>}
        </div>
      )}

      {audioUrl && <audio controls src={audioUrl} className="w-full" />}

      {/* ---------- summary ---------- */}
      <section className="space-y-2">
        <h2 className="text-lg font-semibold">Summary</h2>
        {rec.summary_status === "done" && rec.summary && (
          <div className="space-y-3 rounded-xl border border-zinc-200 p-5 dark:border-zinc-800">
            <p>{rec.summary.summary}</p>
            {rec.summary.key_points.length > 0 && (
              <div>
                <h3 className="text-sm font-semibold text-zinc-500">Key points</h3>
                <ul className="list-disc space-y-1 pl-5">
                  {rec.summary.key_points.map((p, i) => <li key={i}>{p}</li>)}
                </ul>
              </div>
            )}
            {rec.summary.action_items.length > 0 && (
              <div>
                <h3 className="text-sm font-semibold text-zinc-500">Action items</h3>
                <ul className="list-disc space-y-1 pl-5">
                  {rec.summary.action_items.map((p, i) => <li key={i}>{p}</li>)}
                </ul>
              </div>
            )}
          </div>
        )}
        {rec.summary_status === "failed" && (
          <div className="rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:bg-amber-950/40 dark:text-amber-200">
            <p>The summary service was unavailable. Your transcript is below.</p>
            {retryButton("Retry summary")}
            {retryError && <p className="mt-1">{retryError}</p>}
          </div>
        )}
        {rec.summary_status === "skipped" && (
          <p className="text-sm text-zinc-500">Too little speech to summarize.</p>
        )}
        {(rec.summary_status === "pending" || rec.summary_status === "running") && rec.status !== "failed" && (
          <p className="text-sm text-zinc-500">
            {rec.stage === "summarizing" ? "Writing summary…" : "The summary appears once transcription finishes."}
          </p>
        )}
      </section>

      {/* ---------- transcript, part by part ---------- */}
      <section className="space-y-2">
        <div className="flex items-baseline justify-between">
          <h2 className="text-lg font-semibold">Transcript</h2>
          {rec.transcript && (
            <button
              className="text-sm underline"
              onClick={async () => {
                await navigator.clipboard.writeText(rec.transcript ?? "");
                setCopied(true);
                setTimeout(() => setCopied(false), 1500);
              }}
            >
              {copied ? "Copied" : "Copy transcript"}
            </button>
          )}
        </div>
        {rec.chunks.length === 0 ? (
          <p className="text-sm text-zinc-500">
            {rec.status === "failed" ? "No transcript was produced." : "Parts appear here as they are transcribed."}
          </p>
        ) : (
          <ol className="space-y-3">
            {rec.chunks.map((c) => (
              <li key={c.idx} className="grid grid-cols-[6.5rem_1fr] gap-3">
                <span className="pt-0.5 font-mono text-xs text-zinc-500">
                  {timecode(c.start_sec)}–{timecode(c.end_sec)}
                </span>
                {c.status === "done" ? (
                  <p>{c.text || <span className="text-zinc-400">(no speech)</span>}</p>
                ) : c.status === "failed" ? (
                  <p className="text-sm text-red-600">This part couldn&apos;t be transcribed.</p>
                ) : (
                  <p className="text-sm text-zinc-400">{c.last_error ?? "waiting…"}</p>
                )}
              </li>
            ))}
          </ol>
        )}
      </section>
    </main>
  );
}