import assert from "node:assert/strict";
import test from "node:test";
import { guardViewUpdate } from "../lib/view-updates";

/** Late output and cleanup from A must not replace B or clear B's busy state. */
test("detached execution cannot update the newly selected conversation", async () => {
  let generation = 1;
  let text = "A";
  let busy = true;
  const update = guardViewUpdate(() => generation === 1, (value: string) => { text = value; });
  const finish = guardViewUpdate(() => generation === 1, () => { busy = false; });
  update("A streaming");
  assert.equal(text, "A streaming");
  generation = 2;
  text = "B";
  await Promise.resolve();
  update("A completed");
  finish();
  assert.equal(text, "B");
  assert.equal(busy, true);
});
