import assert from "node:assert/strict";
import test from "node:test";

import {
  filterProjectSources,
  formatProjectFileSize,
  mergeProjectItems,
  projectIdFromSearch,
  projectRequestErrorMessage,
  projectSourceFromArtifact,
  projectWorkspacePath,
} from "../lib/project-workspace";

test("builds and restores a shareable project address", () => {
  assert.equal(projectWorkspacePath(null), "/projects");
  assert.equal(projectWorkspacePath("project/1"), "/projects?project=project%2F1");
  assert.equal(projectIdFromSearch("?project=project%2F1"), "project/1");
  assert.equal(projectIdFromSearch("?project="), null);
  assert.equal(projectIdFromSearch(`?project=${"x".repeat(129)}`), null);
});

test("filters source filenames without changing the server order", () => {
  const sources = [
    projectSourceFromArtifact({
      artifact_id: "a1",
      run_id: "r1",
      task_id: null,
      artifact_type: "report",
      filename: "Quarterly Report.csv",
      mime_type: "text/csv",
      size_bytes: 2048,
      created_at: "2026-10-27T00:00:00Z",
    }),
    { kind: "upload" as const, id: "f1", filename: "notes.txt", sizeBytes: null,
      mimeType: null, createdAt: null },
  ];
  assert.deepEqual(filterProjectSources(sources, "report").map((item) => item.id), ["a1"]);
  assert.deepEqual(filterProjectSources(sources, "").map((item) => item.id), ["a1", "f1"]);
});

test("merges paged sources by stable identifier", () => {
  assert.deepEqual(
    mergeProjectItems(
      [{ id: "one", name: "old" }, { id: "two", name: "two" }],
      [{ id: "one", name: "new" }, { id: "three", name: "three" }],
      (item) => item.id,
    ),
    [{ id: "one", name: "new" }, { id: "two", name: "two" }, { id: "three", name: "three" }],
  );
});

test("formats known and unknown sizes and actionable failures", () => {
  assert.equal(formatProjectFileSize(512), "512 B");
  assert.equal(formatProjectFileSize(1536), "1.5 KB");
  assert.equal(formatProjectFileSize(null), "大小未知");
  assert.match(projectRequestErrorMessage({ status: 404 }, "读取项目"), /不存在|无权/);
  assert.match(projectRequestErrorMessage({ status: 503 }, "保存文件"), /暂时不可用/);
  assert.doesNotMatch(projectRequestErrorMessage(new Error("internal address"), "保存文件"), /internal/);
});
