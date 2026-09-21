/**
 * config.ts — fixed backend location.
 *
 * The published app must never ask a customer for a server address or an API
 * token (the old Connection screen did both, and no member could get past it).
 * The base URL is baked in here; the customer signs in with their email and a
 * 6-digit code, and every call is member-scoped by the session token.
 */

export const BASE_URL = 'https://omni.alphadirect.co.bw';

/** All customer endpoints live under this prefix. */
export const API_PREFIX = '/api/v1/rewards/customer';
