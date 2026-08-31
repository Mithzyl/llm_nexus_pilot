"use client";

/** Recover from a root layout failure without exposing the original exception to the browser UI. */
export default function GlobalError({ reset }: { error: Error; reset: () => void }) {
  return (
    <html lang="zh-CN">
      <body>
        <main className="route-state-page">
          <section className="route-state" role="alert">
            <strong>NexusPilot 暂时无法显示</strong>
            <p>请重新加载应用；服务端已经保存的事实不会被清除。</p>
            <button type="button" onClick={reset}>重新加载</button>
          </section>
        </main>
      </body>
    </html>
  );
}
