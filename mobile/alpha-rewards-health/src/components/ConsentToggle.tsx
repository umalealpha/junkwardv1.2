/**
 * ConsentToggle.tsx — a single granular consent row.
 * `comingSoon` rows are locked off (cannot be enabled) and labelled "Later".
 */

import React from 'react';
import {StyleSheet, Switch, Text, View} from 'react-native';
import {colors, fontSize, spacing} from '../theme';

type Props = {
  title: string;
  subtitle: string;
  value: boolean;
  onChange?: (next: boolean) => void;
  comingSoon?: boolean;
};

export default function ConsentToggle({
  title,
  subtitle,
  value,
  onChange,
  comingSoon,
}: Props): React.JSX.Element {
  return (
    <View style={styles.row}>
      <View style={styles.textCol}>
        <View style={styles.titleRow}>
          <Text style={styles.title}>{title}</Text>
          {comingSoon && (
            <View style={styles.badge}>
              <Text style={styles.badgeText}>Later</Text>
            </View>
          )}
        </View>
        <Text style={styles.subtitle}>{subtitle}</Text>
      </View>
      <Switch
        value={comingSoon ? false : value}
        disabled={comingSoon}
        onValueChange={onChange}
        trackColor={{false: colors.border, true: colors.primary}}
        thumbColor={colors.surface}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingVertical: spacing.md,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: colors.border,
  },
  textCol: {
    flex: 1,
    paddingRight: spacing.md,
  },
  titleRow: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  title: {
    color: colors.text,
    fontSize: fontSize.subtitle,
    fontWeight: '700',
  },
  subtitle: {
    color: colors.textMuted,
    fontSize: fontSize.caption,
    marginTop: 2,
  },
  badge: {
    marginLeft: spacing.sm,
    backgroundColor: colors.border,
    borderRadius: 999,
    paddingHorizontal: spacing.sm,
    paddingVertical: 2,
  },
  badgeText: {
    color: colors.textMuted,
    fontSize: 11,
    fontWeight: '700',
  },
});
