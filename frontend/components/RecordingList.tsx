"use client";

import Link from "next/link";
import { LANGUAGES, RecordingSummary, formatDuration, listRecordings } from "@/lib/api";
import { usePolling } from "@/lib/usePolling";

const ACTIVE = new Set(["queued", "processing"]);

function StatusPill({ r }: { r: RecordingSummary }) {
  const base = "rounded-full px-2 py-0.5 text-xs font-medium";
  if (r.status === "completed") {
    return r.error_code === "PARTIAL_TRANSCRIPT"
      ? <span className={`${base} bg-amber-100 text-amber-800`}>Partial</span>
      : <span className={`${base} bg-green-100 text-green-800`}>Done</span>;
  }
  if (r.status === "failed") return <span className={`${base} bg-red-100 text-red-800`}>Failed</span>;
  if (r.status === "queued") return <span className={`${base} bg-zinc-200 text-zinc-700`}>Queued</span>;
  const label = r.stage === "transcribing" && r.chunks_total
    ? `Transcribing ${r.chunks_done}/${r.chunks_total}`
    : r.stage === "summarizing" ? "Summarizing" : "Processing";
  return <span className={`${base} bg-blue-100 text-blue-800`}>{label}</span>;
}

export default function RecordingList() {
  // keep refreshing only while something is still in progress
  const { data, error } = usePolling(listRecordings, (list) => !list.some((r) => ACTIVE.has(r.status)));

  return (
    <section className="space-y-3">
      <h2 className="text-lg font-semibold">Past uploads</h2>

      {error != null && (
        <p className="rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:bg-amber-950/40 dark:text-amber-200">
          {data
            ? "Lost contact with the server. Retrying…"
            : "Starting the server. Free hosting sleeps when idle; this takes about a minute. Retrying…"}
        </p>
      )}

      {!data && error == null && <p className="text-sm text-zinc-500">Loading…</p>}

      {data && data.length === 0 && (
        <p className="rounded-xl border border-dashed border-zinc-300 px-4 py-8 text-center text-sm text-zinc-500 dark:border-zinc-700">
          No recordings yet. Upload one above and it will appear here.
        </p>
      )}

      {data && data.length > 0 && (
        <ul className="divide-y divide-zinc-200 rounded-xl border border-zinc-200 dark:divide-zinc-800 dark:border-zinc-800">
          {data.map((r) => (
            <li key={r.id}>
              <Link
                href={`/recordings/${r.id}`}
                className="flex items-center justify-between gap-3 px-4 py-3 hover:bg-zinc-50 dark:hover:bg-zinc-900"
              >
                <div className="min-w-0">
                  <p className="truncate font-medium">{r.filename}</p>
                  <p className="text-xs text-zinc-500">
                    {LANGUAGES.find((l) => l.code === r.language_code)?.label ?? r.language_code}
                    {" · "}{formatDuration(r.duration_sec)}
                    {" · "}{new Date(r.created_at).toLocaleString()}
                  </p>
                </div>
                <StatusPill r={r} />
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}