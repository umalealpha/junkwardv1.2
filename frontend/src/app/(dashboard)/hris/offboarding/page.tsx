'use client';

import { useCallback, useEffect, useState } from 'react';
import { apiFetch } from '@/lib/api';
import { TopBar } from '@/components/layout/TopBar';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  Inbox,
  Loader2,
  Search,
  Upload,
  X,
} from 'lucide-react';

interface OffboardingEmployee {
  id: number;
  name: string;
  job_title?: string;
  company?: string;
  department?: string;
}

interface CaseStep {
  kind: string;
  label: string;
  done: boolean;
  done_by?: string | null;
  done_at?: string | null;
  note?: string | null;
  document_id?: string | null;
  document_title?: string | null;
}

interface OffboardingItemHeld {
  tag: string;
  description?: string;
}

interface OffboardingCase {
  id: number;
  employee?: OffboardingEmployee | null;
  employee_id?: number;
  employee_name?: string;
  employee_job_title?: string;
  employee_company?: string;
  employee_department?: string;
  reason: string;
  last_working_day: string;
  seniority?: string | null;
  seniority_label?: string | null;
  status: 'open' | 'complete' | 'cancelled';
  access_removed_at?: string | null;
  it_ticket_sent_at?: string | null;
  m365_disabled_at?: string | null;
  m365_note?: string;
  outstanding?: number;
  steps: CaseStep[];
  items_held: OffboardingItemHeld[];
}

interface OffboardingListResponse {
  rows: OffboardingCase[];
}

interface EmployeeSearchResponse {
  rows: OffboardingEmployee[];
}

interface ActionState {
  note: string;
  file: File | null;
}

const formatDate = (date?: string | null): string => {
  if (!date) return '—';
  const d = new Date(date);
  if (isNaN(d.getTime())) return date;
  return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
};

const daysUntil = (date?: string | null): number | null => {
  if (!date) return null;
  const d = new Date(date);
  if (isNaN(d.getTime())) return null;
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  d.setHours(0, 0, 0, 0);
  return Math.round((d.getTime() - today.getTime()) / (1000 * 60 * 60 * 24));
};

const statusPillClass = (status: string): string => {
  if (status === 'open') return 'bg-amber-50 text-amber-700 border-amber-200';
  if (status === 'complete') return 'bg-green-50 text-green-700 border-green-200';
  return 'bg-gray-50 text-gray-600 border-gray-200';
};

const isUploadStep = (step: CaseStep): boolean => {
  const k = step.kind.toLowerCase();
  return (
    ['upload', 'file', 'document', 'form', 'contract', 'certificate'].includes(k) ||
    k.includes('upload') ||
    k.includes('file') ||
    k.includes('document') ||
    k.includes('form')
  );
};

