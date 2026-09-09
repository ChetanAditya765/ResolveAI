"use client";
import { useCallback, useEffect, useState } from "react";
import { errorMessage } from "./api";

// A single request cycle at a time; cleanup also fences late responses after navigation.
export function usePoll<T>(
  load: (signal: AbortSignal) => Promise<T>,
  interval = 3000,
) {
  const [data, setData] = useState<T>();
  const [error, setError] = useState<string>();
  const [version, setVersion] = useState(0);
  const refresh = useCallback(() => setVersion((value) => value + 1), []);
  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const controller = new AbortController();
    async function update() {
      try {
        const result = await load(controller.signal);
        if (!stopped) {
          setData(result);
          setError(undefined);
        }
      } catch (failure) {
        if (!stopped) setError(errorMessage(failure));
      } finally {
        if (!stopped && interval > 0) timer = setTimeout(update, interval);
      }
    }
    void update();
    return () => {
      stopped = true;
      controller.abort();
      clearTimeout(timer);
    };
  }, [load, interval, version]);
  return { data, error, refresh };
}
