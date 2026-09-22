'use client';

import { createContext, useContext, useState, useCallback, useEffect, ReactNode } from 'react';
import { themes, type ThemeKey, type Theme } from '@/lib/themes';

interface ThemeContextValue {
  themeKey: ThemeKey;
  theme: Theme;
  setTheme: (key: ThemeKey) => void;
  isFun: boolean;
  reduceMotion: boolean;
  toggleReduceMotion: () => void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

export function ThemeProvider({ children }: { children: ReactNode }) {
  // Professional is the default look (CFO directive 2026-08-10). Anyone who has
  // NOT deliberately chosen another theme gets Professional. Explicit choices
  // (saved in localStorage) still win — see the mount effect below.
  const [themeKey, setThemeKey] = useState<ThemeKey>('professional');
  const [reduceMotion, setReduceMotion] = useState(false);

  const setTheme = useCallback((key: ThemeKey) => {
    setThemeKey(key);
    if (typeof window !== 'undefined') {
      localStorage.setItem('alpha_theme', key);
      // One CSS class per non-classic theme on <html>. 'light' = Classic = none.
      const cl = document.documentElement.classList;
      cl.toggle('theme-professional', key === 'professional');
      cl.toggle('theme-fun', key === 'fun');
      cl.toggle('theme-heavenly', key === 'heavenly');
    }
  }, []);

  const toggleReduceMotion = useCallback(() => {
    setReduceMotion(prev => {
      const next = !prev;
      if (typeof window !== 'undefined') {
        localStorage.setItem('alpha_reduce_motion', String(next));
      }
      return next;
    });
  }, []);

  // Load persisted preferences on mount
  useEffect(() => {
    const savedTheme = localStorage.getItem('alpha_theme') as ThemeKey | null;
    // No explicit choice → fall back to the default (Professional). An explicit
    // saved theme still wins.
    const effective: ThemeKey = (savedTheme && savedTheme in themes) ? savedTheme : 'professional';
    setThemeKey(effective);
    // The Omni staff PHONE app (/app) is brand-only — navy #0D1B2A / orange
    // #F4A623 — and never takes a desktop theme repaint: the theme-* CSS in
    // globals.css recolours those exact hexes (navy→teal, orange→slate).
    const onPhoneApp = /^\/app(\/|$)/.test(window.location.pathname);
    const cl = document.documentElement.classList;
    cl.toggle('theme-professional', !onPhoneApp && effective === 'professional');
    cl.toggle('theme-fun', !onPhoneApp && effective === 'fun');
    cl.toggle('theme-heavenly', !onPhoneApp && effective === 'heavenly');
    const savedMotion = localStorage.getItem('alpha_reduce_motion');
    if (savedMotion === 'true') {
      setReduceMotion(true);
    }
  }, []);

  return (
    <ThemeContext.Provider value={{
      themeKey,
      theme: themes[themeKey],
      setTheme,
      isFun: themeKey === 'fun',
      reduceMotion,
      toggleReduceMotion,
    }}>
      {children}
    </ThemeContext.Provider>
  );
}

export function useTheme() {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error('useTheme must be used within ThemeProvider');
  return ctx;
}
