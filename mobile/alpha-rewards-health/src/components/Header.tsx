/**
 * Header.tsx — branded top bar with optional Consent / Sign out shortcuts.
 */

import React from 'react';
import {StyleSheet, Text, TouchableOpacity, View} from 'react-native';
import {colors, fontSize, spacing} from '../theme';

type Props = {
  onSignOut?: () => void;
  onConsent?: () => void;
};

export default function Header({onSignOut, onConsent}: Props): React.JSX.Element {
  return (
    <View style={styles.bar}>
      <View style={styles.brandRow}>
        <View style={styles.dot} />
        <Text style={styles.brand}>Alpha Nexus</Text>
      </View>
      <View style={styles.actions}>
        {onConsent && (
          <TouchableOpacity onPress={onConsent} style={styles.link}>
            <Text style={styles.linkText}>Consent</Text>
          </TouchableOpacity>
        )}
        {onSignOut && (
          <TouchableOpacity onPress={onSignOut} style={styles.link}>
            <Text style={styles.linkText}>Sign out</Text>
          </TouchableOpacity>
        )}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  bar: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.md,
    backgroundColor: colors.bg,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: colors.border,
  },
  brandRow: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  dot: {
    width: 14,
    height: 14,
    borderRadius: 7,
    backgroundColor: colors.primary,
    marginRight: spacing.sm,
  },
  brand: {
    color: colors.text,
    fontSize: fontSize.subtitle,
    fontWeight: '700',
  },
  actions: {
    flexDirection: 'row',
  },
  link: {
    marginLeft: spacing.md,
  },
  linkText: {
    color: colors.textMuted,
    fontSize: fontSize.caption,
    fontWeight: '600',
  },
});
