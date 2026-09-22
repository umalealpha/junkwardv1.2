'use client';

import { useCallback, useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import { TopBar } from '@/components/layout/TopBar';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { apiFetch, apiFetchBinary } from '@/lib/api';
import {
  AlertTriangle,
  CheckCircle2,
  ChevronUp,
  Download,
  FileText,
  Loader2,
  Send,
  UserPlus,
  XCircle,
} from 'lucide-react';

type OfferStatus = 'draft' | 'sent' | 'accepted' | 'declined';

interface Offer {
  id: number;
  status: OfferStatus;
  candidate_email?: string | null;
  start_date?: string | null;
  decided_at?: string | null;
  onboarding_request_id?: number | null;
}

interface AuthorityRow {
  authority_id: number;
  reference: string;
  person_name: string;
  position: string;
  department: string;
  entity: string;
  effective_date: string;
  offer: Offer | null;
}

type OffersPayload = { rows?: AuthorityRow[] } | AuthorityRow[];

interface ApiActionResponse {
  onboarding_error?: string | null;
  message?: string;
}

const statusStyles: Record<string, string> = {
  draft: 'bg-slate-100 text-slate-700 border-slate-300',
  sent: 'bg-blue-50 text-blue-700 border-blue-200',
  accepted: 'bg-emerald-50 text-emerald-700 border-emerald-200',
  declined: 'bg-red-50 text-red-700 border-red-200',
};

const statusLabels: Record<string, string> = {
  draft: 'Draft',
  sent: 'Sent',
  accepted: 'Accepted',
  declined: 'Declined',
};

function formatDate(value?: string | null): string {
  if (!value) return '—';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleDateString('en-BW', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
  });
}

function getErrorMessage(e: unknown): string {
  return e instanceof Error ? e.message : 'Something went wrong';
}

function StatusPill({ status }: { status: OfferStatus | null }) {
  if (!status) {
    return (
      <span className="inline-flex items-center rounded-full border border-gray-200 bg-gray-50 px-2.5 py-1 text-[11px] font-medium uppercase tracking-[0.12em] text-gray-500">
        No offer
      </span>
    );
  }

  const label = statusLabels[status] ?? status;
  const cls = statusStyles[status] ?? statusStyles.draft;

  return (
    <span
      className={`inline-flex items-center rounded-full border px-2.5 py-1 text-[11px] font-medium uppercase tracking-[0.12em] ${cls}`}
    >
      {label}
    </span>
  );
}

