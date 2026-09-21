/**
 * ConsentScreen.tsx — granular, consent-first opt-in.
 *
 * Rules enforced here:
 *   - Nothing is read until the user grants consent.
 *   - Only ticked types are requested from Health Connect.
 *   - Granting requests Health Connect permission for the consented types AND
 *     records the consent with the backend (rewards/customer/health-consent/).
 *   - The user can revoke consent locally at any time.
 */

import React, {useState} from 'react';
import {Alert, ScrollView, StyleSheet, Text, View} from 'react-native';

import Card from '../components/Card';
import ConsentToggle from '../components/ConsentToggle';
import PrimaryButton from '../components/PrimaryButton';
import {colors, fontSize, spacing} from '../theme';
import {saveConsent, clearConsent} from '../services/storage';
import {HEALTH_APP_NAME, requestHealthPermissions} from '../services/health';
import {sendConsent} from '../services/api';
import type {ConsentState, Session} from '../types';

type Props = {
  initial: ConsentState;
  session: Session;
  onGranted: (consent: ConsentState) => void;
  onRevoked: (consent: ConsentState) => void;
};

export default function ConsentScreen({
  initial,
  session,
  onGranted,
  onRevoked,
}: Props): React.JSX.Element {
  const [steps, setSteps] = useState(initial.steps);
  const [busy, setBusy] = useState(false);
  const alreadyGranted = Boolean(initial.grantedAt);

  const onGrant = async () => {
    if (!steps) {
      Alert.alert(
        'Nothing selected',
        'Tick at least Steps to start earning Health Points.',
      );
      return;
    }

    const next: ConsentState = {
      steps,
      sleep: false,
      workouts: false,
      grantedAt: new Date().toISOString(),
    };

    setBusy(true);
    try {
      // 1) Request Health Connect permission for exactly the consented types.
      await requestHealthPermissions(next);
      // 2) Persist consent locally.
      await saveConsent(next);
      // 3) Record consent with the backend.
      await sendConsent(session, next);
      onGranted(next);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      Alert.alert('Could not enable health access', message);
    } finally {
      setBusy(false);
    }
  };

  const onRevoke = async () => {
    const revoked: ConsentState = {
      steps: false,
      sleep: false,
      workouts: false,
      grantedAt: null,
    };
    await clearConsent();
    setSteps(false);
    onRevoked(revoked);
    Alert.alert(
      'Consent revoked',
      `The app will not read any health data until you grant consent again. To fully remove access, also revoke it in ${HEALTH_APP_NAME}.`,
    );
  };

  return (
    <ScrollView contentContainerStyle={styles.content}>
      <Text style={styles.h1}>Share your activity</Text>
      <Text style={styles.sub}>
        Turn healthy habits into Alpha Rewards points. You choose exactly what to
        share. Nothing is read until you allow it, and only a daily total is
        sent. Used for rewards only, never for pricing or claims.
      </Text>

      <Card style={styles.card}>
        <ConsentToggle
          title="Steps"
          subtitle={`Daily step count from ${HEALTH_APP_NAME}.`}
          value={steps}
          onChange={setSteps}
        />
        <ConsentToggle
          title="Sleep"
          subtitle="Sleep duration. Coming in a later release."
          value={false}
          comingSoon
        />
        <ConsentToggle
          title="Workouts"
          subtitle="Exercise sessions. Coming in a later release."
          value={false}
          comingSoon
        />
      </Card>

      <View style={styles.actions}>
        <PrimaryButton
          label={alreadyGranted ? 'Update consent' : 'Allow and continue'}
          onPress={onGrant}
          busy={busy}
        />
        {alreadyGranted && (
          <Text style={styles.revoke} onPress={onRevoke}>
            Revoke consent
          </Text>
        )}
      </View>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  content: {
    padding: spacing.md,
    paddingBottom: spacing.xl,
  },
  h1: {
    color: colors.text,
    fontSize: fontSize.title,
    fontWeight: '800',
    marginTop: spacing.sm,
  },
  sub: {
    color: colors.textMuted,
    fontSize: fontSize.body,
    marginTop: spacing.xs,
    marginBottom: spacing.md,
    lineHeight: 21,
  },
  card: {
    marginBottom: spacing.md,
  },
  actions: {
    marginTop: spacing.sm,
  },
  revoke: {
    color: colors.danger,
    fontSize: fontSize.caption,
    fontWeight: '700',
    textAlign: 'center',
    marginTop: spacing.md,
  },
});
