import assert from "node:assert/strict";
import test from "node:test";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { MarkdownContent } from "../components/markdown/MarkdownContent";

/** Preserve semantic GFM while adding a distinct toolbar only to block code. */
test("renders assistant Markdown as semantic GFM content", () => {
  const markup = renderToStaticMarkup(
    createElement(MarkdownContent, {
      content: [
        "### 上传方式",
        "",
        "支持 **重点**、`inline()` 和列表：",
        "",
        "- 第一项",
        "- 第二项",
        "",
        "```python",
        "client.files.create(file=open('test.pdf', 'rb'))",
        "```",
        "",
        "| 字段 | 含义 |",
        "| --- | --- |",
        "| file | 本地文件 |",
      ].join("\n"),
    }),
  );

  assert.match(markup, /<h3>上传方式<\/h3>/);
  assert.match(markup, /<strong>重点<\/strong>/);
  assert.match(markup, /<code>inline\(\)<\/code>/);
  assert.match(markup, /<ul>/);
  assert.match(markup, /class="markdown-code-language"[^>]*>python<\/span>/);
  assert.match(markup, /aria-label="复制代码"/);
  assert.match(markup, /<code class="language-python">/);
  assert.match(markup, /<pre[^>]+tabindex="0"/);
  assert.match(markup, /<table>/);
});

/** Keep raw HTML, unsafe navigation, and remote image requests disabled. */
test("does not execute raw HTML or automatically load model-generated images", () => {
  const markup = renderToStaticMarkup(
    createElement(MarkdownContent, {
      content: [
        "<script>alert('unsafe')</script>",
        "",
        "[危险链接](javascript:alert('unsafe'))",
        "",
        "[文档](https://example.com/docs)",
        "",
        "![远程图片](https://tracker.example/pixel.png)",
      ].join("\n"),
    }),
  );

  assert.doesNotMatch(markup, /<script>/);
  assert.doesNotMatch(markup, /javascript:/);
  assert.match(markup, /target="_blank"/);
  assert.match(markup, /rel="noopener noreferrer"/);
  assert.doesNotMatch(markup, /<img/);
  assert.match(markup, /远程图片/);
});

/** Keep an unfinished streaming fence readable and independently copyable. */
test("renders an unfinished streaming code fence with a toolbar", () => {
  const markup = renderToStaticMarkup(createElement(MarkdownContent, {
    content: "```typescript\nconst greeting = '你好';\n  nextLine(",
  }));

  assert.match(markup, /class="markdown-code-language"[^>]*>typescript<\/span>/);
  assert.match(markup, /nextLine\(/);
  assert.equal((markup.match(/aria-label="复制代码"/g) ?? []).length, 1);
});

/** Provide one toolbar per fenced or indented block without decorating inline code. */
test("supports code without a language and leaves inline code untouched", () => {
  const markup = renderToStaticMarkup(createElement(MarkdownContent, {
    content: "`inline`\n\n```\nplain code\n```\n\n    indented code\n",
  }));

  assert.equal((markup.match(/aria-label="复制代码"/g) ?? []).length, 2);
  assert.equal((markup.match(/class="markdown-code-language"[^>]*>代码<\/span>/g) ?? []).length, 2);
  assert.match(markup, /<code>inline<\/code>/);
});

/** Evidence prose may retain raw HTML as text without entering the HTML execution path. */
test("can preserve raw HTML as inert text for evidence prose", () => {
  const markup = renderToStaticMarkup(
    createElement(MarkdownContent, {
      content: "<script>alert('unsafe')</script>",
      preserveHtmlText: true,
    }),
  );
  assert.match(markup, /&lt;script&gt;/);
  assert.doesNotMatch(markup, /<script>/);
});
