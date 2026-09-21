/**
 * types.ts — shared app types.
 */

/** A signed-in customer session. The token is issued by the backend after
 * email-OTP sign-in; the member is resolved from it server-side, so the phone
 * never sends (or needs to know) a member id. */
export type Session = {
  token: string;
  memberName: string;
};

/**
 * Granular consent. Steps is live in v1; sleep/workouts are shown as "later"
 * and are never read even if somehow set true.
 */
export type ConsentState = {
  steps: boolean;
  sleep: boolean;
  workouts: boolean;
  grantedAt: string | null; // ISO timestamp when consent was last granted
};

/** Backend response from rewards/customer/health-metrics/. */
export type MetricsResponse = {
  pointsAwarded: number;
  totalPoints: number;
  tier: string; // bronze | silver | gold | platinum
  streakDays: number;
};
