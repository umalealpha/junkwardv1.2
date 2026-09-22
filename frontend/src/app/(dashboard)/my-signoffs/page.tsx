'use client';

import { useCallback, useEffect, useState } from 'react';
import { apiFetch } from '@/lib/api';
import { TopBar } from '@/components/layout/TopBar';
import { Button } from '@/components/ui/button';
import { CheckCircle2, Inbox, Loader2 } from 'lucide-react';

interface Ack {
  id: number;
  kind: string;
  title: string;
  due_date: string;
  employee_name?: string;
  employee_signed_at?: string | null;
  manager_signed_at?: string | null;
  needs_manager: boolean;
  overdue: boolean;
  document_url?: string | null;
  side?: string;
}

interface MySignoffsResponse {
  mine?: Ack[];
  to_countersign?: Ack[];
  rows?: Ack[];
}

const formatDate = (date?: string | null): string => {
  if (!date) return '—';
  const d = new Date(date);
  if (isNaN(d.getTime())) return date;
  return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
};

export default function MySignoffsPage() {
  const [mine, setMine] = useState<Ack[]>([]);
  const [toCountersign, setToCountersign] = useState<Ack[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [signingId, setSigningId] = useState<number | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  const tileClass = `border border-gray-200 bg-white rounded-lg shadow-sm p-4 transition-all duration-300 ${
    mounted ? 'motion-safe:opacity-100 motion-safe:translate-y-0' : 'motion-safe:opacity-0 motion-safe:translate-y-2'
  }`;

  const fetchSignoffs = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await apiFetch<MySignoffsResponse>('/hris/my-signoffs/');

      if (Array.isArray(data.rows)) {
        const rows = data.rows as Ack[];
        const hasSide = rows.some((r) => typeof r.side === 'string');
        if (hasSide) {
          setMine(rows.filter((r) => r.side === 'mine' || r.side === 'employee'));
          setToCountersign(rows.filter((r) => r.side === 'to_countersign' || r.side === 'manager'));
        } else {
          setMine(rows);
          setToCountersign([]);
        }
      } else {
        setMine(data.mine || []);
        setToCountersign(data.to_countersign || []);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load sign-offs.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchSignoffs();
  }, [fetchSignoffs]);

  const signItem = async (id: number, list: 'mine' | 'toCountersign') => {
    setSigningId(id);
    setError(null);
    setSuccessMessage(null);
    try {
      await apiFetch(`/hris/acknowledgements/${id}/sign/`, { method: 'POST' });
      setSuccessMessage('You signed it.');
      if (list === 'mine') {
        setMine((prev) => prev.filter((a) => a.id !== id));
      } else {
        setToCountersign((prev) => prev.filter((a) => a.id !== id));
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not sign.');
    } finally {
      setSigningId(null);
    }
  };

  const empty = mine.length === 0 && toCountersign.length === 0;

  return (
    <div className="min-h-screen pb-16">
      <TopBar title="My sign-offs" />

      <div className="px-4 sm:px-6 lg:px-8 py-6 max-w-4xl mx-auto space-y-8">
        {loading && (
          <div className="flex items-center justify-center py-16 text-gray-500">
            <Loader2 className="h-6 w-6 animate-spin mr-2" /> Loading sign-offs…
          </div>
        )}

        {error && !loading && (
          <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg p-4 text-sm">
            {error}
          </div>
        )}

        {successMessage && !loading && (
          <div className="bg-green-50 border border-green-200 text-green-700 rounded-lg p-4 text-sm">
            {successMessage}
          </div>
        )}

        {!loading && !error && empty && (
          <div className="flex flex-col items-center justify-center py-16 text-center text-gray-500">
            <Inbox className="h-10 w-10 text-gray-300 mb-3" />
            <p>Nothing to sign — thank you.</p>
          </div>
        )}

        {!loading && mine.length > 0 && (
          <section>
            <h2 className="text-[11px] uppercase tracking-[0.12em] text-gray-500 mb-3">
              Things to sign
            </h2>
            <div className="space-y-3">
              {mine.map((ack) => (
                <div
                  key={ack.id}
                  className={`${tileClass} flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4`}
                >
                  <div>
                    <p className="text-sm font-medium text-gray-900">{ack.title}</p>
                    <p className="text-xs text-gray-500 mt-1">
                      Due {formatDate(ack.due_date)}
                      {ack.overdue && (
                        <span className="ml-2 font-semibold text-red-600">Overdue</span>
                      )}
                    </p>
                    {ack.employee_name && (
                      <p className="text-xs text-gray-500 mt-0.5">{ack.employee_name}</p>
                    )}
                  </div>
                  <Button
                    type="button"
                    onClick={() => signItem(ack.id, 'mine')}
                    disabled={signingId === ack.id}
                    className="bg-ad-navy text-white hover:bg-ad-navy/90 whitespace-nowrap"
                  >
                    {signingId === ack.id ? (
                      <Loader2 className="h-4 w-4 animate-spin" />
                    ) : (
                      <CheckCircle2 className="h-4 w-4 mr-1" />
                    )}
                    I have read and agree — sign
                  </Button>
                </div>
              ))}
            </div>
          </section>
        )}

        {!loading && toCountersign.length > 0 && (
          <section>
            <h2 className="text-[11px] uppercase tracking-[0.12em] text-gray-500 mb-3">
              As a manager, countersign
            </h2>
            <div className="space-y-3">
              {toCountersign.map((ack) => (
                <div
                  key={ack.id}
                  className={`${tileClass} flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4`}
                >
                  <div>
                    <p className="text-sm font-medium text-gray-900">{ack.title}</p>
                    <p className="text-xs text-gray-500 mt-1">
                      {ack.employee_name ? `${ack.employee_name} · ` : ''}
                      Due {formatDate(ack.due_date)}
                    </p>
                    {ack.overdue && (
                      <p className="text-xs font-semibold text-red-600 mt-1">Overdue</p>
                    )}
                  </div>
                  <Button
                    type="button"
                    onClick={() => signItem(ack.id, 'toCountersign')}
                    disabled={signingId === ack.id}
                    className="bg-ad-orange text-ad-navy hover:bg-ad-orange/90 whitespace-nowrap"
                  >
                    {signingId === ack.id ? (
                      <Loader2 className="h-4 w-4 animate-spin" />
                    ) : (
                      'Countersign'
                    )}
                  </Button>
                </div>
              ))}
            </div>
          </section>
        )}
      </div>
    </div>
  );
}
