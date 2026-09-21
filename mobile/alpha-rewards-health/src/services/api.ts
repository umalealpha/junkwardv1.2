/**
 * api.ts — thin client to the omni customer API.
 *
 * IMPORTANT: the device sends raw metrics; the BACKEND computes points and tier.
 * This keeps scoring rules central, auditable, and not tamperable from the phone.
 *
 * Endpoints (all under {BASE_URL}{API_PREFIX}):
 *   POST /request-otp/     { email }                 -> { ok }
 *   POST /verify-otp/      { email, code }           -> { token, member }
 *   POST /health-consent/  { dataTypes, grantedAt }  -> { ok }
 *   POST /health-metrics/  { date, steps }
 *                          -> { pointsAwarded, totalPoints, tier, streakDays }
 *
 * Auth: the session token from sign-in, sent as `Authorization: Bearer`. The
 * backend resolves the member from that token — the phone never sends a member
 * id, so a signed-in customer can only ever read or write their own record.
 */

import {API_PREFIX, BASE_URL} from '../config';
import type {ConsentState, MetricsResponse, Session} from '../types';

async function request<T>(
  path: string,
  body: unknown,
  token?: string,
): Promise<T> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    Accept: 'application/json',
  };
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }

  let res: Response;
  try {
    res = await fetch(`${BASE_URL}${API_PREFIX}${path}`, {
      method: 'POST',
      headers,
      body: JSON.stringify(body),
    });
  } catch (err) {
    throw new Error('No connection. Check your internet and try again.');
  }

  // Only an AUTHENTICATED call can have an expired session. /verify-otp/ also
  // answers 401 for a wrong or stale code, and that message ("That code is
  // invalid or has expired.") must reach the customer instead.
  if (res.status === 401 && token) {
    throw new Error('Your sign-in has expired. Please sign in again.');
  }

  if (!res.ok) {
    // Prefer the backend's own message — it is already written for customers.
    let detail = '';
    try {
      const parsed = (await res.json()) as {detail?: string};
      detail = parsed?.detail ?? '';
    } catch {
      detail = '';
    }
    throw new Error(detail || `Something went wrong (${res.status}).`);
  }

  return (await res.json()) as T;
}

/** Ask the backend to email a 6-digit sign-in code. */
export async function requestCode(email: string): Promise<void> {
  await request('/request-otp/', {email: email.trim().toLowerCase()});
}

/** Exchange an emailed code for a session token. */
export async function verifyCode(
  email: string,
  code: string,
): Promise<Session> {
  const res = await request<{token?: string; member?: {name?: string}}>(
    '/verify-otp/',
    {email: email.trim().toLowerCase(), code: code.trim()},
  );
  if (!res.token) {
    throw new Error('That code did not work. Request a new one.');
  }
  return {token: res.token, memberName: res.member?.name ?? ''};
}

/** Which data types the user consented to, as backend-facing strings. */
function consentedDataTypes(consent: ConsentState): string[] {
  const types: string[] = [];
  if (consent.steps) {
    types.push('steps');
  }
  if (consent.sleep) {
    types.push('sleep');
  }
  if (consent.workouts) {
    types.push('workouts');
  }
  return types;
}

/** Record the user's granular consent choices with the backend. */
export async function sendConsent(
  session: Session,
  consent: ConsentState,
): Promise<void> {
  await request(
    '/health-consent/',
    {
      dataTypes: consentedDataTypes(consent),
      grantedAt: consent.grantedAt ?? new Date().toISOString(),
    },
    session.token,
  );
}

/** Send today's normalised steps; backend returns points + tier + streak. */
export async function submitMetrics(
  session: Session,
  payload: {date: string; steps: number},
): Promise<MetricsResponse> {
  return request<MetricsResponse>(
    '/health-metrics/',
    {date: payload.date, steps: payload.steps},
    session.token,
  );
}
