"use client";

import { useEffect, useRef, useState } from "react";

/**
 * Poll `fetcher` until `isDone(data)` is true.
 * - 2 s between polls for the first minute, then 5 s (long jobs don't need 2 s resolution)
 * - keeps polling through errors (server waking up, brief network loss) and reports them
 * - pauses while the tab is hidden, resumes immediately when it's visible again
 */
export function usePolling<T>(fetcher: () => Promise<T>, isDone: (data: T) => boolean) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);

  // keep the latest callbacks without restarting the effect on every render
  const fetcherRef = useRef(fetcher);
  const isDoneRef = useRef(isDone);
  fetcherRef.current = fetcher;
  isDoneRef.current = isDone;

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const startedAt = Date.now();

    async function tick() {
      if (cancelled) return;
      if (document.hidden) return; // paused; the visibility listener restarts us
      try {
        const result = await fetcherRef.current();
        if (cancelled) return;
        setData(result);
        setError(null);
        if (isDoneRef.current(result)) return; // terminal state: stop polling
      } catch (err) {
        if (cancelled) return;
        setError(err); // keep the last good data on screen and try again
      }
      const interval = Date.now() - startedAt < 60_000 ? 2000 : 5000;
      timer = setTimeout(tick, interval);
    }

    function onVisible() {
      if (!document.hidden) {
        clearTimeout(timer);
        tick();
      }
    }

    tick();
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      cancelled = true;
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, []);

  return { data, error };
}