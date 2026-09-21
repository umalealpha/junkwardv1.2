/**
 * App.tsx — Alpha Nexus (Android).
 *
 * Tiny hand-rolled navigator (no nav lib dependency) over three screens:
 *   Sign in   -> email + 6-digit code; stores the session token
 *   Consent   -> granular opt-in toggles; nothing is read until granted
 *   Home      -> read today's steps via Health Connect, POST, show points/tier/streak
 *
 * DPA / consent rules (Botswana Data Protection Act):
 *   - Only the data types the user explicitly ticked are ever requested or read.
 *   - The member is resolved from the login token, so no id leaves the device.
 *   - Points + tier + streak are computed on the BACKEND, never on-device.
 *   - This app is rewards-only. No health data flows into underwriting/pricing/claims.
 */

import React, {useCallback, useEffect, useState} from 'react';
import {Platform, SafeAreaView, StatusBar, StyleSheet, View} from 'react-native';

import {colors} from './src/theme';
import {clearSession, loadConsent, loadSession} from './src/services/storage';
import type {ConsentState, Session} from './src/types';
import SignInScreen from './src/screens/SignInScreen';
import ConsentScreen from './src/screens/ConsentScreen';
import HomeScreen from './src/screens/HomeScreen';
import Header from './src/components/Header';

type Route = 'loading' | 'signin' | 'consent' | 'home';

// Android draws behind the system bars from API 35, and API 36 removed the opt-out,
// so from there we reserve the status-bar strip ourselves — otherwise the Header
// slides up underneath the clock. Below API 35 the window is not edge-to-edge and
// adding the inset would leave a blank gap instead. iOS uses SafeAreaView's inset.
const statusBarInset =
  Platform.OS === 'android' && Number(Platform.Version) >= 35
    ? StatusBar.currentHeight ?? 0
    : 0;

const DEFAULT_CONSENT: ConsentState = {
  steps: false,
  sleep: false,
  workouts: false,
  grantedAt: null,
};

export default function App(): React.JSX.Element {
  const [route, setRoute] = useState<Route>('loading');
  const [session, setSession] = useState<Session | null>(null);
  const [consent, setConsent] = useState<ConsentState>(DEFAULT_CONSENT);

  // Decide the starting screen from persisted state.
  useEffect(() => {
    (async () => {
      const [savedSession, savedConsent] = await Promise.all([
        loadSession(),
        loadConsent(),
      ]);
      if (savedSession) {
        setSession(savedSession);
      }
      if (savedConsent) {
        setConsent(savedConsent);
      }

      if (!savedSession) {
        setRoute('signin');
      } else if (!savedConsent || !savedConsent.grantedAt) {
        setRoute('consent');
      } else {
        setRoute('home');
      }
    })();
  }, []);

  const onSignedIn = useCallback(
    (next: Session) => {
      setSession(next);
      setRoute(consent.grantedAt ? 'home' : 'consent');
    },
    [consent.grantedAt],
  );

  const onSignOut = useCallback(async () => {
    await clearSession();
    setSession(null);
    setRoute('signin');
  }, []);

  const onConsentGranted = useCallback((next: ConsentState) => {
    setConsent(next);
    setRoute('home');
  }, []);

  const onConsentRevoked = useCallback((next: ConsentState) => {
    setConsent(next);
    setRoute('consent');
  }, []);

  return (
    <SafeAreaView style={[styles.root, {paddingTop: statusBarInset}]}>
      <StatusBar barStyle="dark-content" backgroundColor={colors.bg} />
      <Header
        onSignOut={session ? onSignOut : undefined}
        onConsent={route === 'home' ? () => setRoute('consent') : undefined}
      />
      <View style={styles.body}>
        {route === 'signin' && <SignInScreen onSignedIn={onSignedIn} />}
        {route === 'consent' && session && (
          <ConsentScreen
            initial={consent}
            session={session}
            onGranted={onConsentGranted}
            onRevoked={onConsentRevoked}
          />
        )}
        {route === 'home' && session && (
          <HomeScreen session={session} consent={consent} />
        )}
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
    backgroundColor: colors.bg,
  },
  body: {
    flex: 1,
  },
});
