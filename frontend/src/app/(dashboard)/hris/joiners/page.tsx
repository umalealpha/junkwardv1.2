'use client';

import { useCallback, useEffect, useState } from 'react';
import { apiFetch } from '@/lib/api';
import { TopBar } from '@/components/layout/TopBar';
import { Button } from '@/components/ui/button';
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  Inbox,
  Loader2,
  PackageCheck,
  XCircle,
  MinusCircle,
} from 'lucide-react';

interface JoinerAck {
  id: number;
  kind: string;
  title: string;
  due_date: string;
  employee_signed_at?: string | null;
  manager_signed_at?: string | null;
  needs_manager: boolean;
  overdue: boolean;
}

interface JoinerDocument {
  category: string;
  label: string;
  present: boolean;
}

interface JoinerItemHeld {
  tag: string;
  description?: string;
}

interface Joiner {
  id: number;
  name: string;
  job_title: string;
  department: string;
  company: string;
  hire_date: string;
  manager?: string | null;
  tasks: { done: number; total: number };
  acks: JoinerAck[];
  documents: JoinerDocument[];
  items_held: JoinerItemHeld[];
  systems: string[];
}

interface JoinerListResponse {
  rows: Joiner[];
}

const formatDate = (date?: string | null): string => {
  if (!date) return '—';
  const d = new Date(date);
  if (isNaN(d.getTime())) return date;
  return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
};

const ProgressChip = ({
  label,
  done,
  total,
}: {
  label: string;
  done: number;
  total: number;
}) => {
  const complete = total > 0 && done >= total;
  return (
    <span
      className={`text-xs border rounded-full px-2.5 py-1 ${
        complete
          ? 'bg-green-50 border-green-200 text-green-700'
          : 'bg-amber-50 border-amber-200 text-amber-700'
      }`}
    >
      {label} {done}/{total}
    </span>
  );
};

