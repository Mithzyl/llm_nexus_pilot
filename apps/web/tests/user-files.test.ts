import assert from "node:assert/strict";
import test from "node:test";
import { messageFiles, uploadUserFile, validateUploadFiles } from "../lib/user-files";

/** Reject unavailable isolated formats before creating a reservation or sending file bytes. */
test("checks Office availability before reserving a file", async () => {
  const original = globalThis.fetch;
  const calls: string[] = [];
  globalThis.fetch = (async (url) => {
    calls.push(String(url));
    return Response.json({ accepted_extensions: [".txt"], executor_status: "not_configured",
      unavailable_reason: "服务器尚未启用 Office／PDF 文件解析。" });
  }) as typeof fetch;
  try {
    await assert.rejects(uploadUserFile(new File(["fixture"], "report.docx"), "key-12345",
      new AbortController().signal), /尚未启用/);
    assert.deepEqual(calls, ["/api/nexus/files/capabilities"]);
  } finally { globalThis.fetch = original; }
});

test("rejects empty, oversized, unsupported and excessive attachments before upload", () => {
  assert.throws(() => validateUploadFiles([new File([], "empty.txt")]));
  assert.throws(() => validateUploadFiles([new File(["x"], "photo.png")]));
  assert.doesNotThrow(() => validateUploadFiles([new File([new Uint8Array(20 * 1024 * 1024)], "allowed.pdf")]));
  assert.throws(() => validateUploadFiles([new File([new Uint8Array(20 * 1024 * 1024 + 1)], "big.txt")]));
  assert.throws(() => validateUploadFiles(Array.from({ length: 11 }, () => new File(["x"], "a.txt"))));
  assert.doesNotThrow(() => validateUploadFiles([new File(["text"], "notes.TXT")]));
});

test("uploads raw bytes only after reservation and keeps backend parse status", async () => {
  const original = globalThis.fetch;
  const calls: RequestInit[] = [];
  globalThis.fetch = (async (_url, init) => {
    calls.push(init ?? {});
    return Response.json({ file_id: "file-1", status: calls.length === 1 ? "reserved" : "ready" });
  }) as typeof fetch;
  try {
    const result = await uploadUserFile(new File(["hello"], "notes.txt"), "stable-file-key", new AbortController().signal);
    assert.equal(result.status, "ready");
    const reservation = JSON.parse(calls[0].body as string);
    assert.equal(reservation.idempotency_key, "stable-file-key");
    assert.equal(reservation.content_hash.length, 64);
    assert.equal(calls[1].method, "PUT");
    assert.equal(new TextDecoder().decode(calls[1].body as ArrayBuffer), "hello");
  } finally { globalThis.fetch = original; }
});

test("does not invent attachment labels from invalid message metadata", () => {
  assert.deepEqual(messageFiles(null), []);
  assert.deepEqual(messageFiles({ files: [null, { filename: "missing ID" }] }), []);
  assert.equal(messageFiles({ files: [{ file_id: "id", filename: "notes.txt" }] }).length, 1);
});
