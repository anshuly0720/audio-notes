import Link from "next/link";
import UploadForm from "@/components/UploadForm";
import RecordingList from "@/components/RecordingList";
export default function Home() {
  return (
    <main className="mx-auto max-w-3xl space-y-8 px-4 py-10">
      <header className="flex items-baseline justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Audio Notes</h1>
          <p className="text-zinc-600 dark:text-zinc-400">Upload audio, get a transcript and a summary.</p>
        </div>
        <Link href="/architecture" className="text-sm underline">How it works</Link>
      </header>

      <UploadForm />
      <RecordingList/>
    </main>
  );
}