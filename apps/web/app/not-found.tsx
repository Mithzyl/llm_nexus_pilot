import Link from "next/link";

/** Return an actionable neutral page for unknown application routes. */
export default function NotFound() {
  return (
    <main className="route-state-page">
      <section className="route-state">
        <strong>页面不存在或不可用</strong>
        <p>请返回对话列表重新选择资源。</p>
        <Link href="/">返回新对话</Link>
      </section>
    </main>
  );
}
