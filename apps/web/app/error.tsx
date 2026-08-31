"use client";

import { useEffect } from "react";

/** Isolate an unexpected route rendering failure and let the user retry without clearing durable data. */
export default function RouteError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error("NexusPilot route rendering failed", error);
  }, [error]);

  return (
    <main className="route-state-page">
      <section className="route-state" role="alert">
        <strong>页面渲染失败</strong>
        <p>已保存的会话和运行不会因此被修改。可以重新加载当前页面。</p>
        <button type="button" onClick={reset}>重新加载</button>
      </section>
    </main>
  );
}
