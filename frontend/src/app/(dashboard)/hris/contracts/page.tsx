'use client';

import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import Link from 'next/link';
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Download,
  FileSpreadsheet,
  Save,
  Search,
  Upload,
  X,
} from 'lucide-react';
import { apiFetch, apiFetchBinary } from '@/lib/api';
import { TopBar } from '@/components/layout/TopBar';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';

interface ContractRow {
  id: number;
  employee_id: number;
  employee: string;
  company: string;
  department: string;
  job_title: string;
  grade: string;
  category: 'expatriate' | 'controller' | 'c_suite' | 'senior_manager' | 'employee';
  contract_type: string;
  contract_type_label?: string;
  start_date: string;
  end_date: string;
  probation_end_date: string;
  retirement_fund: string;
  days_to_end: number | null;
  reminder_from: string | null;
  due_for_renewal: boolean;
  on_probation: boolean;
  decision: string;
  decision_note: string;
  decided_by: string;
  decided_at: string;
}

interface ContractCounts {
  all: number;
  probation: number;
  fixed_term: number;
  due: number;
}

interface NoContractStaff {
  employee_id: number;
  name: string;
  company: string;
  department: string;
}

interface ContractResponse {
  rows: ContractRow[];
  counts: ContractCounts;
  rules: Record<string, number>;
  no_contract: NoContractStaff[];
}

interface UploadPreviewRow {
  row: number;
  name: string;
  status: 'ok' | 'error' | 'unmatched';
  messages: string[];
}

interface UploadPreviewSummary {
  total: number;
  ok: number;
  error: number;
  unmatched: number;
}

interface UploadPreviewResponse {
  rows: UploadPreviewRow[];
  summary?: UploadPreviewSummary;
  saved?: number;
}

interface DecisionResponse {
  message?: string;
}

interface ProbationDecisionResponse {
  message?: string;
}

interface FollowThroughResponse {
  new_contract_id?: number;
  letter_url?: string;
  next?: string;
  message?: string;
  offboarding_case_id?: number | string;
  error?: string;
}

interface FollowThroughState {
  loading: boolean;
  result?: FollowThroughResponse;
  error?: string;
}

type FollowThroughStates = Record<number, FollowThroughState>;

type DecisionValue = 'renew_same' | 'change' | 'end';
type ProbationDecisionValue = 'confirm' | 'extend' | 'end';
type ContractFilter = 'all' | 'probation' | 'fixed_term' | 'due';
type PanelType = 'decision' | 'probation';

const categoryLabelMap: Record<string, string> = {
  expatriate: 'Expatriate',
  controller: 'Controller',
  c_suite: 'C-suite',
  senior_manager: 'Senior Manager',
  employee: 'Employee',
};

const decisionLabelMap: Record<string, string> = {
  renew_same: 'Renew on the same terms',
  change: 'Renew with changes',
  end: 'End the contract',
};

const probationDecisionLabelMap: Record<string, string> = {
  confirm: 'Confirm the appointment',
  extend: 'Extend probation',
  end: 'End employment',
};

const formatDate = (value: string | null | undefined) =>
  value ? new Date(value).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' }) : '—';

const getErrorMessage = (error: unknown) =>
  error instanceof Error ? error.message : 'Something went wrong';

function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}

function uploadStatusPill(status: UploadPreviewRow['status']) {
  if (status === 'ok') {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-emerald-50 px-2 py-1 text-xs font-semibold text-emerald-700">
        <CheckCircle2 className="h-3.5 w-3.5" />
        OK
      </span>
    );
  }

  if (status === 'unmatched') {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-amber-50 px-2 py-1 text-xs font-semibold text-amber-700">
        <AlertTriangle className="h-3.5 w-3.5" />
        Unmatched
      </span>
    );
  }

  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-red-50 px-2 py-1 text-xs font-semibold text-red-700">
      <AlertTriangle className="h-3.5 w-3.5" />
      Error
    </span>
  );
}

function decisionPillClass(decision: string) {
  if (decision === 'renew_same') return 'bg-emerald-50 text-emerald-700';
  if (decision === 'change') return 'bg-amber-50 text-amber-700';
  if (decision === 'end') return 'bg-red-50 text-red-700';
  return 'bg-gray-100 text-gray-600';
}

