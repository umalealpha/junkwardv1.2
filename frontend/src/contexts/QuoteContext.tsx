'use client';

import { createContext, useContext, useState, useEffect, useCallback, ReactNode } from 'react';
import { getQuote } from '@/lib/quotes';

interface QuoteContextValue {
  quote: string;
  refreshQuote: (page: string) => void;
}

const QuoteContext = createContext<QuoteContextValue | null>(null);

export function QuoteProvider({ children }: { children: ReactNode }) {
  // Picking the quote during SSR (or in useState's lazy initialiser) causes a
  // hydration mismatch because Math.random differs between server and client.
  const [quote, setQuote] = useState<string>('');

  useEffect(() => {
    setQuote(getQuote('default'));
  }, []);

  const refreshQuote = useCallback((page: string) => {
    setQuote(getQuote(page));
  }, []);

  return (
    <QuoteContext.Provider value={{ quote, refreshQuote }}>
      {children}
    </QuoteContext.Provider>
  );
}

export function useQuote() {
  const ctx = useContext(QuoteContext);
  if (!ctx) throw new Error('useQuote must be used within QuoteProvider');
  return ctx;
}
