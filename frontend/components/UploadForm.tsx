"use client";

import { useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  ApiError, LANGUAGES, MAX_UPLOAD_MB, completeUpload, contentTypeOf, createRecording, formatBytes, uploadToBucket,
} from "@/lib/api";

// The upload is a small state machine; the UI renders from `phase`.
type Phase =
  | { name: "idle" }
  | { name: "preparing" }                                 // asking the API for an upload URL
  | { name: "uploading"; loaded: number; total: number }  // bytes going to the bucket
  | { name: "finishing" }                                 // telling the API the upload is done
  | { name: "error"; message: string };

export default function UploadForm() {
  const router = useRouter();
  const [file, setFile] = useState<File | null>(null);
  const [language, setLanguage] = useState("en-IN");
  const [phase, setPhase] = useState<Phase>({ name: "idle" });
  const [dragging, setDragging] = useState(false);
  const abortRef = useRef<(() => void) | null>(null);

  const busy = phase.name === "preparing" || phase.name === "uploading" || phase.name === "finishing";

  /** Cheap checks in the browser, so the user hears about problems before uploading anything. */
  function validate(f: File): string | null {
    const type = contentTypeOf(f);
    if (!(type.startsWith("audio/") || type === "video/mp4" || type === "video/webm")) {
      return "That doesn't look like an audio file. Try MP3, M4A, WAV, OGG, FLAC or WebM.";
    }
    if (f.size > MAX_UPLOAD_MB * 1024 * 1024) {
      return `Files can be up to ${MAX_UPLOAD_MB} MB in this demo. This one is ${formatBytes(f.size)}.`;
    }
    if (f.size === 0) return "This file is empty.";
    return null;
  }

  function choose(f: File | undefined) {
    if (!f) return;
    const problem = validate(f);
    setFile(problem ? null : f);
    setPhase(problem ? { name: "error", message: problem } : { name: "idle" });
  }

  async function start() {
    if (!file) return;
    try {
      // 1. ask our API for a slot + presigned URL
      setPhase({ name: "preparing" });
      const slot = await createRecording({
        filename: file.name,
        size_bytes: file.size,
        content_type: contentTypeOf(file),
        language_code: language,
      });

      // 2. send the bytes straight to the bucket, with real progress
      setPhase({ name: "uploading", loaded: 0, total: file.size });
      const upload = uploadToBucket(slot.upload_url, slot.upload_headers, file, (loaded, total) =>
        setPhase({ name: "uploading", loaded, total }),
      );
      abortRef.current = upload.abort;
      await upload.promise;

      // 3. tell the API: it verifies the file and queues the job
      setPhase({ name: "finishing" });
      await completeUpload(slot.id);
      router.push(`/recordings/${slot.id}`);
    } catch (err) {
      const message = err instanceof ApiError ? err.message : "Something went wrong. Please try again.";
      setPhase({ name: "error", message });
    } finally {
      abortRef.current = null;
    }
  }

  const percent = phase.name === "uploading" ? Math.round((phase.loaded / phase.total) * 100) : 0;

  return (
    <section className="rounded-xl border border-zinc-200 bg-white p-6 dark:border-zinc-800 dark:bg-zinc-900">
      <label
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => { e.preventDefault(); setDragging(false); if (!busy) choose(e.dataTransfer.files[0]); }}
        className={`flex cursor-pointer flex-col items-center gap-2 rounded-lg border-2 border-dashed px-6 py-10 text-center transition
          ${dragging ? "border-blue-500 bg-blue-50 dark:bg-blue-950/30" : "border-zinc-300 dark:border-zinc-700"}
          ${busy ? "pointer-events-none opacity-60" : "hover:border-blue-400"}`}
      >
        <input
          type="file"
          accept="audio/*,video/mp4,video/webm"
          className="hidden"
          disabled={busy}
          onChange={(e) => choose(e.target.files?.[0])}
        />
        {file ? (
          <>
            <span className="font-medium">{file.name}</span>
            <span className="text-sm text-zinc-500">{formatBytes(file.size)} · click to choose a different file</span>
          </>
        ) : (
          <>
            <span className="font-medium">Drop an audio file here, or click to choose</span>
            <span className="text-sm text-zinc-500">MP3, M4A, WAV, OGG, FLAC, WebM · up to {MAX_UPLOAD_MB} MB</span>
          </>
        )}
      </label>

      <div className="mt-4 flex flex-wrap items-end gap-4">
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-zinc-600 dark:text-zinc-400">Spoken language</span>
          <select
            value={language}
            onChange={(e) => setLanguage(e.target.value)}
            disabled={busy}
            className="rounded-md border border-zinc-300 bg-white px-3 py-2 dark:border-zinc-700 dark:bg-zinc-800"
          >
            {LANGUAGES.map((l) => (
              <option key={l.code} value={l.code}>{l.label}</option>
            ))}
          </select>
        </label>

        {busy ? (
          <button
            onClick={() => abortRef.current?.()}
            disabled={phase.name !== "uploading"}
            className="rounded-md border border-zinc-300 px-4 py-2 text-sm disabled:opacity-50 dark:border-zinc-700"
          >
            Cancel upload
          </button>
        ) : (
          <button
            onClick={start}
            disabled={!file}
            className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-40"
          >
            Upload and transcribe
          </button>
        )}
      </div>

      {/* progress + status: the page never looks frozen */}
      <div className="mt-4 min-h-12 text-sm" aria-live="polite">
        {phase.name === "preparing" && <p>Preparing upload…</p>}
        {phase.name === "uploading" && (
          <div className="space-y-1">
            <div className="h-2 overflow-hidden rounded bg-zinc-200 dark:bg-zinc-800">
              <div className="h-full bg-blue-600 transition-[width]" style={{ width: `${percent}%` }} />
            </div>
            <p>Uploading {percent}% · {formatBytes(phase.loaded)} of {formatBytes(phase.total)}</p>
          </div>
        )}
        {phase.name === "finishing" && <p>Upload complete. Queuing transcription…</p>}
        {phase.name === "error" && (
          <p className="rounded-md bg-red-50 px-3 py-2 text-red-700 dark:bg-red-950/40 dark:text-red-300">
            {phase.message}
          </p>
        )}
      </div>
    </section>
  );
}