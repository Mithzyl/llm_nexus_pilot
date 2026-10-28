import assert from "node:assert/strict";
import test from "node:test";

import { filterSessionsByTitle, groupSessionsByDate, resolveActiveSession, isActiveSessionReadyForSubmission, type SessionDateGroupKey } from "../lib/session-list";

/** Direct routes must not create a replacement conversation before identity loads. */
test("only submits after the route session identity has loaded", () => {
  assert.equal(isActiveSessionReadyForSubmission(null, null), true);
  assert.equal(isActiveSessionReadyForSubmission("session-1", null), false);
  assert.equal(isActiveSessionReadyForSubmission("session-1", { session_id: "session-2" }), false);
  assert.equal(isActiveSessionReadyForSubmission("session-1", { session_id: "session-1" }), true);
});

test("route session retains its project outside the sidebar page and never leaks to another route", () => {
  const loaded = { session_id: "project-session", project_id: "project-a" };
  assert.equal(resolveActiveSession([], "project-session", loaded)?.project_id, "project-a");
  assert.equal(resolveActiveSession([], "another-session", loaded), null);
  assert.equal(resolveActiveSession([], null, loaded), null);
});

const now = new Date(2026, 9, 21, 12, 0, 0);

function session(title: string | null, updatedAt: string) {
  return { session_id: title ?? "untitled", title, updated_at: updatedAt };
}

test("filters sessions by title only, case-insensitively", () => {
  const items = [session("General Agent Strata", "2026-10-21T10:00:00+08:00"), session("hello", "2026-10-20T10:00:00+08:00")];
  assert.deepEqual(filterSessionsByTitle(items, "  strata ").map((item) => item.title), ["General Agent Strata"]);
  assert.deepEqual(filterSessionsByTitle(items, "hello").map((item) => item.title), ["hello"]);
  assert.deepEqual(filterSessionsByTitle(items, "not-present"), []);
  assert.equal(filterSessionsByTitle(items, "").length, 2);
});

test("groups sessions into today, yesterday and earlier while preserving order", () => {
  const groups = groupSessionsByDate([
    session("today-a", "2026-10-21T09:00:00+08:00"),
    session("yesterday-a", "2026-10-20T09:00:00+08:00"),
    session("today-b", "2026-10-21T08:00:00+08:00"),
    session("older", "2026-10-18T09:00:00+08:00"),
  ], now);
  assert.deepEqual(groups.map((group) => group.label), ["今天", "昨天", "更早"]);
  assert.deepEqual(groups.map((group) => group.items.map((item) => item.title)), [
    ["today-a", "today-b"],
    ["yesterday-a"],
    ["older"],
  ]);
  assert.deepEqual(groups.map((group) => group.key), ["today", "yesterday", "earlier"] as SessionDateGroupKey[]);
});

test("treats invalid dates as earlier rather than guessing", () => {
  const groups = groupSessionsByDate([session("invalid", "not-a-date")], now);
  assert.deepEqual(groups.map((group) => [group.label, group.items.length]), [["更早", 1]]);
});
