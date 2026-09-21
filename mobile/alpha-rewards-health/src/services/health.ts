/**
 * health.ts — step counts from the platform health store.
 *
 * Android: Health Connect via the unified `react-native-health-link` API.
 * iOS:     Apple HealthKit via `react-native-health` DIRECTLY — see below.
 *
 * WHY iOS BYPASSES THE WRAPPER: react-native-health-link's iOS branch calls
 * `AppleHealthKit.initHealthKit(permissions)` with ONE argument, but the native
 * method signature is `initHealthKit:(NSDictionary *)input callback:(...)` and
 * requires TWO. So on iOS the wrapper errors at the bridge, and even when it
 * survives, authorisation is fire-and-forget so the read runs before the user
 * has granted anything. Calling the underlying library directly, promisified,
 * is the only way iOS works at all (Fable review, 4-Aug-2026). The Android path
 * is untouched — it is live in the Play Store.
 *
 * DPA / consent rules:
 *   - Only the data types the user explicitly ticked are requested.
 *   - Points are computed on the BACKEND, not here (see api.ts).
 *   - Health data is never persisted on-device; it is read transiently and posted.
 */

import {Platform} from 'react-native';
import {
  HealthLinkDataType,
  HealthLinkPermissions,
  initializeHealth,
  isAvailable,
  read,
} from 'react-native-health-link';

import type {ConsentState} from '../types';

const IOS = Platform.OS === 'ios';

/** The platform's health app, in the words the user knows it by. */
export const HEALTH_APP_NAME = IOS ? 'Apple Health' : 'Health Connect';

/* eslint-disable @typescript-eslint/no-var-requires */
/** Apple HealthKit, loaded only on iOS (the module is a no-op on Android). */
function appleHealthKit(): any {
  return require('react-native-health').default ?? require('react-native-health');
}
/* eslint-enable @typescript-eslint/no-var-requires */

/** Ask HealthKit for read-only step access. Resolves true once authorised. */
function iosInitHealthKit(): Promise<boolean> {
  const AppleHealthKit = appleHealthKit();
  const permissions = {
    permissions: {
      read: [AppleHealthKit.Constants.Permissions.StepCount],
      write: [],
    },
  };
  return new Promise((resolve, reject) => {
    AppleHealthKit.initHealthKit(permissions, (error: string) => {
      if (error) {
        reject(new Error(
          'Apple Health did not allow access to your steps. Open Settings › Health › Data Access & Devices › Alpha Nexus and allow Steps.',
        ));
        return;
      }
      resolve(true);
    });
  });
}

/** Today's step total from HealthKit. */
function iosStepCount(startDate: string): Promise<number> {
  const AppleHealthKit = appleHealthKit();
  return new Promise((resolve, reject) => {
    AppleHealthKit.getStepCount({date: startDate}, (error: string, result: {value?: number}) => {
      if (error) {
        reject(new Error('Could not read your steps from Apple Health.'));
        return;
      }
      resolve(Math.round(result?.value ?? 0));
    });
  });
}

/**
 * Build the read-only permission set from the user's granular consent.
 * v1 only supports steps; sleep/workouts are intentionally NOT mapped here
 * (they are surfaced as "later" in the UI) so we never request more than v1 scope.
 */
function buildReadPermissions(consent: ConsentState): HealthLinkPermissions[] {
  const read: HealthLinkPermissions[] = [];
  if (consent.steps) {
    read.push(HealthLinkPermissions.Steps);
  }
  // sleep / workouts: deliberately omitted in v1.
  return read;
}

/** Is the platform health store available + usable on this device? */
export async function healthConnectAvailable(): Promise<boolean> {
  if (IOS) {
    // HealthKit is present on every iPhone (not on iPad), and the library's own
    // isAvailable is callback-style, so ask it directly rather than through the
    // wrapper.
    return new Promise(resolve => {
      try {
        appleHealthKit().isAvailable((_err: unknown, ok: boolean) => resolve(ok === true));
      } catch {
        resolve(false);
      }
    });
  }
  try {
    return (await isAvailable()) === true;
  } catch (err) {
    console.warn('isAvailable failed', err);
    return false;
  }
}

/**
 * Request Health Connect permissions for exactly the consented types.
 * Call only AFTER the user has granted consent on the consent screen.
 * Returns false if no consented readable types or if the SDK is unavailable.
 */
export async function requestHealthPermissions(
  consent: ConsentState,
): Promise<boolean> {
  const readPermissions = buildReadPermissions(consent);
  if (readPermissions.length === 0) {
    return false;
  }
  const available = await healthConnectAvailable();
  if (!available) {
    throw new Error(
      IOS
        ? 'Apple Health is not available on this device.'
        : 'Health Connect is not available on this device. Install or update the Health Connect app and try again.',
    );
  }
  if (IOS) {
    return iosInitHealthKit();
  }
  // write is empty — we never write health data.
  await initializeHealth({read: readPermissions, write: []});
  return true;
}

/** Start/end ISO bounds for "today" in the device's local timezone. */
function todayBounds(): {start: string; end: string; dateKey: string} {
  const now = new Date();
  const start = new Date(now);
  start.setHours(0, 0, 0, 0);
  const end = new Date(now);
  end.setHours(23, 59, 59, 999);
  const dateKey = `${start.getFullYear()}-${String(start.getMonth() + 1).padStart(
    2,
    '0',
  )}-${String(start.getDate()).padStart(2, '0')}`;
  return {start: start.toISOString(), end: end.toISOString(), dateKey};
}

/**
 * Total step count for today.
 * Health Connect returns one record per contributing source/interval, so we
 * sum the per-record `value` (count) the health-link deserializer exposes.
 */
export async function getTodaySteps(): Promise<{steps: number; date: string}> {
  const {start, end, dateKey} = todayBounds();
  if (IOS) {
    return {steps: await iosStepCount(start), date: dateKey};
  }
  const records = await read(HealthLinkDataType.Steps, {
    startDate: start,
    endDate: end,
    ascending: true,
  });
  const total = records.reduce((sum, r) => {
    const value = typeof r.value === 'number' ? r.value : 0;
    return sum + value;
  }, 0);
  return {steps: Math.round(total), date: dateKey};
}
