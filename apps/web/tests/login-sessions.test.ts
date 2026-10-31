import assert from "node:assert/strict";
import test from "node:test";
import { loginSessionStatus } from "../lib/login-sessions";

/** Revocation and either expiry take precedence over the current-session label. */
test("session status distinguishes current, active, expired and revoked", () => {
  const session = { revoked_at: null, idle_expires_at: "2030-01-01", absolute_expires_at: "2031-01-01", is_current: false };
  const now = Date.parse("2026-10-30");
  assert.equal(loginSessionStatus(session, now), "active");
  assert.equal(loginSessionStatus({...session, is_current: true}, now), "current");
  assert.equal(loginSessionStatus({...session, idle_expires_at: "2020-01-01"}, now), "expired");
  assert.equal(loginSessionStatus({...session, absolute_expires_at: "2020-01-01"}, now), "expired");
  assert.equal(loginSessionStatus({...session, revoked_at: "2026-10-29", is_current: true}, now), "revoked");
});