export default function OffboardingPage() {
  const [cases, setCases] = useState<OffboardingCase[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expandedCaseId, setExpandedCaseId] = useState<number | null>(null);
  const [mounted, setMounted] = useState(false);

  const [recordOpen, setRecordOpen] = useState(false);
  const [employeeSearch, setEmployeeSearch] = useState('');
  const [employeeResults, setEmployeeResults] = useState<OffboardingEmployee[]>([]);
  const [employeeLoading, setEmployeeLoading] = useState(false);
  const [employeeError, setEmployeeError] = useState<string | null>(null);
  const [selectedEmployee, setSelectedEmployee] = useState<OffboardingEmployee | null>(null);
  const [reason, setReason] = useState<'resignation' | 'end_of_contract' | 'dismissal' | 'other'>('resignation');
  const [lastWorkingDay, setLastWorkingDay] = useState('');
  const [recordSubmitting, setRecordSubmitting] = useState(false);
  const [recordMessage, setRecordMessage] = useState<string | null>(null);
  const [recordError, setRecordError] = useState<string | null>(null);

  const [actionStates, setActionStates] = useState<Record<string, ActionState>>({});
  const [submittingCaseId, setSubmittingCaseId] = useState<number | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitSuccess, setSubmitSuccess] = useState<string | null>(null);

  useEffect(() => {
    setMounted(true);
  }, []);

  const tileClass = `border border-gray-200 bg-white rounded-lg shadow-sm p-4 transition-all duration-300 ${
    mounted ? 'motion-safe:opacity-100 motion-safe:translate-y-0' : 'motion-safe:opacity-0 motion-safe:translate-y-2'
  }`;

  const fetchCases = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await apiFetch<OffboardingListResponse>('/hris/offboarding/');
      setCases(data.rows || []);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load leavers.');
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchCase = useCallback(async (id: number) => {
    const data = await apiFetch<OffboardingCase>(`/hris/offboarding/${id}/`);
    setCases((prev) => prev.map((c) => (c.id === id ? data : c)));
    return data;
  }, []);

  useEffect(() => {
    fetchCases();
  }, [fetchCases]);

  useEffect(() => {
    if (loading || cases.length === 0) return;
    const params = new URLSearchParams(window.location.search);
    const caseId = Number(params.get('case'));
    if (caseId) {
      setExpandedCaseId(caseId);
      setTimeout(() => {
        const el = document.getElementById(`case-${caseId}`);
        if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }, 150);
    } else {
      setExpandedCaseId((prev) => prev ?? cases[0].id);
    }
  }, [loading, cases]);

  useEffect(() => {
    if (!recordOpen || employeeSearch.trim().length === 0) {
      setEmployeeResults([]);
      return;
    }
    const timer = setTimeout(async () => {
      setEmployeeLoading(true);
      setEmployeeError(null);
      try {
        const data = await apiFetch<EmployeeSearchResponse>(
          `/hris/offboarding/employees/?q=${encodeURIComponent(employeeSearch.trim())}`
        );
        setEmployeeResults(data.rows || []);
      } catch (e) {
        setEmployeeError(e instanceof Error ? e.message : 'Could not search employees.');
        setEmployeeResults([]);
      } finally {
        setEmployeeLoading(false);
      }
    }, 350);
    return () => clearTimeout(timer);
  }, [employeeSearch, recordOpen]);

  const updateActionState = (caseId: number, index: number, patch: Partial<ActionState>) => {
    const key = `${caseId}-${index}`;
    setActionStates((prev) => {
      const old = prev[key] ?? { note: '', file: null };
      return { ...prev, [key]: { ...old, ...patch } };
    });
  };

  const submitRecord = async () => {
    if (!selectedEmployee || !lastWorkingDay) return;
    setRecordSubmitting(true);
    setRecordMessage(null);
    setRecordError(null);
    try {
      await apiFetch('/hris/offboarding/open/', {
        method: 'POST',
        body: JSON.stringify({
          employee_id: selectedEmployee.id,
          reason,
          last_working_day: lastWorkingDay,
        }),
      });
      setRecordMessage(`Leaver recorded for ${selectedEmployee.name}.`);
      setRecordOpen(false);
      setSelectedEmployee(null);
      setLastWorkingDay('');
      fetchCases();
    } catch (e) {
      setRecordError(e instanceof Error ? e.message : 'Could not record leaver.');
    } finally {
      setRecordSubmitting(false);
    }
  };

  const submitStep = async (caseId: number, step: CaseStep, index: number) => {
    const key = `${caseId}-${index}`;
    const state = actionStates[key] ?? { note: '', file: null };
    const upload = isUploadStep(step);
    if (upload && !state.file) {
      setSubmitError('Choose a file to upload.');
      return;
    }

    setSubmittingCaseId(caseId);
    setSubmitError(null);
    setSubmitSuccess(null);
    try {
      if (upload) {
        const fd = new FormData();
        fd.append('kind', step.kind);
        fd.append('note', state.note);
        if (state.file) fd.append('file', state.file);
        await apiFetch(`/hris/offboarding/${caseId}/step/`, { method: 'POST', body: fd });
      } else {
        await apiFetch(`/hris/offboarding/${caseId}/step/`, {
          method: 'POST',
          body: JSON.stringify({ kind: step.kind, note: state.note }),
        });
      }
      setSubmitSuccess('Step recorded.');
      await fetchCase(caseId);
      setActionStates((prev) => {
        const old = prev[key];
        if (!old) return prev;
        return { ...prev, [key]: { ...old, note: '', file: null } };
      });
    } catch (e) {
      setSubmitError(e instanceof Error ? e.message : 'Could not complete step.');
    } finally {
      setSubmittingCaseId(null);
    }
  };

  return (
    <div className="min-h-screen pb-16">
      <TopBar title="Leavers" />

      <div className="px-4 sm:px-6 lg:px-8 py-6 max-w-6xl mx-auto space-y-6">
        <div className="bg-[#0D1B2A] text-white rounded-xl p-5 shadow-sm flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
          <div>
            <p className="text-sm text-white">Omni will not archive anyone until every step below is done.</p>
          </div>
          <Button
            type="button"
            onClick={() => {
              setRecordOpen((prev) => !prev);
              setRecordMessage(null);
              setRecordError(null);
            }}
            className="bg-white text-[#0D1B2A] hover:bg-white/90"
          >
            Record a leaver
          </Button>
        </div>

        {recordOpen && (
          <Card className="border border-gray-200 shadow-sm">
            <CardHeader className="flex flex-row items-center justify-between pb-2">
              <CardTitle className="text-base">Record a leaver</CardTitle>
              <button
                type="button"
                onClick={() => setRecordOpen(false)}
                className="text-gray-400 hover:text-gray-700"
                aria-label="Close"
              >
                <X className="h-5 w-5" />
              </button>
            </CardHeader>
            <CardContent className="space-y-4">
              <div>
                <label className="text-[11px] uppercase tracking-[0.12em] text-gray-500">Employee</label>
                <div className="relative mt-1">
                  <Search className="absolute left-3 top-2.5 h-4 w-4 text-gray-400" />
                  <input
                    type="text"
                    value={selectedEmployee ? selectedEmployee.name : employeeSearch}
                    onChange={(e) => {
                      setSelectedEmployee(null);
                      setEmployeeSearch(e.target.value);
                    }}
                    placeholder="Search by name"
                    className="w-full border border-gray-200 rounded-lg pl-9 pr-3 py-2 text-sm shadow-sm focus:outline-none focus:ring-2 focus:ring-ad-orange"
                  />
                </div>
                {employeeLoading && (
                  <p className="mt-2 flex items-center gap-2 text-sm text-gray-500">
                    <Loader2 className="h-4 w-4 animate-spin" /> Searching…
                  </p>
                )}
                {employeeError && (
                  <p className="mt-2 text-sm text-red-600">{employeeError}</p>
                )}
                {!selectedEmployee && employeeResults.length > 0 && (
                  <div className="mt-2 border border-gray-200 rounded-lg overflow-hidden max-h-64 overflow-y-auto">
                    {employeeResults.map((emp) => (
                      <button
                        key={emp.id}
                        type="button"
                        onClick={() => {
                          setSelectedEmployee(emp);
                          setEmployeeSearch(emp.name);
                          setEmployeeResults([]);
                        }}
                        className="w-full text-left px-3 py-2 hover:bg-gray-50 flex items-center justify-between gap-2"
                      >
                        <span className="font-medium text-gray-900">{emp.name}</span>
                        <span className="text-xs text-gray-500">
                          {emp.company}
                          {emp.department ? ` · ${emp.department}` : ''}
                        </span>
                      </button>
                    ))}
                  </div>
                )}
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div>
                  <label className="text-[11px] uppercase tracking-[0.12em] text-gray-500">Reason</label>
                  <select
                    value={reason}
                    onChange={(e) =>
                      setReason(e.target.value as 'resignation' | 'end_of_contract' | 'dismissal' | 'other')
                    }
                    className="mt-1 w-full border border-gray-200 rounded-lg px-3 py-2 text-sm shadow-sm focus:outline-none focus:ring-2 focus:ring-ad-orange"
                  >
                    <option value="resignation">Resignation</option>
                    <option value="end_of_contract">End of contract</option>
                    <option value="dismissal">Dismissal</option>
                    <option value="other">Other</option>
                  </select>
                </div>
                <div>
                  <label className="text-[11px] uppercase tracking-[0.12em] text-gray-500">Last working day</label>
                  <input
                    type="date"
                    value={lastWorkingDay}
                    onChange={(e) => setLastWorkingDay(e.target.value)}
                    className="mt-1 w-full border border-gray-200 rounded-lg px-3 py-2 text-sm shadow-sm focus:outline-none focus:ring-2 focus:ring-ad-orange"
                  />
                </div>
              </div>

              {recordMessage && <p className="text-sm text-green-700">{recordMessage}</p>}
              {recordError && <p className="text-sm text-red-600">{recordError}</p>}

              <div className="flex justify-end gap-3">
                <Button type="button" variant="outline" onClick={() => setRecordOpen(false)}>
                  Cancel
                </Button>
                <Button
                  type="button"
                  onClick={submitRecord}
                  disabled={
                    recordSubmitting || !selectedEmployee || !lastWorkingDay
                  }
                  className="bg-ad-navy text-white hover:bg-ad-navy/90"
                >
                  {recordSubmitting ? <Loader2 className="h-4 w-4 animate-spin" /> : 'Record leaver'}
                </Button>
              </div>
            </CardContent>
          </Card>
        )}

        {loading && (
          <div className="flex items-center justify-center py-16 text-gray-500">
            <Loader2 className="h-6 w-6 animate-spin mr-2" /> Loading leavers…
          </div>
        )}

        {error && !loading && (
          <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg p-4 text-sm">
            {error}
          </div>
        )}

        {!loading && !error && cases.length === 0 && (
          <div className="flex flex-col items-center justify-center py-16 text-center text-gray-500">
            <Inbox className="h-10 w-10 text-gray-300 mb-3" />
            <p>No leavers yet.</p>
          </div>
        )}

        <div className="space-y-4">
          {cases.map((c) => {
            const empName = c.employee?.name ?? c.employee_name ?? 'Unknown';
            const empJob = c.employee?.job_title ?? c.employee_job_title ?? '';
            const empCompany = c.employee?.company ?? c.employee_company ?? '';
            const empDepartment = c.employee?.department ?? c.employee_department ?? '';
            const seniorityLabel = c.seniority_label ?? c.seniority ?? '';
            const days = daysUntil(c.last_working_day);
            const doneCount = c.steps.filter((s) => s.done).length;
            const totalSteps = c.steps.length || 6;
            const progress = totalSteps > 0 ? Math.round((doneCount / totalSteps) * 100) : 0;
            const expanded = expandedCaseId === c.id;

            return (
              <div key={c.id} id={`case-${c.id}`} className={tileClass}>
                <button
                  type="button"
                  onClick={() => setExpandedCaseId(expanded ? null : c.id)}
                  className="w-full text-left flex items-start justify-between gap-4"
                >
                  <div>
                    <div className="flex items-center gap-2 flex-wrap">
                      <h2 className="text-base font-semibold text-gray-900">{empName}</h2>
                      <span className={`text-xs border px-2 py-0.5 rounded-full ${statusPillClass(c.status)}`}>
                        {c.status}
                      </span>
                    </div>
                    <p className="text-sm text-gray-600 mt-0.5">{empJob}</p>
                    <p className="text-xs text-gray-500 mt-1">
                      {empCompany}
                      {empDepartment ? ` · ${empDepartment}` : ''}
                    </p>
                  </div>
                  <ChevronDown className={`h-5 w-5 text-gray-400 transition-transform ${expanded ? 'rotate-180' : ''}`} />
                </button>

                <div className="mt-4 flex flex-wrap items-center gap-3 text-sm">
                  <span className="text-gray-600">
                    Last day: <span className="font-medium">{formatDate(c.last_working_day)}</span>
                    {days !== null && (
                      <span className={`ml-1 text-xs font-semibold ${days <= 30 ? 'text-red-600' : 'text-gray-500'}`}>
                        {days === 0 ? 'today' : days > 0 ? `in ${days} days` : `${Math.abs(days)} days ago`}
                      </span>
                    )}
                  </span>
                  {seniorityLabel && (
                    <span className="text-xs border border-gray-200 bg-gray-50 text-gray-700 rounded-full px-2 py-0.5">
                      {seniorityLabel}
                    </span>
                  )}
                  <span className="text-xs text-gray-500">
                    {doneCount}/{totalSteps} steps done
                  </span>
                </div>

                <div className="mt-2 h-1.5 bg-gray-100 rounded-full overflow-hidden">
                  <div
                    className={`h-full transition-all duration-300 ${progress === 100 ? 'bg-green-500' : 'bg-ad-orange'}`}
                    style={{ width: `${progress}%` }}
                  />
                </div>

                {expanded && (
                  <div className="mt-5 space-y-4 border-t border-gray-100 pt-4">
                    {c.items_held.length > 0 && (
                      <div className="bg-amber-50 border border-amber-200 rounded-lg p-3">
                        <p className="text-xs font-semibold text-amber-800 flex items-center gap-2">
                          <AlertTriangle className="h-4 w-4" />
                          Items still held
                        </p>
                        <div className="mt-2 flex flex-wrap gap-2">
                          {c.items_held.map((item, idx) => (
                            <span
                              key={`${item.tag}-${idx}`}
                              className="text-xs bg-white border border-amber-200 text-amber-800 rounded-full px-2 py-1"
                            >
                              {item.tag}
                              {item.description ? ` · ${item.description}` : ''}
                            </span>
                          ))}
                        </div>
                        <p className="text-xs text-amber-700 mt-2">
                          Return through Assets before the last day.
                        </p>
                      </div>
                    )}

                    <div className="space-y-3">
                      {c.steps.map((step, idx) => {
                        const stepKey = `${c.id}-${idx}`;
                        const action = actionStates[stepKey] ?? { note: '', file: null };
                        const isUpload = isUploadStep(step);
                        const isSubmitting = submittingCaseId === c.id;

                        if (step.done) {
                          return (
                            <div key={stepKey} className="flex items-start gap-3 rounded-lg border border-green-200 bg-green-50 p-3">
                              <CheckCircle2 className="h-5 w-5 text-green-600 mt-0.5" />
                              <div className="text-sm">
                                <p className="font-medium text-gray-900 flex items-center gap-2">
                                  {step.label}
                                </p>
                                <p className="text-xs text-gray-500 mt-0.5">
                                  {step.done_by || 'Completed'} · {formatDate(step.done_at)}
                                </p>
                                {step.document_title && (
                                  <p className="text-xs text-gray-500 mt-0.5">
                                    Document: <span className="font-medium">{step.document_title}</span>
                                  </p>
                                )}
                                {step.note && <p className="text-xs text-gray-600 mt-1">{step.note}</p>}
                              </div>
                            </div>
                          );
                        }

                        return (
                          <div key={stepKey} className="rounded-lg border border-gray-200 p-3 space-y-3">
                            <div className="flex items-center gap-2">
                              {isUpload ? (
                                <Upload className="h-4 w-4 text-gray-500" />
                              ) : (
                                <CheckCircle2 className="h-4 w-4 text-gray-500" />
                              )}
                              <p className="text-sm font-medium text-gray-900">{step.label}</p>
                            </div>

                            <textarea
                              value={action.note}
                              onChange={(e) => updateActionState(c.id, idx, { note: e.target.value })}
                              placeholder="Add a note (optional)"
                              className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm shadow-sm focus:outline-none focus:ring-2 focus:ring-ad-orange"
                              rows={2}
                            />

                            {isUpload && (
                              <input
                                type="file"
                                onChange={(e) => updateActionState(c.id, idx, { file: e.target.files?.[0] ?? null })}
                                className="block w-full text-sm text-gray-500 file:mr-3 file:rounded-md file:border-0 file:bg-gray-100 file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-gray-700 hover:file:bg-gray-200"
                              />
                            )}

                            <div className="flex justify-end">
                              <Button
                                type="button"
                                onClick={() => submitStep(c.id, step, idx)}
                                disabled={isSubmitting}
                                className="bg-ad-navy text-white hover:bg-ad-navy/90"
                              >
                                {isSubmitting ? (
                                  <Loader2 className="h-4 w-4 animate-spin" />
                                ) : isUpload ? (
                                  'Upload'
                                ) : (
                                  'Sign off'
                                )}
                              </Button>
                            </div>
                          </div>
                        );
                      })}

                      {c.status === 'open' && (
                        <div className="text-xs text-gray-500">
                          {c.access_removed_at ? (
                            <p>System access closed now (email kept).</p>
                          ) : (
                            <p>IT has been emailed.</p>
                          )}
                        </div>
                      )}

                      <p className="text-xs text-gray-500">
                        {c.m365_disabled_at
                          ? `Microsoft 365 switched off ${new Date(c.m365_disabled_at).toLocaleDateString('en-GB')}. To undo, IT turns it back on in the Microsoft admin centre.`
                          : c.m365_note
                            ? `Microsoft 365 still on: ${c.m365_note}.`
                            : 'Microsoft 365 (email, Teams) goes off the day after the last working day.'}
                      </p>
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>

        {submitError && (
          <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg p-4 text-sm">
            {submitError}
          </div>
        )}
        {submitSuccess && (
          <div className="bg-green-50 border border-green-200 text-green-700 rounded-lg p-4 text-sm">
            {submitSuccess}
          </div>
        )}
      </div>
    </div>
  );
}
