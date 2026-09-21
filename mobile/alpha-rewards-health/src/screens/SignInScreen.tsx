/**
 * SignInScreen.tsx — email + 6-digit code sign-in.
 *
 * This replaces the old "Connection" screen, which asked the customer for a
 * server address and an API token — nobody outside the office could get past
 * it. Two steps now: type your email, then type the code we email you.
 */

import React, {useState} from 'react';
import {
  KeyboardAvoidingView,
  Platform,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from 'react-native';

import Card from '../components/Card';
import PrimaryButton from '../components/PrimaryButton';
import {colors, fontSize, radius, spacing} from '../theme';
import {requestCode, verifyCode} from '../services/api';
import {saveSession} from '../services/storage';
import type {Session} from '../types';

type Props = {
  onSignedIn: (session: Session) => void;
};

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export default function SignInScreen({onSignedIn}: Props): React.JSX.Element {
  const [step, setStep] = useState<'email' | 'code'>('email');
  const [email, setEmail] = useState('');
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const onSendCode = async () => {
    const trimmed = email.trim();
    if (!EMAIL_RE.test(trimmed)) {
      setError('Enter the email address on your policy.');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await requestCode(trimmed);
      setNotice(`We sent a 6-digit code to ${trimmed}.`);
      setStep('code');
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const onVerify = async () => {
    if (code.trim().length < 4) {
      setError('Enter the code from your email.');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const session = await verifyCode(email, code);
      await saveSession(session);
      onSignedIn(session);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const onStartOver = () => {
    setStep('email');
    setCode('');
    setError(null);
    setNotice(null);
  };

  return (
    <KeyboardAvoidingView
      style={styles.flex}
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
      <ScrollView contentContainerStyle={styles.content}>
        <Text style={styles.h1}>Sign in</Text>
        <Text style={styles.sub}>
          {step === 'email'
            ? 'Use the email address on your Alpha Direct policy. We will email you a code — no password to remember.'
            : 'Enter the 6-digit code we just emailed you. It expires in 10 minutes.'}
        </Text>

        <Card style={styles.card}>
          {step === 'email' ? (
            <Field
              label="Email address"
              value={email}
              onChangeText={setEmail}
              placeholder="you@example.com"
              autoCapitalize="none"
              keyboardType="email-address"
              last
            />
          ) : (
            <Field
              label="6-digit code"
              value={code}
              onChangeText={setCode}
              placeholder="123456"
              autoCapitalize="none"
              keyboardType="number-pad"
              maxLength={6}
              last
            />
          )}
        </Card>

        {notice && <Text style={styles.notice}>{notice}</Text>}
        {error && <Text style={styles.error}>{error}</Text>}

        <View style={styles.actions}>
          {step === 'email' ? (
            <PrimaryButton label="Email me a code" onPress={onSendCode} busy={busy} />
          ) : (
            <>
              <PrimaryButton label="Sign in" onPress={onVerify} busy={busy} />
              <TouchableOpacity onPress={onStartOver} style={styles.secondary}>
                <Text style={styles.secondaryText}>Use a different email</Text>
              </TouchableOpacity>
            </>
          )}
        </View>

        <Text style={styles.footer}>
          Your email is used only to send your own sign-in code. Health data is
          used for rewards only — it never affects your premium or your claims.
        </Text>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

type FieldProps = {
  label: string;
  value: string;
  onChangeText: (t: string) => void;
  placeholder?: string;
  autoCapitalize?: 'none' | 'characters' | 'words' | 'sentences';
  keyboardType?: 'default' | 'email-address' | 'number-pad';
  maxLength?: number;
  last?: boolean;
};

function Field({
  label,
  value,
  onChangeText,
  placeholder,
  autoCapitalize,
  keyboardType,
  maxLength,
  last,
}: FieldProps): React.JSX.Element {
  return (
    <View style={[styles.field, last && styles.fieldLast]}>
      <Text style={styles.label}>{label}</Text>
      <TextInput
        value={value}
        onChangeText={onChangeText}
        placeholder={placeholder}
        placeholderTextColor={colors.textMuted}
        autoCapitalize={autoCapitalize}
        keyboardType={keyboardType}
        maxLength={maxLength}
        autoCorrect={false}
        style={styles.input}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  flex: {flex: 1},
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
  card: {
    marginBottom: spacing.md,
  },
  field: {
    marginBottom: spacing.md,
  },
  fieldLast: {
    marginBottom: 0,
  },
  label: {
    color: colors.text,
    fontSize: fontSize.caption,
    fontWeight: '700',
    marginBottom: spacing.xs,
  },
  input: {
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.button,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.md,
    color: colors.text,
    fontSize: fontSize.body,
    backgroundColor: colors.bg,
  },
  notice: {
    color: colors.textMuted,
    fontSize: fontSize.caption,
    marginBottom: spacing.sm,
  },
  error: {
    color: colors.danger,
    fontSize: fontSize.caption,
    marginBottom: spacing.md,
  },
  actions: {
    marginTop: spacing.sm,
  },
  secondary: {
    alignItems: 'center',
    paddingVertical: spacing.md,
  },
  secondaryText: {
    color: colors.textMuted,
    fontSize: fontSize.caption,
    fontWeight: '600',
  },
  footer: {
    color: colors.textMuted,
    fontSize: fontSize.caption,
    marginTop: spacing.lg,
    lineHeight: 18,
  },
});
