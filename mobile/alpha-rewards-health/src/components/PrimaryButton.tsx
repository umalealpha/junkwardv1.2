/**
 * PrimaryButton.tsx — orange pill primary action with disabled + busy states.
 */

import React from 'react';
import {
  ActivityIndicator,
  StyleSheet,
  Text,
  TouchableOpacity,
} from 'react-native';
import {colors, fontSize, radius, spacing} from '../theme';

type Props = {
  label: string;
  onPress: () => void;
  disabled?: boolean;
  busy?: boolean;
};

export default function PrimaryButton({
  label,
  onPress,
  disabled,
  busy,
}: Props): React.JSX.Element {
  const isDisabled = disabled || busy;
  return (
    <TouchableOpacity
      activeOpacity={0.85}
      onPress={onPress}
      disabled={isDisabled}
      style={[styles.button, isDisabled && styles.disabled]}>
      {busy ? (
        <ActivityIndicator color={colors.primaryText} />
      ) : (
        <Text style={styles.label}>{label}</Text>
      )}
    </TouchableOpacity>
  );
}

const styles = StyleSheet.create({
  button: {
    backgroundColor: colors.primary,
    borderRadius: radius.button,
    paddingVertical: spacing.md,
    paddingHorizontal: spacing.lg,
    alignItems: 'center',
    justifyContent: 'center',
    minHeight: 52,
  },
  disabled: {
    backgroundColor: colors.disabled,
  },
  label: {
    color: colors.primaryText,
    fontSize: fontSize.subtitle,
    fontWeight: '700',
  },
});
