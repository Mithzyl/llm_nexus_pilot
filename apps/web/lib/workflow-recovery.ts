/** Wait between reads; cancellation releases the timer and listener immediately. */
function pause(milliseconds: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    /** Finish once on either timeout or cancellation. */
    const finish = () => {
      clearTimeout(timer);
      signal.removeEventListener("abort", finish);
      resolve();
    };
    const timer = setTimeout(finish, milliseconds);
    signal.addEventListener("abort", finish, { once: true });
    if (signal.aborted) finish();
  });
}

/** Follow an existing Run or workflow with serial reads and no execution writes. */
export async function followWorkflowResult<T extends { status: string }>(options: {
  signal: AbortSignal;
  read: (signal: AbortSignal) => Promise<T>;
  apply: (result: T, signal: AbortSignal) => void | Promise<void>;
  onError: (error: unknown) => void;
  intervalMs?: number;
}): Promise<void> {
  const interval = options.intervalMs ?? 1500;
  let failures = 0;
  while (!options.signal.aborted) {
    try {
      const signal = AbortSignal.any([options.signal, AbortSignal.timeout(15000)]);
      const result = await options.read(signal);
      if (options.signal.aborted) return;
      await options.apply(result, signal);
      if (options.signal.aborted) return;
      failures = 0;
      if (!["pending", "running", "waiting_for_input", "cancel_requested"].includes(result.status)) return;
    } catch (error) {
      if (options.signal.aborted) return;
      failures += 1;
      options.onError(error);
    }
    await pause(Math.min(interval * 2 ** Math.min(failures, 4), 30000), options.signal);
  }
}
