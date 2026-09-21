/**
 * HomeScreen.tsx — read today's steps via Health Connect, POST to the backend,
 * and show pointsAwarded / totalPoints / tier / streakDays.
 *
 * The device only reads steps and posts them; the backend decides the points.
 */

import React, {useState} from 'react';
import {Alert, RefreshControl, ScrollView, StyleSheet, Text, View} from 'react-native';

import Card from '../components/Card';
import PrimaryButton from '../components/PrimaryButton';
import {colors, fontSize, spacing} from '../theme';
import {HEALTH_APP_NAME, getTodaySteps, requestHealthPermissions} from '../services/health';
import {submitMetrics} from '../services/api';
import type {ConsentState, MetricsResponse, Session} from '../types';

type Props = {
  session: Session;
  consent: ConsentState;
};

export default function HomeScreen({session, consent}: Props): React.JSX.Element {
  const [busy, setBusy] = useState(false);
  const [steps, setSteps] = useState<number | null>(null);
  const [result, setResult] = useState<MetricsResponse | null>(null);
  const [lastError, setLastError] = useState<string | null>(null);

  const sync = async () => {
    if (!consent.steps || !consent.grantedAt) {
      Alert.alert(
        'Consent needed',
        'Grant consent for Steps before syncing your activity.',
      );
      return;
    }

    setBusy(true);
    setLastError(null);
    try {
      // Re-assert Health Connect permission (idempotent) then read steps.
      await requestHealthPermissions(consent);
      const {steps: todaySteps, date} = await getTodaySteps();
      setSteps(todaySteps);

      const response = await submitMetrics(session, {date, steps: todaySteps});
      setResult(response);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setLastError(message);
      Alert.alert('Sync failed', message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <ScrollView
      contentContainerStyle={styles.content}
      refreshControl={
        <RefreshControl refreshing={busy} onRefresh={sync} tintColor={colors.primary} />
      }>
      <Text style={styles.h1}>Today</Text>
      <Text style={styles.sub}>
        {session.memberName ? `Signed in as ${session.memberName}` : 'Signed in'}
      </Text>

      <Card style={styles.heroCard}>
        <Text style={styles.heroLabel}>Steps today</Text>
        <Text style={styles.heroValue}>
          {steps === null ? '—' : steps.toLocaleString()}
        </Text>
        <Text style={styles.heroHint}>
          {steps === null
            ? `Tap sync to read your steps from ${HEALTH_APP_NAME}.`
            : `Read from ${HEALTH_APP_NAME}.`}
        </Text>
      </Card>

      <View style={styles.statRow}>
        <Stat
          label="Points today"
          value={result ? `+${result.pointsAwarded}` : '—'}
          accent
        />
        <Stat
          label="Total points"
          value={result ? result.totalPoints.toLocaleString() : '—'}
        />
      </View>

      <View style={styles.statRow}>
        <Stat label="Tier" value={result ? result.tier : '—'} />
        <StreakStat days={result ? result.streakDays : null} />
      </View>

      {lastError && <Text style={styles.error}>{lastError}</Text>}

      <View style={styles.actions}>
        <PrimaryButton label="Sync today's activity" onPress={sync} busy={busy} />
      </View>

      <Text style={styles.footer}>
        Health data is used for rewards only. It never affects your premium or
        claims.
      </Text>
    </ScrollView>
  );
}

function Stat({
  label,
  value,
  accent,
}: {
  label: string;
  value: string;
  accent?: boolean;
}): React.JSX.Element {
  return (
    <Card style={styles.statCard}>
      <Text style={styles.statLabel}>{label}</Text>
      <Text style={[styles.statValue, accent && styles.statAccent]}>{value}</Text>
    </Card>
  );
}

function StreakStat({days}: {days: number | null}): React.JSX.Element {
  return (
    <Card style={styles.statCard}>
      <Text style={styles.statLabel}>Streak</Text>
      <View style={styles.streakRow}>
        <Text style={styles.flame}>🔥</Text>
        <Text style={styles.statValue}>
          {days === null ? '—' : `${days} day${days === 1 ? '' : 's'}`}
        </Text>
      </View>
    </Card>
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
  },
  heroCard: {
    marginBottom: spacing.md,
    alignItems: 'flex-start',
  },
  heroLabel: {
    color: colors.textMuted,
    fontSize: fontSize.caption,
    fontWeight: '700',
    textTransform: 'uppercase',
    letterSpacing: 1,
  },
  heroValue: {
    color: colors.text,
    fontSize: fontSize.hero,
    fontWeight: '800',
    marginTop: spacing.xs,
  },
  heroHint: {
    color: colors.textMuted,
    fontSize: fontSize.caption,
    marginTop: spacing.xs,
  },
  statRow: {
    flexDirection: 'row',
    gap: spacing.md,
    marginBottom: spacing.md,
  },
  statCard: {
    flex: 1,
    paddingVertical: spacing.md,
  },
  statLabel: {
    color: colors.textMuted,
    fontSize: fontSize.caption,
    fontWeight: '700',
  },
  statValue: {
    color: colors.text,
    fontSize: fontSize.title,
    fontWeight: '800',
    marginTop: spacing.xs,
  },
  statAccent: {
    color: colors.success,
  },
  streakRow: {
    flexDirection: 'row',
    alignItems: 'center',
    marginTop: spacing.xs,
  },
  flame: {
    fontSize: fontSize.subtitle,
    marginRight: spacing.xs,
  },
  error: {
    color: colors.danger,
    fontSize: fontSize.caption,
    marginBottom: spacing.md,
  },
  actions: {
    marginTop: spacing.sm,
  },
  footer: {
    color: colors.textMuted,
    fontSize: fontSize.caption,
    textAlign: 'center',
    marginTop: spacing.lg,
    lineHeight: 18,
  },
});
