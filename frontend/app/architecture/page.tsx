import Link from "next/link";

export const metadata = { title: "How it works · Audio Notes" };

const GITHUB_URL = "https://github.com/anshuly0720/audio-notes";

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-3">
      <h2 className="text-xl font-semibold">{title}</h2>
      {children}
    </section>
  );
}

function Table({ head, rows }: { head: string[]; rows: string[][] }) {
  return (
    <div className="overflow-x-auto rounded-lg border border-zinc-200 dark:border-zinc-800">
      <table className="w-full text-left text-sm">
        <thead className="bg-zinc-100 dark:bg-zinc-900">
          <tr>{head.map((h) => <th key={h} className="px-3 py-2 font-medium">{h}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="border-t border-zinc-200 align-top dark:border-zinc-800">
              {r.map((c, j) => <td key={j} className="px-3 py-2">{c}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function ArchitecturePage() {
  return (
    <main className="mx-auto max-w-3xl space-y-10 px-4 py-10 leading-relaxed">
      <header className="space-y-2">
        <Link href="/" className="text-sm underline">← Back to the app</Link>
        <h1 className="text-3xl font-semibold">How Audio Notes works</h1>
        <p className="text-zinc-600 dark:text-zinc-400">
          You upload an audio file. The app transcribes it with Gnani&apos;s speech-to-text API, summarizes it
          with an LLM, and keeps it in a list you can reopen.
        </p>
        <p>
          Source code:{" "}
          <a href={GITHUB_URL} className="underline" target="_blank" rel="noopener noreferrer">{GITHUB_URL}</a>
        </p>
      </header>

      <Section title="The pieces">
        <Table
          head={["Piece", "What I used", "Why"]}
          rows={[
            ["Frontend", "Next.js on Vercel", "Required by the brief. Pages are client components that call the API."],
            ["Backend", "FastAPI in Docker on Render", "Docker lets me install ffmpeg, which I need to cut audio."],
            ["Database", "Postgres on Neon", "Stores recordings, parts, results, and the job queue."],
            ["Storage bucket", "Backblaze B2 (S3-compatible)", "Holds the original audio. Private; accessed only with short-lived signed URLs."],
            ["Background jobs", "A jobs table in Postgres + a worker loop", "No extra service to host, and adding a job commits in the same transaction as the data."],
            ["Speech-to-text", "Gnani REST API /stt/v3", "Required by the brief."],
            ["Summary", "Gemini (free tier), with a fallback model", "Free, and good at cleaning up unpunctuated transcripts."],
          ]}
        />
      </Section>

      <Section title="From upload to transcript, step by step">
        <ol className="list-decimal space-y-2 pl-6">
          <li><strong>Create.</strong> The browser sends the file name, size, type and language. The API checks them, saves a row with status <code>awaiting_upload</code>, and returns a signed upload URL that is valid for 15 minutes.</li>
          <li><strong>Upload.</strong> The browser sends the file straight to the bucket using that URL. The file never passes through my server, so there is no server upload limit and the progress bar shows real bytes.</li>
          <li><strong>Complete.</strong> The browser tells the API the upload finished. The API asks the bucket whether the file really exists and how big it is, then marks the recording <code>queued</code> and adds a job. Both happen in one database transaction.</li>
          <li><strong>Claim.</strong> The worker takes the job using <code>FOR UPDATE SKIP LOCKED</code>, which guarantees two workers can never take the same job.</li>
          <li><strong>Prepare.</strong> The worker downloads the file, checks it with ffprobe, converts it to 16 kHz mono, finds the pauses, and plans where to cut. The plan is saved as one row per part.</li>
          <li><strong>Transcribe.</strong> Each part is sent to Gnani, one at a time. Each result is saved the moment it arrives.</li>
          <li><strong>Summarize.</strong> The parts are joined in order into the transcript. A second job sends it to Gemini for a title, summary, key points and action items.</li>
          <li><strong>Watch.</strong> The page asks the API for the recording every 2 seconds and shows the stage, &quot;part n of N&quot;, and the transcript so far.</li>
        </ol>
      </Section>

      <Section title="How I handled long audio">
        <p>
          I tested Gnani&apos;s API before designing anything, and three measurements shaped the whole design:
        </p>
        <ul className="list-disc space-y-1 pl-6">
          <li>The REST API rejects audio longer than <strong>30 seconds</strong> (the docs say 60). The error is <code>MAX_AUDIO_DURATION_EXCEEDED</code>.</li>
          <li>It allows about <strong>one request per second</strong> and no parallel requests. Extra requests get a 429 with no Retry-After header.</li>
          <li>A 25-second part takes about <strong>1 second</strong> to transcribe.</li>
        </ul>
        <p>
          So the worker splits every file into parts of at most 27 seconds and sends them one by one, at least
          1.1 seconds apart. An hour of audio takes roughly 3 minutes.
        </p>
        <p>
          <strong>Where to cut matters.</strong> My first version cut every 25 seconds, and text went missing exactly
          at the cut points because words were split in half. Now ffmpeg&apos;s <code>silencedetect</code> finds the
          pauses, and each cut is placed in the longest pause between 10 and 27 seconds into the part. If there is
          no pause, it cuts at 27 seconds. I also add 0.3 seconds of silence to both ends of every part, because
          Gnani dropped speech that sat right at the edge of a clip.
        </p>
        <Table
          head={["Approach, same 2.5-minute recording", "Words returned (script ≈ 420)", "Time"]}
          rows={[
            ["Fixed 25 s cuts", "333", "6 s"],
            ["Gnani Batch API", "341", "11–21 s"],
            ["Cuts at pauses + padding (what I use)", "356", "about 10 s"],
          ]}
        />
        <p>
          <strong>Why not the Batch API?</strong> It accepts long files in one job and was better on a Hindi-English
          clip. But it only reports &quot;queued, then done&quot;, it also lost sentences in my test, and I
          can&apos;t control where. With my own parts I can show real progress, retry a single failed part, and
          fix problems at the boundaries. With more time I would add Batch as a second lane for Hindi and for
          speaker labels.
        </p>
      </Section>

      <Section title="What runs in the request and what runs in the background">
        <Table
          head={["Where", "What", "Why"]}
          rows={[
            ["In the API request (under a second)", "Create the recording and signed URL, confirm the upload and add the job, list, detail, retry, playback URL", "These only read or write a few rows, so the user gets an answer immediately."],
            ["In the browser", "The upload itself, straight to the bucket", "Keeps large files off my server and gives real upload progress."],
            ["In the background worker", "Download, check, convert, cut, transcribe, join, summarize, requeue stuck jobs", "These take seconds to minutes and depend on outside services that can be slow or fail."],
          ]}
        />
        <p>
          On Render&apos;s free tier there is no separate worker service, so the worker runs inside the API process
          as a background task. The same code can run on its own with <code>python -m app.worker</code>.
        </p>
      </Section>

      <Section title="Where files and data live">
        <Table
          head={["What", "Where", "For how long"]}
          rows={[
            ["Original audio", "Private Backblaze B2 bucket, recordings/<id>/original.<ext>", "Kept, so it can be played back"],
            ["16 kHz copy and part files", "A temporary folder on the worker", "Deleted when the job ends"],
            ["Parts, transcript, summary, status, errors", "Postgres", "Kept"],
            ["Upload and playback links", "Signed URLs", "15 minutes to upload, 1 hour to play"],
          ]}
        />
      </Section>

      <Section title="Progress">
        <p>
          The upload bar comes from the browser&apos;s upload progress events. After that, progress is simply
          the number of finished parts out of the total, which the worker updates in Postgres after every part.
          The page polls every 2 seconds, slows to 5 seconds after a minute, pauses when the tab is hidden, and
          stops when the recording is done or failed. Because all state is in the database, you can refresh or
          close the page and come back to the same place.
        </p>
        <p>
          I chose polling over WebSockets because the progress is already saved per part, and a plain request
          every 2 seconds keeps working through refreshes and a sleeping server.
        </p>
      </Section>

      <Section title="What happens when things fail">
        <Table
          head={["Failure", "What the app does", "What you see"]}
          rows={[
            ["Wrong file type or too large", "Checked in the browser and again in the API", "A message before anything uploads"],
            ["Upload interrupted", "The browser reports the error", "\"The upload was interrupted\"; the file stays selected to retry"],
            ["Corrupted or non-audio file", "ffprobe fails before any Gnani call", "\"We couldn't read any audio in this file\""],
            ["Silence only", "Every part comes back empty", "\"No speech was detected\""],
            ["Gnani 429, 5xx or timeout", "Retry that part up to 4 times with increasing waits", "Progress continues; the part shows it is retrying"],
            ["Gnani rejects one part (400)", "Mark only that part failed and continue", "Transcript with that part marked, plus \"Retry failed parts\""],
            ["Gnani key or credits problem (403)", "Stop immediately; retrying can't help", "A clear error with \"Try again\""],
            ["Gemini overloaded", "Try the model twice, then fall back to a lighter model", "Usually nothing; otherwise the transcript plus \"Retry summary\""],
            ["Server restarts mid-job", "The job's heartbeat goes stale and it is requeued; finished parts are skipped", "Progress pauses, then continues from the next part"],
            ["Server asleep (free tier)", "The page keeps retrying", "\"Starting the server… about a minute\""],
          ]}
        />
        <p>
          I tested the restart case by stopping the server at part 3 of 9. After the restart it continued from
          part 4 without transcribing the first three again.
        </p>
      </Section>

      <Section title="Limits of this demo">
        <ul className="list-disc space-y-1 pl-6">
          <li>Files up to 500 MB and 60 minutes, and 10 uploads per hour per IP address. These protect the free Gnani credits (about ₹0.43 per audio minute in my tests).</li>
          <li>The backend sleeps after 15 minutes without traffic, so the first request can take about a minute.</li>
          <li>There are no accounts, so everyone sees the same list of recordings.</li>
          <li>Gnani&apos;s English output has no punctuation, so the transcript is shown as it comes.</li>
          <li>Gnani&apos;s Hindi mode sometimes drops parts of Hindi-English speech. Short parts limit the loss.</li>
          <li>The summary uses Gemini&apos;s free tier, where Google may use the content to improve its products.</li>
        </ul>
      </Section>

      <Section title="What I would do with more time">
        <ul className="list-disc space-y-1 pl-6">
          <li>Add the Batch API as a second lane for Hindi and for speaker labels.</li>
          <li>Flag parts that return far fewer words than their length suggests, so silent losses are visible.</li>
          <li>Push progress to the page with server-sent events instead of polling.</li>
          <li>Resumable multipart uploads for very large files and unreliable networks.</li>
          <li>Accounts, so each person sees only their own recordings.</li>
          <li>Run the worker as its own service and scale it by queue length.</li>
          <li>Add punctuation to the transcript with an LLM pass, kept separate from the raw text.</li>
          <li>Delete old audio automatically with a bucket lifecycle rule.</li>
        </ul>
      </Section>
    </main>
  );
}