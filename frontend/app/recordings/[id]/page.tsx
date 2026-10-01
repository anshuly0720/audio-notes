"use client";

import { useParams } from "next/navigation";

export default function RecordingPage() {
  const { id } = useParams<{ id: string }>();
  return <main className="mx-auto max-w-3xl px-4 py-10">Recording {id} — detail page comes next.</main>;
}