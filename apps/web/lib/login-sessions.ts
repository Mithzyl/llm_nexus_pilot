export type LoginSession = {
  login_session_id: string;
  device_name: string | null;
  authenticated_at: string;
  last_used_at: string;
  idle_expires_at: string;
  absolute_expires_at: string;
  revoked_at: string | null;
  is_current: boolean;
};

/** Describe server session timestamps without treating revoked or expired sessions as usable. */
export function loginSessionStatus(session: Pick<LoginSession, "revoked_at" | "idle_expires_at" | "absolute_expires_at" | "is_current">, now: number) {
  if (session.revoked_at) return "revoked";
  if (!(Date.parse(session.idle_expires_at) > now) || !(Date.parse(session.absolute_expires_at) > now)) return "expired";
  return session.is_current ? "current" : "active";
}