export default function RecruitmentOffersPage() {
  const [rows, setRows] = useState<AuthorityRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [noticeType, setNoticeType] = useState<'success' | 'error' | 'amber'>('success');
  const [mounted, setMounted] = useState(false);

  const [draftAuthorityId, setDraftAuthorityId] = useState<number | null>(null);
  const [acceptAuthorityId, setAcceptAuthorityId] = useState<number | null>(null);
  const [draftForm, setDraftForm] = useState({ email: '', startDate: '' });
  const [acceptForm, setAcceptForm] = useState({ email: '', startDate: '' });
  const [actionLoading, setActionLoading] = useState<{ authorityId: number; action: string } | null>(null);
  const [downloadLoading, setDownloadLoading] = useState<number | null>(null);
  const [onboardingErrors, setOnboardingErrors] = useState<Record<number, string>>({});

  useEffect(() => {
    setMounted(true);
  }, []);

  const loadData = useCallback(async () => {
    setLoading(true);
    setError(null);

    try {
      const data = await apiFetch<OffersPayload>('/recruitment/offers/');
      const list = Array.isArray(data) ? data : (data.rows ?? []);
      setRows(list);
    } catch (e) {
      setError(getErrorMessage(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadData();
  }, [loadData]);

  const showNotice = (message: string, type: 'success' | 'error' | 'amber' = 'success') => {
    setNotice(message);
    setNoticeType(type);
  };

  const handleDraft = async (e: FormEvent<HTMLFormElement>, row: AuthorityRow) => {
    e.preventDefault();
    setActionLoading({ authorityId: row.authority_id, action: 'draft' });
    setNotice(null);

    try {
      await apiFetch<ApiActionResponse>(`/recruitment/offers/${row.authority_id}/draft/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          candidate_email: draftForm.email || null,
          start_date: draftForm.startDate || null,
        }),
      });

      setDraftAuthorityId(null);
      setDraftForm({ email: '', startDate: '' });
      showNotice(`Offer letter drafted for ${row.person_name}.`);
      await loadData();
    } catch (e) {
      showNotice(getErrorMessage(e), 'error');
    } finally {
      setActionLoading(null);
    }
  };

  const handleDownload = async (row: AuthorityRow) => {
    setDownloadLoading(row.authority_id);
    setNotice(null);

    try {
      const res = await apiFetchBinary(`/api/v1/recruitment/offers/${row.authority_id}/letter/`);
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = `offer-${row.reference}.docx`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      showNotice(getErrorMessage(e), 'error');
    } finally {
      setDownloadLoading(null);
    }
  };

  const handleStatus = async (
    row: AuthorityRow,
    status: 'sent' | 'accepted' | 'declined',
    email?: string,
    startDate?: string,
  ) => {
    setActionLoading({ authorityId: row.authority_id, action: status });
    setNotice(null);

    try {
      const payload: Record<string, unknown> = { status };
      if (email) payload.candidate_email = email;
      if (startDate) payload.start_date = startDate;

      const response = await apiFetch<ApiActionResponse>(`/recruitment/offers/${row.authority_id}/status/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });

      if (status === 'accepted' && response?.onboarding_error) {
        setOnboardingErrors((prev) => ({
          ...prev,
          [row.authority_id]: response.onboarding_error as string,
        }));
      } else if (status === 'accepted') {
        setOnboardingErrors((prev) => {
          const next = { ...prev };
          delete next[row.authority_id];
          return next;
        });
      }

      setAcceptAuthorityId(null);
      setAcceptForm({ email: '', startDate: '' });

      showNotice(
        status === 'sent'
          ? `Offer letter marked as sent for ${row.person_name}.`
          : status === 'accepted'
            ? `Offer accepted for ${row.person_name}.`
            : `Offer marked as declined for ${row.person_name}.`,
      );

      await loadData();
    } catch (e) {
      showNotice(getErrorMessage(e), 'error');
    } finally {
      setActionLoading(null);
    }
  };

  const renderActions = (row: AuthorityRow) => {
    const offer = row.offer;
    const isLoading = actionLoading?.authorityId === row.authority_id;
    const isDownloading = downloadLoading === row.authority_id;

    if (!offer) {
      return (
        <div className="flex flex-col gap-2">
          <Button
            type="button"
            variant="outline"
            disabled={actionLoading !== null}
            onClick={() => {
              setDraftAuthorityId((prev) => (prev === row.authority_id ? null : row.authority_id));
              setAcceptAuthorityId(null);
              setDraftForm({ email: '', startDate: '' });
            }}
          >
            {draftAuthorityId === row.authority_id ? (
              <ChevronUp className="mr-2 h-4 w-4" />
            ) : (
              <FileText className="mr-2 h-4 w-4" />
            )}
            Draft offer letter
          </Button>
        </div>
      );
    }

    if (offer.status === 'draft') {
      return (
        <div className="flex flex-col gap-2">
          <Button
            type="button"
            variant="outline"
            disabled={downloadLoading !== null}
            onClick={() => void handleDownload(row)}
          >
            {isDownloading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Download className="mr-2 h-4 w-4" />}
            Download letter
          </Button>
          <Button
            type="button"
            disabled={isLoading}
            onClick={() =>
              void handleStatus(row, 'sent', offer.candidate_email ?? undefined, offer.start_date ?? undefined)
            }
          >
            {isLoading && actionLoading?.action === 'sent' ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : (
              <Send className="mr-2 h-4 w-4" />
            )}
            Mark as sent
          </Button>
        </div>
      );
    }

    if (offer.status === 'sent') {
      return (
        <div className="flex flex-col gap-2">
          <Button
            type="button"
            variant="outline"
            disabled={isLoading}
            onClick={() => {
              setAcceptAuthorityId((prev) => (prev === row.authority_id ? null : row.authority_id));
              setDraftAuthorityId(null);
              setAcceptForm({
                email: offer.candidate_email ?? '',
                startDate: offer.start_date ?? '',
              });
            }}
          >
            <CheckCircle2 className="mr-2 h-4 w-4" />
            Accepted
          </Button>
          <Button
            type="button"
            variant="ghost"
            disabled={isLoading}
            onClick={() =>
              void handleStatus(row, 'declined', offer.candidate_email ?? undefined, offer.start_date ?? undefined)
            }
          >
            {isLoading && actionLoading?.action === 'declined' ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : (
              <XCircle className="mr-2 h-4 w-4" />
            )}
            Declined
          </Button>
        </div>
      );
    }

    if (offer.status === 'accepted') {
      return (
        <div className="flex flex-col gap-2">
          <a
            href="/hris/onboard"
            className="inline-flex items-center justify-center rounded-md bg-[#0D1B2A] px-4 py-2 text-sm font-medium text-white shadow-sm transition-colors hover:bg-[#1B2A3B]"
          >
            <UserPlus className="mr-2 h-4 w-4" />
            Approve on Onboard Employee
          </a>
          {offer.onboarding_request_id ? (
            <p className="text-xs text-gray-500">Onboarding request #{offer.onboarding_request_id}</p>
          ) : (
            <p className="text-xs text-gray-500">Onboarding request pending</p>
          )}
        </div>
      );
    }

    return null;
  };

  return (
    <div className="min-h-screen bg-gray-50">
      <TopBar title="Offer letters" />
      <div className="mx-auto max-w-6xl px-4 py-6 sm:px-6 lg:px-8">
        <div className="mb-6">
          <p className="text-[11px] uppercase tracking-[0.12em] text-gray-500">
            Approved Authorities to Recruit → offer → onboarding
          </p>
          <h1 className="mt-2 text-2xl font-bold text-[#0D1B2A] sm:text-3xl">Offer letters</h1>
          <p className="mt-2 text-sm text-gray-500">
            Draft, send, and record decisions for candidate offer letters in one place.
          </p>
        </div>

        {notice ? (
          <div
            className={`mb-4 rounded-md border px-4 py-3 text-sm ${
              noticeType === 'error'
                ? 'border-red-200 bg-red-50 text-red-700'
                : noticeType === 'amber'
                  ? 'border-amber-200 bg-amber-50 text-amber-800'
                  : 'border-emerald-200 bg-emerald-50 text-emerald-700'
            }`}
          >
            {notice}
          </div>
        ) : null}

        {loading ? (
          <div className="flex items-center justify-center rounded-xl border border-gray-200 bg-white py-20 text-gray-500">
            <Loader2 className="mr-2 h-5 w-5 animate-spin" />
            Loading offer letters…
          </div>
        ) : error ? (
          <div className="flex flex-col items-center justify-center rounded-xl border border-red-200 bg-red-50 px-4 py-16 text-center">
            <AlertTriangle className="mb-3 h-6 w-6 text-red-500" />
            <p className="text-sm font-medium text-red-700">{error}</p>
            <Button type="button" variant="outline" className="mt-4" onClick={() => void loadData()}>
              Retry
            </Button>
          </div>
        ) : rows.length === 0 ? (
          <div className="flex flex-col items-center justify-center rounded-xl border border-gray-200 bg-white px-4 py-16 text-center">
            <FileText className="mb-3 h-6 w-6 text-gray-300" />
            <p className="text-sm font-medium text-gray-700">No approved authorities to recruit</p>
            <p className="mt-1 text-xs text-gray-500">
              When an authority is approved, its offer letter can be drafted here.
            </p>
          </div>
        ) : (
          <div className="space-y-4">
            {rows.map((row) => {
              const cardAnimation = `motion-safe:transition-all motion-safe:duration-500 ease-out ${
                mounted
                  ? 'motion-safe:opacity-100 motion-safe:translate-y-0'
                  : 'motion-safe:opacity-0 motion-safe:translate-y-2'
              }`;
              const onboardingError = onboardingErrors[row.authority_id];

              return (
                <Card
                  key={row.authority_id}
                  className={`overflow-hidden border border-gray-200 bg-white shadow-sm ${cardAnimation}`}
                >
                  <CardContent className="p-5">
                    <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <h2 className="text-base font-semibold text-[#0D1B2A]">{row.person_name}</h2>
                          <StatusPill status={row.offer?.status ?? null} />
                        </div>
                        <p className="mt-1 text-sm text-gray-600">{row.position}</p>

                        <dl className="mt-4 grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
                          <div>
                            <dt className="text-[11px] uppercase tracking-[0.12em] text-gray-500">Department</dt>
                            <dd className="mt-1 break-words text-gray-900">{row.department}</dd>
                          </div>
                          <div>
                            <dt className="text-[11px] uppercase tracking-[0.12em] text-gray-500">Entity</dt>
                            <dd className="mt-1 break-words text-gray-900">{row.entity}</dd>
                          </div>
                          <div>
                            <dt className="text-[11px] uppercase tracking-[0.12em] text-gray-500">Reference</dt>
                            <dd className="mt-1 break-words text-gray-900">{row.reference}</dd>
                          </div>
                          <div>
                            <dt className="text-[11px] uppercase tracking-[0.12em] text-gray-500">Planned start</dt>
                            <dd className="mt-1 text-gray-900">{formatDate(row.effective_date)}</dd>
                          </div>

                          {row.offer?.candidate_email ? (
                            <div className="col-span-2 sm:col-span-2">
                              <dt className="text-[11px] uppercase tracking-[0.12em] text-gray-500">
                                Candidate email
                              </dt>
                              <dd className="mt-1 break-words text-gray-900">{row.offer.candidate_email}</dd>
                            </div>
                          ) : null}

                          {row.offer?.start_date ? (
                            <div>
                              <dt className="text-[11px] uppercase tracking-[0.12em] text-gray-500">Offer start</dt>
                              <dd className="mt-1 text-gray-900">{formatDate(row.offer.start_date)}</dd>
                            </div>
                          ) : null}
                        </dl>
                      </div>

                      <div className="w-full shrink-0 lg:w-72">{renderActions(row)}</div>
                    </div>

                    {draftAuthorityId === row.authority_id && !row.offer ? (
                      <form
                        onSubmit={(e) => void handleDraft(e, row)}
                        className="mt-5 grid gap-3 rounded-lg border border-gray-200 bg-gray-50 p-4 sm:grid-cols-[1fr_1fr_auto] sm:items-end"
                      >
                        <div>
                          <label
                            htmlFor={`draft-email-${row.authority_id}`}
                            className="text-[11px] uppercase tracking-[0.12em] text-gray-500"
                          >
                            Candidate email
                          </label>
                          <input
                            id={`draft-email-${row.authority_id}`}
                            type="email"
                            value={draftForm.email}
                            onChange={(e) => setDraftForm((prev) => ({ ...prev, email: e.target.value }))}
                            className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2 text-sm outline-none focus:border-[#F4A623] focus:ring-2 focus:ring-[#F4A623]/30"
                            placeholder="name@example.com"
                          />
                        </div>
                        <div>
                          <label
                            htmlFor={`draft-start-${row.authority_id}`}
                            className="text-[11px] uppercase tracking-[0.12em] text-gray-500"
                          >
                            Start date
                          </label>
                          <input
                            id={`draft-start-${row.authority_id}`}
                            type="date"
                            value={draftForm.startDate}
                            onChange={(e) => setDraftForm((prev) => ({ ...prev, startDate: e.target.value }))}
                            className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2 text-sm outline-none focus:border-[#F4A623] focus:ring-2 focus:ring-[#F4A623]/30"
                          />
                        </div>
                        <div className="flex gap-2">
                          <Button type="submit" disabled={actionLoading !== null}>
                            {actionLoading?.authorityId === row.authority_id &&
                            actionLoading?.action === 'draft' ? (
                              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                            ) : null}
                            Draft letter
                          </Button>
                          <Button type="button" variant="ghost" onClick={() => setDraftAuthorityId(null)}>
                            Cancel
                          </Button>
                        </div>
                      </form>
                    ) : null}

                    {acceptAuthorityId === row.authority_id && row.offer?.status === 'sent' ? (
                      <form
                        onSubmit={(e) => {
                          e.preventDefault();
                          void handleStatus(row, 'accepted', acceptForm.email, acceptForm.startDate);
                        }}
                        className="mt-5 grid gap-3 rounded-lg border border-gray-200 bg-gray-50 p-4 sm:grid-cols-[1fr_1fr_auto] sm:items-end"
                      >
                        <div>
                          <label
                            htmlFor={`accept-email-${row.authority_id}`}
                            className="text-[11px] uppercase tracking-[0.12em] text-gray-500"
                          >
                            Candidate email
                          </label>
                          <input
                            id={`accept-email-${row.authority_id}`}
                            type="email"
                            value={acceptForm.email}
                            onChange={(e) => setAcceptForm((prev) => ({ ...prev, email: e.target.value }))}
                            className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2 text-sm outline-none focus:border-[#F4A623] focus:ring-2 focus:ring-[#F4A623]/30"
                            required
                          />
                        </div>
                        <div>
                          <label
                            htmlFor={`accept-start-${row.authority_id}`}
                            className="text-[11px] uppercase tracking-[0.12em] text-gray-500"
                          >
                            Start date
                          </label>
                          <input
                            id={`accept-start-${row.authority_id}`}
                            type="date"
                            value={acceptForm.startDate}
                            onChange={(e) => setAcceptForm((prev) => ({ ...prev, startDate: e.target.value }))}
                            className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2 text-sm outline-none focus:border-[#F4A623] focus:ring-2 focus:ring-[#F4A623]/30"
                            required
                          />
                        </div>
                        <div className="flex gap-2">
                          <Button type="submit" disabled={actionLoading !== null}>
                            {actionLoading?.authorityId === row.authority_id &&
                            actionLoading?.action === 'accepted' ? (
                              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                            ) : null}
                            Confirm acceptance
                          </Button>
                          <Button type="button" variant="ghost" onClick={() => setAcceptAuthorityId(null)}>
                            Cancel
                          </Button>
                        </div>
                      </form>
                    ) : null}

                    {onboardingError ? (
                      <div className="mt-4 flex items-start gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
                        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                        <span>{onboardingError}</span>
                      </div>
                    ) : null}
                  </CardContent>
                </Card>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
