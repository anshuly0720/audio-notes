"use client";

import { useEffect, useState } from "react";

const API_URL = process.env.NEXT_PUBLIC_API_URL;

type Status = "checking" | "ok" | "error";

export default function Home() {
  const [api, setApi] = useState<Status>("checking");
  const [db, setDb] = useState<Status>("checking");
  const [detail, setDetail] = useState("");

  useEffect(() => {
    // Hit one endpoint and record ok/error. A network failure (server asleep, CORS) lands in catch.
    async function check(path: string, set: (s: Status) => void) {
      try {
        const res = await fetch(`${API_URL}${path}`, { cache: "no-store" });
        set(res.ok ? "ok" : "error");
        if (!res.ok) setDetail(`${path} returned HTTP ${res.status}`);
      } catch (err) {
        set("error");
        setDetail(`${path}: ${String(err)}`);
      }
    }
    // liveness first, then the database
    check("/api/health", setApi).then(() => check("/api/health/db", setDb));
  }, []);

  return (
    <main className="mx-auto max-w-xl p-8 space-y-4">
      <h1 className="text-2xl font-semibold">Audio Notes</h1>
      <p>API: <strong>{api}</strong></p>
      <p>Database: <strong>{db}</strong></p>
      <p className="text-sm text-gray-500">API URL: {API_URL}</p>
      {detail && <p className="text-sm text-red-600">{detail}</p>}
    </main>
  );
}