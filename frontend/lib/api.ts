// All communication with the backend. Components never call fetch() directly.

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export const MAX_UPLOAD_MB = 500; // mirrors the backend's demo cap (the backend still enforces it)

export const LANGUAGES: { code: string; label: string }[] = [
  { code: "en-IN", label: "English" },
  { code: "hi-IN", label: "Hindi" },
  { code: "bn-IN", label: "Bengali" },
  { code: "gu-IN", label: "Gujarati" },
  { code: "kn-IN", label: "Kannada" },
  { code: "ml-IN", label: "Malayalam" },
  { code: "mr-IN", label: "Marathi" },
  { code: "pa-IN", label: "Punjabi" },
  { code: "ta-IN", label: "Tamil" },
  { code: "te-IN", label: "Telugu" },
];

// ---------- types (mirror backend/app/schemas.py) ----------
export type Chunk = {
  idx: number;
  start_sec: number;
  end_sec: number;
  status: "pending" | "done" | "failed";
  text: string | null;
  attempts: number;
  last_error: string | null; // e.g. "retrying (try 2) after ASR_RATE_LIMITED"
};

export type Summary = {
  title: string;
  summary: string;
  key_points: string[];
  action_items: string[];
};

export type RecordingSummary = {
  id: string;
  filename: string;
  language_code: string;
  status: "awaiting_upload" | "queued" | "processing" | "completed" | "failed";
  stage: "probing" | "chunking" | "transcribing" | "summarizing" | null;
  duration_sec: number | null;
  chunks_done: number;
  chunks_total: number;
  error_code: string | null;
  created_at: string;
};

export type RecordingDetail = RecordingSummary & {
  size_bytes: number;
  error_message: string | null;
  transcript: string | null;
  summary: Summary | null;
  summary_status: "pending" | "running" | "done" | "failed" | "skipped";
  completed_at: string | null;
  chunks: Chunk[];
};

// ---------- errors ----------
/** Every failure becomes an ApiError with a machine code and a message we can show. */
export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...init?.headers },
      cache: "no-store",
    });
  } catch {
    // fetch only throws when no HTTP response arrived: server asleep, offline, or CORS
    throw new ApiError(0, "NETWORK", "Can't reach the server. It may be waking up — this usually takes under a minute.");
  }
  if (!res.ok) {
    let code = "HTTP_ERROR";
    let message = `Request failed (${res.status}).`;
    try {
      const body = await res.json();
      // our API: {"detail": {"code", "message"}}; FastAPI validation: {"detail": [...]}
      if (body?.detail?.code) {
        code = body.detail.code;
        message = body.detail.message;
      }
    } catch {
      /* non-JSON error body: keep the generic message */
    }
    throw new ApiError(res.status, code, message);
  }
  return res.json() as Promise<T>;
}

// ---------- endpoints ----------
export function createRecording(input: {
  filename: string;
  size_bytes: number;
  content_type: string;
  language_code: string;
}) {
  return request<{ id: string; upload_url: string; upload_headers: Record<string, string>; expires_in: number }>(
    "/api/recordings",
    { method: "POST", body: JSON.stringify(input) },
  );
}

export const completeUpload = (id: string) =>
  request<RecordingSummary>(`/api/recordings/${id}/complete`, { method: "POST" });

export const listRecordings = () => request<RecordingSummary[]>("/api/recordings");

export const getRecording = (id: string) => request<RecordingDetail>(`/api/recordings/${id}`);

export const getAudioUrl = (id: string) => request<{ url: string }>(`/api/recordings/${id}/audio`);

export const retryRecording = (id: string) =>
  request<RecordingSummary>(`/api/recordings/${id}/retry`, { method: "POST" });

// ---------- direct-to-bucket upload ----------
/**
 * PUT the file straight to the bucket using the presigned URL.
 * XMLHttpRequest (not fetch) because only XHR reports upload progress.
 * Returns the promise plus an abort() for the Cancel button.
 */
export function uploadToBucket(
  url: string,
  headers: Record<string, string>,
  file: File,
  onProgress: (loaded: number, total: number) => void,
): { promise: Promise<void>; abort: () => void } {
  const xhr = new XMLHttpRequest();
  const promise = new Promise<void>((resolve, reject) => {
    xhr.open("PUT", url);
    for (const [name, value] of Object.entries(headers)) xhr.setRequestHeader(name, value);
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress(e.loaded, e.total);
    };
    xhr.onload = () =>
      xhr.status >= 200 && xhr.status < 300
        ? resolve()
        : reject(new ApiError(xhr.status, "UPLOAD_FAILED", `Storage rejected the upload (${xhr.status}).`));
    xhr.onerror = () => reject(new ApiError(0, "UPLOAD_FAILED", "The upload was interrupted. Check your connection and try again."));
    xhr.onabort = () => reject(new ApiError(0, "UPLOAD_CANCELLED", "Upload cancelled."));
    xhr.send(file);
  });
  return { promise, abort: () => xhr.abort() };
}

// ---------- small helpers ----------
const EXT_TYPES: Record<string, string> = {
  mp3: "audio/mpeg", m4a: "audio/mp4", wav: "audio/wav", ogg: "audio/ogg", flac: "audio/flac",
  aac: "audio/aac", opus: "audio/ogg", webm: "audio/webm", mp4: "video/mp4",
};

/** Browsers sometimes give an empty file.type; fall back to the extension. */
export function contentTypeOf(file: File): string {
  if (file.type) return file.type;
  const ext = file.name.split(".").pop()?.toLowerCase() ?? "";
  return EXT_TYPES[ext] ?? "";
}

export function formatBytes(n: number): string {
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

export function formatDuration(sec: number | null): string {
  if (sec == null) return "—";
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}