export default function JoinersPage() {
  const [joiners, setJoiners] = useState<Joiner[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [startingId, setStartingId] = useState<number | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  const tileClass = `border border-gray-200 bg-white rounded-lg shadow-sm p-4 transition-all duration-300 ${
    mounted ? 'motion-safe:opacity-100 motion-safe:translate-y-0' : 'motion-safe:opacity-0 motion-safe:translate-y-2'
  }`;

  const fetchJoiners = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await apiFetch<JoinerListResponse>('/hris/joiners/');
      setJoiners(data.rows || []);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load new joiners.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchJoiners();
  }, [fetchJoiners]);

  const startJoinerPack = async (joinerId: number) => {
    setStartingId(joinerId);
    setMessage(null);
    setError(null);
    try {
      const updated = await apiFetch<Joiner>(`/hris/joiners/${joinerId}/start/`, { method: 'POST' });
      setJoiners((prev) => prev.map((j) => (j.id === joinerId ? updated : j)));
      setMessage('Joiner pack started.');
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not start joiner pack.');
    } finally {
      setStartingId(null);
    }
  };

  return (
    <div className="min-h-screen pb-16">
      <TopBar title="New joiners" />

      <div className="px-4 sm:px-6 lg:px-8 py-6 max-w-6xl mx-auto space-y-6">
        {loading && (
          <div className="flex items-center justify-center py-16 text-gray-500">
            <Loader2 className="h-6 w-6 animate-spin mr-2" /> Loading joiners…
          </div>
        )}

        {error && !loading && (
          <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg p-4 text-sm">
            {error}
          </div>
        )}

        {message && !loading && (
          <div className="bg-green-50 border border-green-200 text-green-700 rounded-lg p-4 text-sm">
            {message}
          </div>
        )}

        {!loading && !error && joiners.length === 0 && (
          <div className="flex flex-col items-center justify-center py-16 text-center text-gray-500">
            <Inbox className="h-10 w-10 text-gray-300 mb-3" />
            <p>No new joiners yet.</p>
          </div>
        )}

        <div className="space-y-4">
          {joiners.map((joiner) => {
            const expanded = expandedId === joiner.id;
            const ackDone = joiner.acks.filter(
              (a) => a.employee_signed_at && (!a.needs_manager || a.manager_signed_at)
            ).length;
            const ackTotal = joiner.acks.length;
            const docDone = joiner.documents.filter((d) => d.present).length;
            const docTotal = joiner.documents.length;

            return (
              <div key={joiner.id} className={tileClass}>
                <button
                  type="button"
                  onClick={() => setExpandedId(expanded ? null : joiner.id)}
                  className="w-full text-left flex items-start justify-between gap-4"
                >
                  <div>
                    <h2 className="text-base font-semibold text-gray-900">{joiner.name}</h2>
                    <p className="text-sm text-gray-600 mt-0.5">{joiner.job_title}</p>
                    <p className="text-xs text-gray-500 mt-1">
                      Start date: {formatDate(joiner.hire_date)}
                      {joiner.manager ? ` · Manager: ${joiner.manager}` : ''}
                    </p>
                    <div className="mt-2 flex flex-wrap gap-2">
                      <ProgressChip
                        label="Checklist"
                        done={joiner.tasks?.done ?? 0}
                        total={joiner.tasks?.total ?? 0}
                      />
                      <ProgressChip label="Sign-offs" done={ackDone} total={ackTotal} />
                      <ProgressChip label="Documents" done={docDone} total={docTotal} />
                    </div>
                  </div>
                  <ChevronDown className={`h-5 w-5 text-gray-400 transition-transform ${expanded ? 'rotate-180' : ''}`} />
                </button>

                {expanded && (
                  <div className="mt-5 border-t border-gray-100 pt-4 space-y-5">
                    {joiner.acks.length > 0 && (
                      <div>
                        <p className="text-[11px] uppercase tracking-[0.12em] text-gray-500 mb-2">
                          Sign-offs
                        </p>
                        <div className="space-y-2">
                          {joiner.acks.map((ack) => (
                            <div
                              key={ack.id}
                              className={`rounded-lg border p-3 ${
                                ack.overdue ? 'border-red-200 bg-red-50' : 'border-gray-200'
                              }`}
                            >
                              <div className="flex items-start justify-between gap-3">
                                <div>
                                  <p className="text-sm font-medium text-gray-900">{ack.title}</p>
                                  <p className="text-xs text-gray-500 mt-0.5">
                                    Due {formatDate(ack.due_date)}
                                    {ack.overdue && (
                                      <span className="ml-2 font-semibold text-red-600">Overdue</span>
                                    )}
                                  </p>
                                </div>
                                <div className="flex items-center gap-3 text-xs">
                                  <span className="flex items-center gap-1">
                                    Employee{' '}
                                    {ack.employee_signed_at ? (
                                      <CheckCircle2 className="h-4 w-4 text-green-600" />
                                    ) : (
                                      <XCircle className="h-4 w-4 text-red-500" />
                                    )}
                                  </span>
                                  <span className="flex items-center gap-1">
                                    Manager{' '}
                                    {ack.needs_manager ? (
                                      ack.manager_signed_at ? (
                                        <CheckCircle2 className="h-4 w-4 text-green-600" />
                                      ) : (
                                        <XCircle className="h-4 w-4 text-red-500" />
                                      )
                                    ) : (
                                      <MinusCircle className="h-4 w-4 text-gray-400" />
                                    )}
                                  </span>
                                </div>
                              </div>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}

                    {joiner.documents.filter((d) => !d.present).length > 0 && (
                      <div className="bg-red-50 border border-red-200 rounded-lg p-3">
                        <p className="text-xs font-semibold text-red-800 flex items-center gap-2">
                          <AlertTriangle className="h-4 w-4" />
                          Missing documents
                        </p>
                        <ul className="mt-2 space-y-1">
                          {joiner.documents
                            .filter((d) => !d.present)
                            .map((doc, idx) => (
                              <li key={`${doc.category}-${idx}`} className="text-xs text-red-700">
                                {doc.label}
                              </li>
                            ))}
                        </ul>
                      </div>
                    )}

                    {joiner.documents.filter((d) => d.present).length > 0 && (
                      <div className="bg-green-50 border border-green-200 rounded-lg p-3">
                        <p className="text-xs font-semibold text-green-800 flex items-center gap-2">
                          <CheckCircle2 className="h-4 w-4" />
                          Documents received
                        </p>
                        <div className="mt-2 flex flex-wrap gap-2">
                          {joiner.documents
                            .filter((d) => d.present)
                            .map((doc, idx) => (
                              <span
                                key={`${doc.category}-${idx}`}
                                className="text-xs bg-white border border-green-200 text-green-800 rounded-full px-2 py-1"
                              >
                                {doc.label}
                              </span>
                            ))}
                        </div>
                      </div>
                    )}

                    {joiner.items_held.length > 0 && (
                      <div className="bg-amber-50 border border-amber-200 rounded-lg p-3">
                        <p className="text-xs font-semibold text-amber-800 flex items-center gap-2">
                          <PackageCheck className="h-4 w-4" />
                          Items held
                        </p>
                        <div className="mt-2 flex flex-wrap gap-2">
                          {joiner.items_held.map((item, idx) => (
                            <span
                              key={`${item.tag}-${idx}`}
                              className="text-xs bg-white border border-amber-200 text-amber-800 rounded-full px-2 py-1"
                            >
                              {item.tag}
                              {item.description ? ` · ${item.description}` : ''}
                            </span>
                          ))}
                        </div>
                      </div>
                    )}

                    {joiner.systems.length > 0 && (
                      <div>
                        <p className="text-[11px] uppercase tracking-[0.12em] text-gray-500 mb-2">
                          IT was asked to set up
                        </p>
                        <div className="flex flex-wrap gap-2">
                          {joiner.systems.map((system, idx) => (
                            <span
                              key={`${system}-${idx}`}
                              className="text-xs bg-gray-50 border border-gray-200 text-gray-700 rounded-full px-2 py-1"
                            >
                              {system}
                            </span>
                          ))}
                        </div>
                      </div>
                    )}

                    {joiner.acks.length === 0 && (
                      <div className="flex justify-end">
                        <Button
                          type="button"
                          onClick={() => startJoinerPack(joiner.id)}
                          disabled={startingId === joiner.id}
                          className="bg-ad-navy text-white hover:bg-ad-navy/90"
                        >
                          {startingId === joiner.id ? (
                            <Loader2 className="h-4 w-4 animate-spin" />
                          ) : (
                            'Start joiner pack'
                          )}
                        </Button>
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