function daysColour(row: ContractRow) {
  const days = row.days_to_end;
  if (days === null) return 'text-gray-500';

  if (days <= 30) return 'text-red-600';

  if (row.reminder_from && new Date(row.reminder_from).getTime() <= Date.now()) {
    return 'text-amber-600';
  }

  return 'text-gray-600';
}

export default function ContractsPage() {
  const searchParams = useSearchParams();
  const initialContractParam = useRef<string | null>(searchParams.get('contract'));
  const initialProbationParam = useRef<string | null>(searchParams.get('probation'));

  const [mounted, setMounted] = useState(false);
  const [filter, setFilter] = useState<ContractFilter>('all');
  const [searchInput, setSearchInput] = useState('');
  const [q, setQ] = useState('');
  const [data, setData] = useState<ContractResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const [activeContractId, setActiveContractId] = useState<number | null>(null);
  const [panelType, setPanelType] = useState<PanelType>('decision');

  const [decisionValue, setDecisionValue] = useState<DecisionValue | ''>('');
  const [note, setNote] = useState('');
  const [decisionSaving, setDecisionSaving] = useState(false);
  const [decisionError, setDecisionError] = useState('');
  const [decisionMessage, setDecisionMessage] = useState('');

  const [probationDecision, setProbationDecision] = useState<ProbationDecisionValue | ''>('');
  const [probationNewEnd, setProbationNewEnd] = useState('');
  const [probationNote, setProbationNote] = useState('');
  const [probationSaving, setProbationSaving] = useState(false);
  const [probationError, setProbationError] = useState('');
  const [probationMessage, setProbationMessage] = useState('');

  const [followThroughState, setFollowThroughState] = useState<FollowThroughStates>({});

  const [showNoContract, setShowNoContract] = useState(false);
  const [uploadPreview, setUploadPreview] = useState<UploadPreviewResponse | null>(null);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [savingUpload, setSavingUpload] = useState(false);
  const [uploadError, setUploadError] = useState('');
  const [uploadMessage, setUploadMessage] = useState('');

  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const tableRowRefs = useRef<Record<number, HTMLTableRowElement | null>>({});
  const cardRowRefs = useRef<Record<number, HTMLDivElement | null>>({});

  useEffect(() => {
    setMounted(true);
  }, []);

  useEffect(() => {
    const timer = setTimeout(() => setQ(searchInput), 350);
    return () => clearTimeout(timer);
  }, [searchInput]);

  const loadData = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const response = await apiFetch<ContractResponse>(
        `/hris/contracts/?filter=${filter}&q=${encodeURIComponent(q)}&company=`
      );
      setData(response);

      const contractParam = initialContractParam.current;
      if (contractParam) {
        const id = Number(contractParam);
        if (response.rows.some((row) => row.id === id)) {
          setActiveContractId(id);
          setPanelType('decision');
        }
        initialContractParam.current = null;
      } else if (initialProbationParam.current) {
        const id = Number(initialProbationParam.current);
        if (response.rows.some((row) => row.id === id)) {
          setActiveContractId(id);
          setPanelType('probation');
        }
        initialProbationParam.current = null;
      }
    } catch (err) {
      setError(getErrorMessage(err));
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [filter, q]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const activeRow = useMemo(
    () => data?.rows.find((row) => row.id === activeContractId) ?? null,
    [data, activeContractId]
  );

  useEffect(() => {
    if (!activeContractId || !data) return;

    const row = data.rows.find((r) => r.id === activeContractId);
    if (!row) {
      setActiveContractId(null);
      return;
    }

    if (panelType === 'probation') {
      setProbationDecision('');
      setProbationNewEnd('');
      setProbationNote('');
      setProbationError('');
      setProbationMessage('');

      setDecisionValue('');
      setNote('');
      setDecisionError('');
      setDecisionMessage('');
    } else {
      setDecisionValue(
        row.decision === 'renew_same'
          ? 'renew_same'
          : row.decision === 'change'
            ? 'change'
            : row.decision === 'end'
              ? 'end'
              : ''
      );
      setNote(row.decision_note ?? '');
      setDecisionError('');
      setDecisionMessage('');

      setProbationDecision('');
      setProbationNewEnd('');
      setProbationNote('');
      setProbationError('');
      setProbationMessage('');
    }
  }, [activeContractId, data, panelType]);

  useEffect(() => {
    if (!activeContractId) return;

    const target =
      window.innerWidth >= 640
        ? tableRowRefs.current[activeContractId]
        : cardRowRefs.current[activeContractId];

    target?.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }, [activeContractId]);

  const closePanel = () => {
    setActiveContractId(null);
    setDecisionError('');
    setDecisionMessage('');
    setProbationError('');
    setProbationMessage('');
  };

  const openDecision = (row: ContractRow) => {
    setActiveContractId(row.id);
    setPanelType('decision');
  };

  const openProbation = (row: ContractRow) => {
    setActiveContractId(row.id);
    setPanelType('probation');
  };

  const saveDecision = async () => {
    if (!activeRow) {
      setDecisionError('No contract selected');
      return;
    }

    if (!decisionValue) {
      setDecisionError('Choose a decision first');
      return;
    }

    setDecisionSaving(true);
    setDecisionError('');
    setDecisionMessage('');

    try {
      await apiFetch<DecisionResponse>(`/hris/contracts/${activeRow.id}/decision/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ decision: decisionValue, note }),
      });

      setDecisionMessage('Decision saved');
      setActiveContractId(null);
      await loadData();
    } catch (err) {
      setDecisionError(getErrorMessage(err));
    } finally {
      setDecisionSaving(false);
    }
  };

  const saveProbationDecision = async () => {
    if (!activeRow) {
      setProbationError('No contract selected');
      return;
    }

    if (!probationDecision) {
      setProbationError('Choose a probation decision first');
      return;
    }

    if (probationDecision === 'extend' && !probationNewEnd) {
      setProbationError('Choose the new probation end date');
      return;
    }

    setProbationSaving(true);
    setProbationError('');
    setProbationMessage('');

    const payload: { decision: ProbationDecisionValue; note: string; new_end?: string } = {
      decision: probationDecision,
      note: probationNote,
    };

    if (probationDecision === 'extend') {
      payload.new_end = probationNewEnd;
    }

    try {
      await apiFetch<ProbationDecisionResponse>(`/hris/contracts/${activeRow.id}/probation/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });

      setProbationMessage('Probation decision saved');
      setActiveContractId(null);
      await loadData();
    } catch (err) {
      setProbationError(getErrorMessage(err));
    } finally {
      setProbationSaving(false);
    }
  };

  const handleCarryThrough = async (row: ContractRow) => {
    setFollowThroughState((prev) => ({
      ...prev,
      [row.id]: { loading: true, error: '', result: undefined },
    }));

    try {
      const result = await apiFetch<FollowThroughResponse>(
        `/hris/contracts/${row.id}/follow-through/`,
        { method: 'POST' }
      );

      setFollowThroughState((prev) => ({
        ...prev,
        [row.id]: { loading: false, result, error: '' },
      }));

      await loadData();
    } catch (err) {
      setFollowThroughState((prev) => ({
        ...prev,
        [row.id]: {
          loading: false,
          result: prev[row.id]?.result,
          error: getErrorMessage(err),
        },
      }));
    }
  };

  const downloadRenewalLetter = async (rowId: number, url: string) => {
    try {
      const response = await apiFetchBinary(url);
      const blob = await response.blob();
      downloadBlob(blob, 'renewal.docx');
    } catch (err) {
      setFollowThroughState((prev) => {
        const current = prev[rowId] ?? { loading: false };
        return {
          ...prev,
          [rowId]: { ...current, error: getErrorMessage(err) },
        };
      });
    }
  };

  const renderFollowThroughResult = (row: ContractRow, result: FollowThroughResponse) => {
    return (
      <div className="mt-2 space-y-2 text-left">
        {result.error && <p className="text-sm text-red-600">{result.error}</p>}
        {result.message && <p className="text-sm text-gray-600">{result.message}</p>}
        {result.letter_url && (
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => downloadRenewalLetter(row.id, result.letter_url as string)}
          >
            <Download className="mr-1 h-4 w-4" />
            Download renewal letter
          </Button>
        )}
        {result.next && (
          <a
            href={result.next}
            className="inline-flex items-center rounded-md border border-gray-200 px-3 py-1.5 text-sm font-semibold text-[#0D1B2A] transition hover:border-[#0D1B2A]"
          >
            {result.message || 'Next step'}
          </a>
        )}
        {result.offboarding_case_id && (
          <Link
            href={`/hris/offboarding?case=${result.offboarding_case_id}`}
            className="inline-flex items-center rounded-md border border-gray-200 px-3 py-1.5 text-sm font-semibold text-[#0D1B2A] transition hover:border-[#0D1B2A]"
          >
            View offboarding case
          </Link>
        )}
      </div>
    );
  };

  const renderFollowThroughButton = (row: ContractRow) => {
    const state = followThroughState[row.id];

    return (
      <div className="flex flex-col items-start gap-1">
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => handleCarryThrough(row)}
          disabled={state?.loading}
        >
          {state?.loading ? 'Working…' : 'Carry it through'}
        </Button>
        {state?.error && <span className="text-left text-xs text-red-600">{state.error}</span>}
        {state?.result && renderFollowThroughResult(row, state.result)}
      </div>
    );
  };

  const renderDecisionPanel = (row: ContractRow) => {
    return (
      <div className="border-t border-gray-100 bg-gray-50 p-4 sm:p-5">
        <div className="flex items-start justify-between gap-3">
          <div>
            <p className="text-sm font-semibold text-[#0D1B2A]">Decision for {row.employee}</p>
            <p className="text-sm text-gray-500">{row.job_title} · {row.company}</p>
          </div>
          <Button type="button" variant="outline" size="sm" onClick={closePanel}>
            <X className="mr-1 h-4 w-4" />
            Close
          </Button>
        </div>

        {decisionError && <p className="mt-3 text-sm text-red-600">{decisionError}</p>}
        {decisionMessage && <p className="mt-3 text-sm text-emerald-600">{decisionMessage}</p>}

        <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-3">
          {(['renew_same', 'change', 'end'] as DecisionValue[]).map((value) => {
            const selected = decisionValue === value;
            return (
              <button
                key={value}
                type="button"
                disabled={decisionSaving}
                onClick={() => setDecisionValue(value)}
                className={`rounded-md border px-4 py-3 text-sm font-semibold transition ${
                  selected
                    ? 'border-[#0D1B2A] bg-[#0D1B2A] text-white'
                    : 'border-gray-200 bg-white text-gray-700 hover:border-[#0D1B2A]'
                }`}
              >
                {decisionLabelMap[value]}
              </button>
            );
          })}
        </div>

        <textarea
          value={note}
          onChange={(event) => setNote(event.target.value)}
          disabled={decisionSaving}
          rows={3}
          placeholder="Add a note (optional)"
          className="mt-3 w-full rounded-md border border-gray-200 bg-white p-2 text-sm outline-none focus:border-[#0D1B2A]"
        />

        <div className="mt-3">
          <Button type="button" onClick={saveDecision} disabled={decisionSaving || !decisionValue}>
            <Save className="mr-1 h-4 w-4" />
            {decisionSaving ? 'Saving...' : 'Save decision'}
          </Button>
        </div>
      </div>
    );
  };

  const renderProbationPanel = (row: ContractRow) => {
    const showExtendDate = probationDecision === 'extend';

    return (
      <div className="border-t border-gray-100 bg-gray-50 p-4 sm:p-5">
        <div className="flex items-start justify-between gap-3">
          <div>
            <p className="text-sm font-semibold text-[#0D1B2A]">Probation decision for {row.employee}</p>
            <p className="text-sm text-gray-500">{row.job_title} · {row.company}</p>
          </div>
          <Button type="button" variant="outline" size="sm" onClick={closePanel}>
            <X className="mr-1 h-4 w-4" />
            Close
          </Button>
        </div>

        {probationError && <p className="mt-3 text-sm text-red-600">{probationError}</p>}
        {probationMessage && <p className="mt-3 text-sm text-emerald-600">{probationMessage}</p>}

        <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-3">
          {(['confirm', 'extend', 'end'] as ProbationDecisionValue[]).map((value) => {
            const selected = probationDecision === value;
            return (
              <button
                key={value}
                type="button"
                disabled={probationSaving}
                onClick={() => setProbationDecision(value)}
                className={`rounded-md border px-4 py-3 text-sm font-semibold transition ${
                  selected
                    ? 'border-[#0D1B2A] bg-[#0D1B2A] text-white'
                    : 'border-gray-200 bg-white text-gray-700 hover:border-[#0D1B2A]'
                }`}
              >
                {probationDecisionLabelMap[value]}
              </button>
            );
          })}
        </div>

        {showExtendDate && (
          <div className="mt-3">
            <label className="text-sm font-medium text-gray-700">New probation end date</label>
            <input
              type="date"
              value={probationNewEnd}
              onChange={(event) => setProbationNewEnd(event.target.value)}
              disabled={probationSaving}
              className="mt-1 w-full rounded-md border border-gray-200 bg-white p-2 text-sm outline-none focus:border-[#0D1B2A]"
            />
          </div>
        )}

        <textarea
          value={probationNote}
          onChange={(event) => setProbationNote(event.target.value)}
          disabled={probationSaving}
          rows={3}
          placeholder="Add a note (optional)"
          className="mt-3 w-full rounded-md border border-gray-200 bg-white p-2 text-sm outline-none focus:border-[#0D1B2A]"
        />

        <div className="mt-3">
          <Button
            type="button"
            onClick={saveProbationDecision}
            disabled={probationSaving || !probationDecision || (showExtendDate && !probationNewEnd)}
          >
            <Save className="mr-1 h-4 w-4" />
            {probationSaving ? 'Saving...' : 'Save probation decision'}
          </Button>
        </div>
      </div>
    );
  };

  const renderActivePanel = (row: ContractRow) => {
    if (panelType === 'probation') return renderProbationPanel(row);
    return renderDecisionPanel(row);
  };

  const handleUploadFile = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;

    setSelectedFile(file);
    setUploadPreview(null);
    setUploadError('');
    setUploadMessage('');
    setUploading(true);

    const formData = new FormData();
    formData.append('file', file);

    try {
      const response = await apiFetch<UploadPreviewResponse>('/hris/contracts/upload/?commit=0', {
        method: 'POST',
        body: formData,
      });
      setUploadPreview(response);
    } catch (err) {
      setUploadError(getErrorMessage(err));
    } finally {
      setUploading(false);
      event.target.value = '';
    }
  };

  const commitUpload = async () => {
    if (!selectedFile) {
      setUploadError('Choose a file first');
      return;
    }

    setSavingUpload(true);
    setUploadError('');
    setUploadMessage('');

    const formData = new FormData();
    formData.append('file', selectedFile);

    try {
      const response = await apiFetch<UploadPreviewResponse>('/hris/contracts/upload/?commit=1', {
        method: 'POST',
        body: formData,
      });

      const okRows = response.summary?.ok ?? response.rows.filter((row) => row.status === 'ok').length;
      const saved = response.saved ?? okRows;

      setUploadMessage(`Saved ${saved} rows`);
      setUploadPreview(null);
      setSelectedFile(null);
      await loadData();
    } catch (err) {
      setUploadError(getErrorMessage(err));
    } finally {
      setSavingUpload(false);
    }
  };

  const downloadTemplate = async () => {
    setUploadError('');
    setUploadMessage('');

    try {
      const response = await apiFetchBinary('/api/v1/hris/contracts/template/');
      const blob = await response.blob();
      downloadBlob(blob, 'contract-sheet.xlsx');
    } catch (err) {
      setUploadError(getErrorMessage(err));
    }
  };

  const counts = data?.counts ?? { all: 0, probation: 0, fixed_term: 0, due: 0 };
  const filterTiles: { key: ContractFilter; label: string; count: number; highlight?: boolean }[] = [
    { key: 'all', label: 'All contracts', count: counts.all },
    { key: 'probation', label: 'On probation', count: counts.probation },
    { key: 'fixed_term', label: 'Fixed-term', count: counts.fixed_term },
    { key: 'due', label: 'Due for renewal', count: counts.due, highlight: counts.due > 0 },
  ];

  const rules = data?.rules ?? {};
  const rulesSummary =
    Object.keys(rules).length > 0
      ? `Reminders go out: ${Object.entries(rules)
          .map(([category, months]) => `${categoryLabelMap[category] ?? category} ${months} months`)
          .join(' · ')}`
      : '';

  return (
    <div className="min-h-screen bg-gray-50">
      <TopBar title="Contracts" />

      <main className="mx-auto max-w-6xl space-y-6 px-4 py-7 sm:px-6">
        <div
          className={`grid grid-cols-2 gap-3 sm:grid-cols-4 motion-safe:transition-all motion-safe:duration-500 ${
            mounted ? 'motion-safe:translate-y-0 motion-safe:opacity-100' : 'motion-safe:translate-y-2 motion-safe:opacity-0'
          }`}
        >
          {filterTiles.map((tile) => (
            <button
              key={tile.key}
              type="button"
              onClick={() => setFilter(tile.key)}
              className={`rounded-xl border bg-white p-4 text-left shadow-sm transition hover:shadow-md ${
                filter === tile.key ? 'border-[#0D1B2A] ring-2 ring-[#F4A623]/60' : 'border-gray-200'
              }`}
            >
              <p className="text-[11px] uppercase tracking-[0.12em] text-gray-500">{tile.label}</p>
              <p className={`mt-2 text-3xl font-bold ${tile.highlight ? 'text-[#F4A623]' : 'text-[#0D1B2A]'}`}>
                {tile.count}
              </p>
            </button>
          ))}
        </div>

        <div className="relative">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
          <input
            value={searchInput}
            onChange={(event) => setSearchInput(event.target.value)}
            placeholder="Search name, job title, department"
            className="w-full rounded-lg border border-gray-200 bg-white py-3 pl-9 pr-4 text-sm shadow-sm outline-none focus:border-[#0D1B2A]"
          />
        </div>

        {rulesSummary && (
          <div className="rounded-lg border border-gray-200 bg-white px-4 py-3 text-[11px] uppercase tracking-[0.12em] text-gray-500">
            {rulesSummary}
          </div>
        )}

        {loading ? (
          <p className="py-10 text-center text-sm text-gray-500">Loading contracts…</p>
        ) : error ? (
          <p className="rounded-lg border border-red-100 bg-red-50 px-4 py-3 text-sm text-red-600">{error}</p>
        ) : data ? (
          <>
            <div className="hidden overflow-x-auto sm:block">
              <table className="w-full border-collapse rounded-xl border border-gray-200 bg-white shadow-sm">
                <thead className="bg-gray-50 text-[11px] uppercase tracking-[0.12em] text-gray-500">
                  <tr>
                    <th className="p-3 text-left">Person</th>
                    <th className="p-3 text-left">Company</th>
                    <th className="p-3 text-left">Group</th>
                    <th className="p-3 text-left">Contract type</th>
                    <th className="p-3 text-left">End date</th>
                    <th className="p-3 text-left">Action</th>
                  </tr>
                </thead>
                <tbody>
                  {data.rows.map((row) => (
                    <Fragment key={row.id}>
                      <tr
                        ref={(el) => {
                          tableRowRefs.current[row.id] = el;
                        }}
                        className={`border-t border-gray-100 ${activeRow?.id === row.id ? 'bg-[#0D1B2A]/5' : ''}`}
                      >
                        <td className="p-3">
                          <p className="font-semibold text-[#0D1B2A]">{row.employee}</p>
                          <p className="text-sm text-gray-500">{row.job_title}</p>
                          {row.on_probation && <p className="text-xs font-semibold text-amber-600">On probation</p>}
                        </td>
                        <td className="p-3 text-sm text-gray-700">{row.company}</td>
                        <td className="p-3">
                          <span className="rounded-full bg-gray-100 px-2 py-1 text-xs font-semibold text-gray-700">
                            {categoryLabelMap[row.category] ?? row.category}
                          </span>
                        </td>
                        <td className="p-3 text-sm text-gray-700">{row.contract_type_label || row.contract_type}</td>
                        <td className="p-3">
                          <p className="text-sm text-gray-700">{formatDate(row.end_date)}</p>
                          {row.probation_end_date && (
                            <p className="text-xs text-gray-500">
                              Probation ends {formatDate(row.probation_end_date)}
                            </p>
                          )}
                          {row.days_to_end !== null && (
                            <p className={`text-xs font-semibold ${daysColour(row)}`}>
                              {row.days_to_end >= 0
                                ? `in ${row.days_to_end} days`
                                : `${Math.abs(row.days_to_end)} days ago`}
                            </p>
                          )}
                        </td>
                        <td className="p-3">
                          <div className="flex flex-col items-start gap-2">
                            {row.decision ? (
                              <span className={`inline-flex rounded-full px-2 py-1 text-xs font-semibold ${decisionPillClass(row.decision)}`}>
                                {decisionLabelMap[row.decision] ?? row.decision}
                              </span>
                            ) : (
                              <Button type="button" variant="outline" size="sm" onClick={() => openDecision(row)}>
                                Decide
                              </Button>
                            )}

                            {(row.on_probation || row.probation_end_date) && (
                              <Button type="button" variant="outline" size="sm" onClick={() => openProbation(row)}>
                                Probation
                              </Button>
                            )}

                            {row.decision && renderFollowThroughButton(row)}
                          </div>
                        </td>
                      </tr>
                      {activeRow?.id === row.id && (
                        <tr className="bg-[#0D1B2A]/5">
                          <td colSpan={6} className="p-0">
                            {renderActivePanel(row)}
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  ))}
                </tbody>
              </table>
            </div>

            <div className="space-y-3 sm:hidden">
              {data.rows.map((row) => (
                <div
                  key={row.id}
                  ref={(el) => {
                    cardRowRefs.current[row.id] = el;
                  }}
                  className={`rounded-xl border border-gray-200 bg-white p-4 shadow-sm ${activeRow?.id === row.id ? 'border-[#0D1B2A]' : ''}`}
                >
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <p className="font-semibold text-[#0D1B2A]">{row.employee}</p>
                      <p className="text-sm text-gray-500">{row.job_title}</p>
                      {row.on_probation && <p className="text-xs font-semibold text-amber-600">On probation</p>}
                    </div>
                    <span className="rounded-full bg-gray-100 px-2 py-1 text-xs font-semibold text-gray-700">
                      {categoryLabelMap[row.category] ?? row.category}
                    </span>
                  </div>

                  <dl className="mt-4 space-y-2 text-sm">
                    <div className="flex justify-between">
                      <dt className="text-gray-500">Company</dt>
                      <dd className="font-medium text-gray-800">{row.company}</dd>
                    </div>
                    <div className="flex justify-between">
                      <dt className="text-gray-500">Contract type</dt>
                      <dd className="font-medium text-gray-800">{row.contract_type_label || row.contract_type}</dd>
                    </div>
                    <div className="flex justify-between">
                      <dt className="text-gray-500">End date</dt>
                      <dd className="text-right font-medium text-gray-800">
                        <p>{formatDate(row.end_date)}</p>
                        {row.probation_end_date && (
                          <p className="text-xs text-gray-500">
                            Probation ends {formatDate(row.probation_end_date)}
                          </p>
                        )}
                        {row.days_to_end !== null && (
                          <p className={`text-xs font-semibold ${daysColour(row)}`}>
                            {row.days_to_end >= 0
                              ? `in ${row.days_to_end} days`
                              : `${Math.abs(row.days_to_end)} days ago`}
                          </p>
                        )}
                      </dd>
                    </div>
                    <div className="flex justify-between">
                      <dt className="text-gray-500">Action</dt>
                      <dd>
                        <div className="flex flex-col items-end gap-2">
                          {row.decision ? (
                            <span className={`inline-flex rounded-full px-2 py-1 text-xs font-semibold ${decisionPillClass(row.decision)}`}>
                              {decisionLabelMap[row.decision] ?? row.decision}
                            </span>
                          ) : (
                            <Button type="button" variant="outline" size="sm" onClick={() => openDecision(row)}>
                              Decide
                            </Button>
                          )}

                          {(row.on_probation || row.probation_end_date) && (
                            <Button type="button" variant="outline" size="sm" onClick={() => openProbation(row)}>
                              Probation
                            </Button>
                          )}

                          {row.decision && renderFollowThroughButton(row)}
                        </div>
                      </dd>
                    </div>
                  </dl>

                  {activeRow?.id === row.id && renderActivePanel(row)}
                </div>
              ))}
            </div>
          </>
        ) : null}

        <Card>
          <button
            type="button"
            onClick={() => setShowNoContract((visible) => !visible)}
            className="flex w-full items-center justify-between p-4 text-left"
          >
            <span className="font-semibold text-[#0D1B2A]">
              Staff with no contract in Omni ({data?.no_contract.length ?? 0})
            </span>
            {showNoContract ? (
              <ChevronUp className="h-5 w-5 text-gray-400" />
            ) : (
              <ChevronDown className="h-5 w-5 text-gray-400" />
            )}
          </button>

          {showNoContract && (
            <CardContent className="border-t border-gray-100">
              {(data?.no_contract.length ?? 0) === 0 ? (
                <p className="py-4 text-sm text-gray-500">Everyone has a contract.</p>
              ) : (
                <ul className="divide-y divide-gray-100">
                  {data?.no_contract.map((staff) => (
                    <li key={staff.employee_id} className="flex flex-col gap-1 py-3 text-sm sm:flex-row sm:justify-between">
                      <span className="font-semibold text-gray-800">{staff.name}</span>
                      <span className="text-gray-500">{staff.department} · {staff.company}</span>
                    </li>
                  ))}
                </ul>
              )}
            </CardContent>
          )}
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Data</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
              <Button type="button" variant="outline" onClick={downloadTemplate}>
                <Download className="mr-1 h-4 w-4" />
                Download the contract sheet
              </Button>

              <Button type="button" onClick={() => fileInputRef.current?.click()} disabled={uploading}>
                <FileSpreadsheet className="mr-1 h-4 w-4" />
                Upload the filled sheet
              </Button>

              <input
                ref={fileInputRef}
                type="file"
                accept=".xlsx,.xls"
                className="hidden"
                onChange={handleUploadFile}
              />
            </div>

            {uploading && <p className="text-sm text-gray-500">Uploading preview…</p>}
            {uploadMessage && <p className="text-sm text-emerald-600">{uploadMessage}</p>}
            {uploadError && <p className="text-sm text-red-600">{uploadError}</p>}

            {uploadPreview && (
              <div className="rounded-lg border border-gray-200 bg-white">
                <div className="flex items-center justify-between border-b border-gray-100 p-3">
                  <h3 className="font-semibold text-[#0D1B2A]">Upload preview</h3>
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    onClick={() => {
                      setUploadPreview(null);
                      setSelectedFile(null);
                      setUploadError('');
                      setUploadMessage('');
                    }}
                  >
                    <X className="mr-1 h-4 w-4" />
                    Clear
                  </Button>
                </div>

                <div className="overflow-x-auto">
                  <table className="w-full text-left text-sm">
                    <thead className="bg-gray-50 text-[11px] uppercase tracking-[0.12em] text-gray-500">
                      <tr>
                        <th className="p-3">Row</th>
                        <th className="p-3">Name</th>
                        <th className="p-3">Status</th>
                        <th className="p-3">Messages</th>
                      </tr>
                    </thead>
                    <tbody>
                      {uploadPreview.rows.map((row) => (
                        <tr key={row.row} className="border-t border-gray-100">
                          <td className="p-3 text-gray-700">{row.row}</td>
                          <td className="p-3 text-gray-700">{row.name}</td>
                          <td className="p-3">{uploadStatusPill(row.status)}</td>
                          <td className="p-3 text-gray-500">
                            {row.messages?.length > 0 ? row.messages.join(', ') : '—'}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>

                <div className="flex items-center justify-between p-3">
                  <p className="text-sm text-gray-500">
                    {uploadPreview.summary
                      ? `Total ${uploadPreview.summary.total} · OK ${uploadPreview.summary.ok} · Errors ${uploadPreview.summary.error} · Unmatched ${uploadPreview.summary.unmatched}`
                      : ''}
                  </p>
                  <Button type="button" onClick={commitUpload} disabled={savingUpload}>
                    <Upload className="mr-1 h-4 w-4" />
                    {savingUpload
                      ? 'Saving...'
                      : `Save ${uploadPreview.summary?.ok ?? uploadPreview.rows.filter((row) => row.status === 'ok').length} rows`}
                  </Button>
                </div>
              </div>
            )}
          </CardContent>
        </Card>
      </main>
    </div>
  );
}
