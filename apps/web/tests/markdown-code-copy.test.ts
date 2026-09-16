import assert from "node:assert/strict";
import test from "node:test";

import { copyCodeText, sourceCodeFromRenderedText } from "../components/markdown/code-copy";

/** Remove only the renderer-added newline and retain meaningful whitespace. */
test("extracts original code without trimming indentation or trailing blank lines", () => {
  assert.equal(sourceCodeFromRenderedText("  const value = 1;  \n\n"), "  const value = 1;  \n");
  assert.equal(sourceCodeFromRenderedText(""), "");
  assert.equal(sourceCodeFromRenderedText("streaming(\n"), "streaming(");
});

/** Write precisely the supplied code, not toolbar labels or surrounding Markdown. */
test("copies exact code and reports success after the clipboard accepts it", async () => {
  const writes: string[] = [];
  const content = "  print('你好')\n\n";
  const result = await copyCodeText(content, {
    /** Record the exact payload accepted by the browser clipboard abstraction. */
    async writeText(value: string) { writes.push(value); },
  });

  assert.equal(result, "copied");
  assert.deepEqual(writes, [content]);
});

/** Treat denied or unavailable clipboard access as a visible failure, not success. */
test("reports clipboard rejection and unavailable clipboard without throwing", async () => {
  assert.equal(await copyCodeText("code", {
    /** Reproduce a browser denying permission to write the clipboard. */
    async writeText() { throw new Error("NotAllowedError"); },
  }), "failed");
  assert.equal(await copyCodeText("code", undefined), "failed");
});
