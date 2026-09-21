/**
 * storage.ts — local persistence via AsyncStorage.
 *
 * Stores the sign-in session (token + display name) and the user's granular
 * consent choices. NO health data is persisted on-device.
 */

import AsyncStorage from '@react-native-async-storage/async-storage';
import type {ConsentState, Session} from '../types';

const SESSION_KEY = '@alpha_nexus/session';
const CONSENT_KEY = '@alpha_rewards_health/consent';
// Pre-sign-in builds stored a base URL + a pasted API token here. That is dead
// now and must not linger on a device that upgrades.
const LEGACY_SETTINGS_KEY = '@alpha_rewards_health/settings';

export async function loadSession(): Promise<Session | null> {
  try {
    await AsyncStorage.removeItem(LEGACY_SETTINGS_KEY);
    const raw = await AsyncStorage.getItem(SESSION_KEY);
    if (!raw) {
      return null;
    }
    const parsed = JSON.parse(raw) as Partial<Session>;
    if (!parsed.token) {
      return null;
    }
    return {token: parsed.token, memberName: parsed.memberName ?? ''};
  } catch (err) {
    console.warn('loadSession failed', err);
    return null;
  }
}

export async function saveSession(session: Session): Promise<void> {
  await AsyncStorage.setItem(SESSION_KEY, JSON.stringify(session));
}

export async function clearSession(): Promise<void> {
  await AsyncStorage.removeItem(SESSION_KEY);
}

export async function loadConsent(): Promise<ConsentState | null> {
  try {
    const raw = await AsyncStorage.getItem(CONSENT_KEY);
    if (!raw) {
      return null;
    }
    const parsed = JSON.parse(raw) as Partial<ConsentState>;
    return {
      steps: Boolean(parsed.steps),
      sleep: Boolean(parsed.sleep),
      workouts: Boolean(parsed.workouts),
      grantedAt: parsed.grantedAt ?? null,
    };
  } catch (err) {
    console.warn('loadConsent failed', err);
    return null;
  }
}

export async function saveConsent(consent: ConsentState): Promise<void> {
  await AsyncStorage.setItem(CONSENT_KEY, JSON.stringify(consent));
}

export async function clearConsent(): Promise<void> {
  await AsyncStorage.removeItem(CONSENT_KEY);
}
