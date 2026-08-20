import assert from "node:assert/strict";
import test from "node:test";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { MarkdownContent } from "../components/markdown/MarkdownContent";

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
  assert.match(markup, /<pre><code class="language-python">/);
  assert.match(markup, /<table>/);
});

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
