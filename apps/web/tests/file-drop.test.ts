import assert from "node:assert/strict";
import test from "node:test";
import { isFileDrag } from "../lib/file-drop";

/** Only file drags should suppress the browser's open-file navigation. */
test("file drop detection leaves text and URL drops untouched", () => {
  assert.equal(isFileDrag(["Files"]), true);
  assert.equal(isFileDrag(["text/plain", "Files"]), true);
  assert.equal(isFileDrag(["text/plain", "text/uri-list"]), false);
  assert.equal(isFileDrag([]), false);
});
