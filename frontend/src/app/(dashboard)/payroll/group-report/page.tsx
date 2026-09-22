'use client';

import { Fragment, useCallback, useEffect, useRef, useState } from 'react';
import { yearToDate } from '@/lib/groupPayrollYtd';
import {
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Download,
  Printer,
  RefreshCw,
  XCircle,
  Users,
} from 'lucide-react';
import {
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
} from 'recharts';
import { apiFetchBinary } from '@/lib/api';
import { TopBar } from '@/components/layout/TopBar';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';

interface GroupTotals {
  headcount: number;
  basic: number;
  incentive: number;
  commission: number;
  employer_contrib: number;
  gross: number;
  paye: number;
  net: number;
  ctc: number;
  final_companies: number;
  draft_companies: number;
}

interface DepartmentBreakdown {
  headcount: number;
  ctc: number;
}

interface CompanyLine {
  company: string;
  company_id?: number;
  status: 'final' | 'draft' | 'not_run';
  headcount: number;
  basic: number;
  incentive: number;
  commission: number;
  employer_contrib: number;
  gross: number;
  paye: number;
  net: number;
  ctc: number;
  source_currency: string;
  source_gross: number;
  fx_rate: number;
  tieout_ok: boolean;
  tieout_note: string | null;
  by_department: Record<string, DepartmentBreakdown>;
}

interface VersionInfo {
  version: number;
  generated_at: string;
  trigger: string;
}

interface PriorReport {
  period: string;
  totals: GroupTotals;
}

interface GroupReportResponse {
  period: string;
  version: number;
  trigger: string;
  generated_at: string;
  generated_by: string;
  totals: GroupTotals;
  excluded: string[];
  lines: CompanyLine[];
  versions: VersionInfo[];
  prior: PriorReport | null;
  periods: string[];
  can_regenerate: boolean;
}

interface TrendPoint {
  period: string;
  ctc: number | string;
  headcount: number;
  by_company: Record<string, number>;
}

interface TrendResponse {
  points: TrendPoint[];
}

interface DepartmentRow {
  department: string;
  headcount: number;
  ctc: number;
}

interface DepartmentsResponse {
  rows: DepartmentRow[];
}

interface AgentCommissionCycle {
  label: string;
  status: string;
  total: number;
}

interface AgentCommissionsResponse {
  total: number;
  cycles: AgentCommissionCycle[];
  note: string | null;
}

interface PeopleRow {
  name: string;
  department: string;
  job_title: string;
  basic: number;
  incentive: number;
  commission: number;
  gross: number;
  ctc: number;
}

interface PeopleResponse {
  rows: PeopleRow[];
}

interface PayrollErrorPayload {
  detail?: string;
  period?: string;
  can_regenerate?: boolean;
}

class PayrollApiError extends Error {
  status: number;
  payload: PayrollErrorPayload;

  constructor(status: number, payload: PayrollErrorPayload) {
    super(payload.detail || 'Payroll request failed');
    this.status = status;
    this.payload = payload;
  }
}

const pula = (value: number | null | undefined) =>
  'P' + Number(value ?? 0).toLocaleString('en-BW', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });

const shortPula = (value: number) => {
  const abs = Math.abs(value);
  if (abs >= 1_000_000) {
    return `P${(value / 1_000_000).toFixed(1)}m`;
  }
  if (abs >= 1000) {
    return `P${(value / 1000).toFixed(1)}k`;
  }
  return pula(value);
};

const formatPeriodShort = (period: string) => {
  if (!period) return '';
  return new Date(`${period}-01T00:00:00`).toLocaleDateString('en-GB', {
    month: 'short',
    year: '2-digit',
  });
};

const getErrorMessage = (error: unknown) =>
  error instanceof Error ? error.message : 'Something went wrong';

const formatDate = (value: string | null | undefined) =>
  value ? new Date(value).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' }) : '—';

const formatTime = (value: string | null | undefined) =>
  value ? new Date(value).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' }) : '—';

const monthName = (period: string) => {
  if (!period) return 'Group Payroll';
  return new Date(`${period}-01T00:00:00`).toLocaleDateString('en-GB', {
    month: 'long',
    year: 'numeric',
  });
};

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

async function fetchJsonWithStatus<T>(path: string): Promise<T> {
  const response = await apiFetchBinary(path);
  if (!response.ok) {
    const payload = (await response.json().catch(() => ({}))) as PayrollErrorPayload;
    throw new PayrollApiError(response.status, payload);
  }
  return response.json() as Promise<T>;
}

async function fetchGroupReport(period: string, version?: string): Promise<GroupReportResponse> {
  const params = new URLSearchParams();
  if (period) params.set('period', period);
  if (version) params.set('version', version);

  const response = await apiFetchBinary(`/api/v1/payroll/group-report/?${params.toString()}`);

  if (!response.ok) {
    const payload = (await response.json().catch(() => ({}))) as PayrollErrorPayload;
    throw new PayrollApiError(response.status, payload);
  }

  return response.json() as Promise<GroupReportResponse>;
}

const statusPillClass = (status: CompanyLine['status']) => {
  if (status === 'final') return 'bg-emerald-50 text-emerald-700';
  if (status === 'draft') return 'bg-amber-50 text-amber-700';
  return 'bg-red-50 text-red-700';
};

const statusLabel = (status: CompanyLine['status']) => status.replace('_', ' ');

const barColours = [
  'bg-[#0D1B2A]',
  'bg-[#1B2C41]',
  'bg-[#2C3E55]',
  'bg-[#3F526B]',
  'bg-[#52647A]',
  'bg-[#677894]',
];

export default function GroupReportPage() {
  const [period, setPeriod] = useState('');
  const [version, setVersion] = useState('');
  const [data, setData] = useState<GroupReportResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [forbidden, setForbidden] = useState('');
  const [notFound, setNotFound] = useState<{
    period: string;
    canRegenerate: boolean;
    detail: string;
  } | null>(null);
  const [regenerating, setRegenerating] = useState(false);
  const [message, setMessage] = useState('');
  const [expandedCompany, setExpandedCompany] = useState<string | null>(null);

  // New states for trend, departments, agent commissions, people
  const [trendData, setTrendData] = useState<TrendResponse | null>(null);
  const [trendError, setTrendError] = useState(false);
  const [trendForbidden, setTrendForbidden] = useState(false);

  const [departmentsData, setDepartmentsData] = useState<DepartmentsResponse | null>(null);
  const [departmentsError, setDepartmentsError] = useState(false);
  const [departmentsForbidden, setDepartmentsForbidden] = useState(false);

  const [agentData, setAgentData] = useState<AgentCommissionsResponse | null>(null);
  const [agentError, setAgentError] = useState(false);
  const [agentForbidden, setAgentForbidden] = useState(false);

  const [peopleCompanyId, setPeopleCompanyId] = useState<number | null>(null);
  const [peopleData, setPeopleData] = useState<PeopleResponse | null>(null);
  const [peopleLoading, setPeopleLoading] = useState(false);
  const [peopleError, setPeopleError] = useState(false);
  const [peopleForbidden, setPeopleForbidden] = useState(false);

  const skipNextLoad = useRef(false);

  // --- Existing main report load ---
  const loadReport = useCallback(async (p: string, v: string) => {
    setLoading(true);
    setError('');
    setForbidden('');
    setNotFound(null);
    setMessage('');

    try {
      const response = await fetchGroupReport(p, v);
      setData(response);
      if (!p && response.period) {
        skipNextLoad.current = true;
        setPeriod(response.period);
      }
    } catch (err) {
      if (err instanceof PayrollApiError && err.status === 404) {
        setNotFound({
          period: p || err.payload.period || '',
          canRegenerate: err.payload.can_regenerate ?? false,
          detail: err.payload.detail ?? `No report for ${p} yet`,
        });
        setData(null);
      } else if (err instanceof PayrollApiError && err.status === 403) {
        setForbidden(
          err.payload.detail ||
            'This report is only for the CFO, CEO, COO and HR leadership.'
        );
        setData(null);
      } else {
        setError(getErrorMessage(err));
        setData(null);
      }
    } finally {
      setLoading(false);
    }
  }, []);

  // One load per change
  const linkRead = useRef(false);
  useEffect(() => {
    if (!linkRead.current) {
      linkRead.current = true;
      const fromLink = new URLSearchParams(window.location.search).get('period') || '';
      if (fromLink && fromLink !== period) {
        setPeriod(fromLink);
        return;
      }
    }
    if (skipNextLoad.current) {
      skipNextLoad.current = false;
      return;
    }
    loadReport(period, version);
  }, [period, version, loadReport]);

  // --- New: Fetch trend once ---
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const result = await fetchJsonWithStatus<TrendResponse>('/api/v1/payroll/group-report/trend/');
        if (!cancelled) setTrendData(result);
      } catch (err) {
        if (!cancelled) {
          if (err instanceof PayrollApiError && err.status === 403) {
            setTrendForbidden(true);
          } else {
            setTrendError(true);
          }
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // --- New: Fetch departments and agent commissions when period changes ---
  useEffect(() => {
    if (!period) return;
    let cancelled = false;

    (async () => {
      try {
        const deps = await fetchJsonWithStatus<DepartmentsResponse>(
          `/api/v1/payroll/group-report/departments/?period=${encodeURIComponent(period)}`
        );
        if (!cancelled) setDepartmentsData(deps);
      } catch (err) {
        if (!cancelled) {
          if (err instanceof PayrollApiError && err.status === 403) {
            setDepartmentsForbidden(true);
          } else {
            setDepartmentsError(true);
          }
        }
      }

      try {
        const agent = await fetchJsonWithStatus<AgentCommissionsResponse>(
          `/api/v1/payroll/group-report/agent-commissions/?period=${encodeURIComponent(period)}`
        );
        if (!cancelled) setAgentData(agent);
      } catch (err) {
        if (!cancelled) {
          if (err instanceof PayrollApiError && err.status === 403) {
            setAgentForbidden(true);
          } else {
            setAgentError(true);
          }
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [period]);

  // --- New: People drill-down handler ---
  const handleShowPeople = async (companyId: number) => {
    if (peopleLoading && peopleCompanyId === companyId) return;
    setPeopleCompanyId(companyId);
    setPeopleLoading(true);
    setPeopleError(false);
    setPeopleForbidden(false);
    setPeopleData(null);

    try {
      const result = await fetchJsonWithStatus<PeopleResponse>(
        `/api/v1/payroll/group-report/people/?period=${encodeURIComponent(period)}&company=${companyId}`
      );
      setPeopleData(result);
    } catch (err) {
      if (err instanceof PayrollApiError && err.status === 403) {
        setPeopleForbidden(true);
      } else {
        setPeopleError(true);
      }
    } finally {
      setPeopleLoading(false);
    }
  };

  const canRegenerate = data?.can_regenerate || notFound?.canRegenerate || false;

  const handleRegenerate = async () => {
    setRegenerating(true);
    setError('');
    setMessage('');

    try {
      const response = await apiFetchBinary('/api/v1/payroll/group-report/regenerate/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ period }),
      });

      if (!response.ok) {
        const payload = (await response.json().catch(() => ({}))) as PayrollErrorPayload;
        throw new PayrollApiError(response.status, payload);
      }

      setMessage('Report regenerated');
      await loadReport(period, version);
    } catch (err) {
      if (err instanceof PayrollApiError && err.status === 403) {
        setForbidden(err.message);
      } else {
        setError(getErrorMessage(err));
      }
    } finally {
      setRegenerating(false);
    }
  };

  const handleExport = async () => {
    setError('');
    setMessage('');

    const exportVersion = version || (data ? String(data.version) : '');

    try {
      const response = await apiFetchBinary(
        `/api/v1/payroll/group-report/export/?period=${encodeURIComponent(period)}&version=${encodeURIComponent(exportVersion)}`
      );

      if (!response.ok) {
        const text = await response.text();
        throw new Error(text || 'Download failed');
      }

      const blob = await response.blob();
      downloadBlob(blob, `group-payroll-${period}.xlsx`);
    } catch (err) {
      setError(getErrorMessage(err));
    }
  };

  const currentCtc = Number(data?.totals.ctc ?? 0);
  const priorCtc = Number(data?.prior?.totals?.ctc ?? 0);
  const ytd = data && trendData ? yearToDate(trendData.points, data.period) : null;
  const momPercent = priorCtc ? ((currentCtc - priorCtc) / Math.abs(priorCtc)) * 100 : null;

  const chartLines = (data?.lines ?? []).filter((line) => line.ctc > 0);
  const totalCtc = data?.totals.ctc ?? 0;

  const secondaryTiles: { label: string; value: number; number?: boolean }[] = data
    ? [
        { label: 'Basic pay', value: data.totals.basic },
        { label: 'Incentives', value: data.totals.incentive },
        { label: 'Commissions', value: data.totals.commission },
        { label: 'Employer contributions', value: data.totals.employer_contrib },
        { label: 'PAYE', value: data.totals.paye },
        { label: 'Headcount', value: data.totals.headcount, number: true },
      ]
    : [];

  const periodOptions = data?.periods ?? [];
  const showFallbackPeriod = period && !periodOptions.includes(period);

  const versions = data?.versions ?? [];

  // API sends money as strings — convert before adding, or the share becomes NaN.
  const deptTotal = departmentsData?.rows.reduce((sum, row) => sum + Number(row.ctc), 0) ?? 0;

  return (
    <div className="min-h-screen bg-gray-50 print:bg-white">
      <div className="print:hidden">
        <TopBar title="Group Payroll" />
      </div>

      <main className="mx-auto max-w-6xl space-y-6 px-4 py-7 sm:px-6">
        <div className="flex flex-col gap-4 print:hidden lg:flex-row lg:items-center lg:justify-between">
          <div>
            <h1 className="text-2xl font-bold text-[#0D1B2A]">{monthName(period)}</h1>
            {data && (
              <p className="text-sm text-gray-500">
                Version {data.version} · generated {formatDate(data.generated_at)} {formatTime(data.generated_at)} · by{' '}
                {data.trigger}
              </p>
            )}
          </div>

          <div className="flex flex-wrap gap-2">
            <select
              value={period}
              onChange={(event) => setPeriod(event.target.value)}
              className="rounded-md border border-gray-200 bg-white px-3 py-2 text-sm shadow-sm outline-none focus:border-[#0D1B2A]"
            >
              {showFallbackPeriod && <option value={period}>{period}</option>}
              {periodOptions.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </select>

            <select
              value={version || (data ? String(data.version) : '')}
              onChange={(event) => setVersion(event.target.value)}
              className="rounded-md border border-gray-200 bg-white px-3 py-2 text-sm shadow-sm outline-none focus:border-[#0D1B2A]"
            >
              {versions.length === 0 && <option value="">Latest</option>}
              {versions.map((item) => (
                <option key={item.version} value={String(item.version)}>
                  Version {item.version}
                </option>
              ))}
            </select>

            <Button type="button" variant="outline" onClick={handleExport} disabled={!data}>
              <Download className="mr-1 h-4 w-4" />
              Export
            </Button>
            <Button type="button" variant="outline" onClick={() => window.print()}>
              <Printer className="mr-1 h-4 w-4" />
              Print
            </Button>
            {canRegenerate && (
              <Button type="button" onClick={handleRegenerate} disabled={regenerating}>
                <RefreshCw className="mr-1 h-4 w-4" />
                {regenerating ? 'Regenerating...' : 'Regenerate'}
              </Button>
            )}
          </div>
        </div>

        {message && <p className="rounded-lg border border-emerald-100 bg-emerald-50 px-4 py-3 text-sm text-emerald-700">{message}</p>}
        {error && <p className="rounded-lg border border-red-100 bg-red-50 px-4 py-3 text-sm text-red-600">{error}</p>}

        {loading ? (
          <p className="py-14 text-center text-sm text-gray-500">Loading group payroll…</p>
        ) : forbidden ? (
          <p className="rounded-lg border border-red-100 bg-red-50 px-4 py-3 text-sm text-red-700">{forbidden}</p>
        ) : notFound ? (
          <Card className="border border-gray-200 shadow-sm">
            <CardContent className="p-10 text-center">
              <h2 className="text-xl font-bold text-[#0D1B2A]">{notFound.detail}</h2>
              <p className="mt-2 text-sm text-gray-500">Period: {notFound.period}</p>
              {notFound.canRegenerate && (
                <Button type="button" onClick={handleRegenerate} disabled={regenerating} className="mt-5">
                  <RefreshCw className="mr-1 h-4 w-4" />
                  {regenerating ? 'Generating...' : 'Generate report'}
                </Button>
              )}
            </CardContent>
          </Card>
        ) : data ? (
          <>
            {/* Hero tile */}
            <div className="rounded-2xl bg-[#0D1B2A] p-6 text-white shadow-lg">
              <p className="text-[11px] uppercase tracking-[0.12em] text-white/80">Cost to company</p>
              <p className="mt-2 text-4xl font-bold tabular-nums text-white sm:text-5xl">
                {pula(data.totals.ctc)}
              </p>
              {momPercent !== null ? (
                <p className={`mt-2 text-sm font-semibold ${momPercent >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                  {momPercent >= 0 ? '▲' : '▼'} {Math.abs(momPercent).toFixed(1)}% vs {monthName(data.prior?.period ?? '')}
                </p>
              ) : (
                <p className="mt-2 text-sm text-white/80">First saved month — no earlier month to compare yet</p>
              )}
              {ytd && ytd.months > 0 && (
                <p className="mt-3 text-sm text-white/90">
                  Year to date (from {formatPeriodShort(ytd.from)}, {ytd.months} {ytd.months === 1 ? 'month' : 'months'}):{' '}
                  <span className="font-semibold tabular-nums">{pula(ytd.total)}</span>
                </p>
              )}
            </div>

            {/* Secondary tiles */}
            <div className="grid grid-cols-2 gap-3 md:grid-cols-3 2xl:grid-cols-6">
              {secondaryTiles.map((tile) => (
                <Card key={tile.label} className="border border-gray-200 shadow-sm">
                  <CardContent className="p-4">
                    <p className="text-[11px] uppercase tracking-[0.12em] text-gray-500">{tile.label}</p>
                    <p className="mt-2 break-words text-xl font-bold tabular-nums text-[#0D1B2A]">
                      {tile.number ? tile.value.toLocaleString('en-BW') : pula(tile.value)}
                    </p>
                  </CardContent>
                </Card>
              ))}
            </div>

            {/* UniCoin agent commissions tile */}
            {!agentForbidden && (
              <Card className="border border-gray-200 shadow-sm">
                <CardContent className="p-4">
                  <p className="text-[11px] uppercase tracking-[0.12em] text-gray-500">
                    UniCoin agent commissions (paid outside payroll)
                  </p>
                  {agentError ? (
                    <p className="mt-2 text-sm text-gray-500">Could not load</p>
                  ) : agentData ? (
                    <>
                      <p className="mt-2 text-2xl font-bold text-[#0D1B2A]">{pula(agentData.total)}</p>
                      {agentData.total === 0 ? (
                        <p className="mt-1 text-sm text-gray-500">No agent commission cycle for this month</p>
                      ) : (
                        <ul className="mt-2 space-y-1">
                          {agentData.cycles.map((cycle) => (
                            <li key={cycle.label} className="flex justify-between text-sm">
                              <span className="text-gray-600">
                                {cycle.label} <span className="text-xs text-gray-400">({cycle.status})</span>
                              </span>
                              <span className="font-medium text-[#0D1B2A]">{pula(cycle.total)}</span>
                            </li>
                          ))}
                        </ul>
                      )}
                      {agentData.note && <p className="mt-2 text-xs text-gray-500">{agentData.note}</p>}
                    </>
                  ) : (
                    <p className="mt-2 text-sm text-gray-500">Loading…</p>
                  )}
                </CardContent>
              </Card>
            )}

            {/* Cost to company — last 12 months trend */}
            {!trendForbidden && (
              <Card className="border border-gray-200 shadow-sm">
                <CardHeader>
                  <CardTitle className="text-base font-bold text-[#0D1B2A]">
                    Cost to company — last 12 months
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  {trendError ? (
                    <p className="text-sm text-gray-500">Could not load</p>
                  ) : trendData && trendData.points.length >= 2 ? (
                    <div className="h-64">
                      <ResponsiveContainer width="100%" height="100%">
                        <LineChart data={trendData.points}>
                          <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
                          <XAxis
                            dataKey="period"
                            tickFormatter={formatPeriodShort}
                            tickLine={false}
                            axisLine={false}
                            tick={{ fontSize: 12, fill: '#6B7280' }}
                          />
                          <YAxis
                            tickFormatter={shortPula}
                            tickLine={false}
                            axisLine={false}
                            width={60}
                            tick={{ fontSize: 12, fill: '#6B7280' }}
                          />
                          <Tooltip
                            formatter={(value) => pula(Number(value))}
                            labelFormatter={(label) => monthName(String(label))}
                          />
                          <Line type="monotone" dataKey="ctc" stroke="#0D1B2A" strokeWidth={2} dot={false} />
                        </LineChart>
                      </ResponsiveContainer>
                    </div>
                  ) : (
                    <p className="text-sm text-gray-500">Trend appears once two months are saved</p>
                  )}
                </CardContent>
              </Card>
            )}

            {/* By department */}
            {!departmentsForbidden && (
              <Card className="border border-gray-200 shadow-sm">
                <CardHeader>
                  <CardTitle className="text-base font-bold text-[#0D1B2A]">
                    By department — {monthName(period)}
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  {departmentsError ? (
                    <p className="text-sm text-gray-500">Could not load</p>
                  ) : departmentsData ? (
                    <div className="space-y-3">
                      {departmentsData.rows.map((row) => {
                        const share = deptTotal ? (Number(row.ctc) / deptTotal) * 100 : 0;
                        return (
                          <div key={row.department}>
                            <div className="flex justify-between text-sm">
                              <span className="font-medium text-[#0D1B2A]">{row.department}</span>
                              <span className="text-gray-600">
                                {row.headcount} · {pula(row.ctc)} · {share.toFixed(1)}%
                              </span>
                            </div>
                            <div className="mt-1 h-2 w-full rounded-full bg-gray-100">
                              <div
                                className="h-2 rounded-full bg-[#0D1B2A]"
                                style={{ width: `${share}%` }}
                              />
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  ) : (
                    <p className="text-sm text-gray-500">Loading…</p>
                  )}
                </CardContent>
              </Card>
            )}

            {/* Company breakdown bar */}
            <div>
              <div className="flex h-3 w-full overflow-hidden rounded-full">
                {totalCtc > 0 && chartLines.map((line, index) => (
                  <div
                    key={line.company}
                    style={{ width: `${(line.ctc / totalCtc) * 100}%` }}
                    className={barColours[index % barColours.length]}
                    title={`${line.company}: ${pula(line.ctc)}`}
                  />
                ))}
              </div>
              <div className="mt-3 grid grid-cols-1 gap-1 text-xs text-gray-500 sm:grid-cols-2 lg:grid-cols-3">
                {chartLines.map((line) => (
                  <span key={line.company}>
                    {line.company} ({pula(line.ctc)})
                  </span>
                ))}
              </div>
            </div>

            {data.excluded.length > 0 && (
              <p className="text-xs text-gray-500">
                Group total excludes: {data.excluded.join(', ')} (not yet run)
              </p>
            )}

            {/* Desktop table */}
            <div className="hidden overflow-x-auto md:block">
              <table className="w-full border-collapse rounded-xl border border-gray-200 bg-white shadow-sm">
                <thead className="bg-gray-50 text-[11px] uppercase tracking-[0.12em] text-gray-500">
                  <tr>
                    <th className="p-3 text-left">Company</th>
                    <th className="p-3 text-left">Status</th>
                    <th className="p-3 text-right">Headcount</th>
                    <th className="p-3 text-right">Basic</th>
                    <th className="p-3 text-right">Incentives</th>
                    <th className="p-3 text-right">Commissions</th>
                    <th className="p-3 text-right">CTC</th>
                    <th className="p-3 text-left">Tie-out</th>
                  </tr>
                </thead>
                <tbody>
                  {data.lines.map((line) => (
                    <Fragment key={line.company}>
                      <tr
                        className="cursor-pointer border-t border-gray-100 hover:bg-gray-50"
                        onClick={() =>
                          setExpandedCompany((current) => (current === line.company ? null : line.company))
                        }
                      >
                        <td className="p-3">
                          <div className="flex items-center gap-2 font-semibold text-[#0D1B2A]">
                            {line.company}
                            {expandedCompany === line.company ? (
                              <ChevronUp className="h-4 w-4 text-gray-400" />
                            ) : (
                              <ChevronDown className="h-4 w-4 text-gray-400" />
                            )}
                          </div>
                        </td>
                        <td className="p-3">
                          <span className={`inline-flex rounded-full px-2 py-1 text-xs font-semibold ${statusPillClass(line.status)}`}>
                            {statusLabel(line.status)}
                          </span>
                        </td>
                        <td className="p-3 text-right text-sm text-gray-700">{line.headcount}</td>
                        <td className="p-3 text-right text-sm text-gray-700">{pula(line.basic)}</td>
                        <td className="p-3 text-right text-sm text-gray-700">{pula(line.incentive)}</td>
                        <td className="p-3 text-right text-sm text-gray-700">{pula(line.commission)}</td>
                        <td className="p-3 text-right font-semibold text-[#0D1B2A]">
                          {pula(line.ctc)}
                          {line.source_currency && line.source_currency !== 'BWP' && (
                            <p className="text-xs font-normal text-gray-500">
                              {line.source_currency} {line.source_gross.toLocaleString('en-BW')} at {line.fx_rate}
                            </p>
                          )}
                        </td>
                        <td className="p-3">
                          <div className="flex items-center gap-2">
                            {line.tieout_ok ? (
                              <CheckCircle2 className="h-5 w-5 text-emerald-600" />
                            ) : (
                              <XCircle className="h-5 w-5 text-red-500" />
                            )}
                            {line.tieout_note && (
                              <p className="text-xs text-gray-500">{line.tieout_note}</p>
                            )}
                          </div>
                        </td>
                      </tr>

                      {expandedCompany === line.company && (
                        <tr className="border-t border-gray-100 bg-gray-50">
                          <td colSpan={8} className="p-4">
                            <div className="space-y-2">
                              {Object.entries(line.by_department)
                                .sort((a, b) => b[1].ctc - a[1].ctc)
                                .map(([department, breakdown]) => (
                                  <div key={department} className="flex items-center justify-between text-sm">
                                    <span className="text-gray-700">{department}</span>
                                    <span className="font-medium text-[#0D1B2A]">
                                      {breakdown.headcount} · {pula(breakdown.ctc)}
                                    </span>
                                  </div>
                                ))}
                            </div>

                            {line.company_id !== undefined && (
                              <div className="mt-4">
                                <Button
                                  type="button"
                                  variant="outline"
                                  size="sm"
                                  onClick={() => handleShowPeople(line.company_id!)}
                                  disabled={peopleLoading && peopleCompanyId === line.company_id}
                                >
                                  <Users className="mr-1 h-4 w-4" />
                                  {peopleLoading && peopleCompanyId === line.company_id ? 'Loading…' : 'Show people'}
                                </Button>
                                {peopleError && peopleCompanyId === line.company_id && (
                                  <p className="mt-2 text-sm text-gray-500">Could not load</p>
                                )}
                                {peopleForbidden && peopleCompanyId === line.company_id && (
                                  <p className="mt-2 text-sm text-gray-500">Confidential — viewing is logged</p>
                                )}
                                {peopleData && peopleCompanyId === line.company_id && (
                                  <div className="mt-3 overflow-x-auto">
                                    <table className="w-full text-left text-sm">
                                      <thead className="text-[11px] uppercase tracking-[0.12em] text-gray-500">
                                        <tr>
                                          <th className="p-2">Name</th>
                                          <th className="p-2">Department</th>
                                          <th className="p-2">Job title</th>
                                          <th className="p-2 text-right">Basic</th>
                                          <th className="p-2 text-right">Incentive</th>
                                          <th className="p-2 text-right">Commission</th>
                                          <th className="p-2 text-right">Gross</th>
                                          <th className="p-2 text-right">CTC</th>
                                        </tr>
                                      </thead>
                                      <tbody>
                                        {peopleData.rows.map((person) => (
                                          <tr key={person.name} className="border-t border-gray-100">
                                            <td className="p-2 font-medium text-[#0D1B2A]">{person.name}</td>
                                            <td className="p-2 text-gray-600">{person.department}</td>
                                            <td className="p-2 text-gray-600">{person.job_title}</td>
                                            <td className="p-2 text-right text-gray-600">{pula(person.basic)}</td>
                                            <td className="p-2 text-right text-gray-600">{pula(person.incentive)}</td>
                                            <td className="p-2 text-right text-gray-600">{pula(person.commission)}</td>
                                            <td className="p-2 text-right text-gray-600">{pula(person.gross)}</td>
                                            <td className="p-2 text-right font-semibold text-[#0D1B2A]">{pula(person.ctc)}</td>
                                          </tr>
                                        ))}
                                      </tbody>
                                    </table>
                                    <p className="mt-2 text-xs text-gray-400">Confidential — viewing is logged</p>
                                  </div>
                                )}
                              </div>
                            )}
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  ))}
                </tbody>
              </table>
            </div>

            {/* Mobile cards */}
            <div className="space-y-3 md:hidden">
              {data.lines.map((line) => (
                <div key={line.company} className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
                  <button
                    type="button"
                    className="flex w-full items-center justify-between"
                    onClick={() =>
                      setExpandedCompany((current) => (current === line.company ? null : line.company))
                    }
                  >
                    <span className="font-semibold text-[#0D1B2A]">{line.company}</span>
                    <span className={`inline-flex rounded-full px-2 py-1 text-xs font-semibold ${statusPillClass(line.status)}`}>
                      {statusLabel(line.status)}
                    </span>
                  </button>

                  <dl className="mt-4 space-y-2 text-sm">
                    <div className="flex justify-between">
                      <dt className="text-gray-500">Headcount</dt>
                      <dd className="font-medium text-gray-800">{line.headcount}</dd>
                    </div>
                    <div className="flex justify-between">
                      <dt className="text-gray-500">Basic</dt>
                      <dd className="font-medium text-gray-800">{pula(line.basic)}</dd>
                    </div>
                    <div className="flex justify-between">
                      <dt className="text-gray-500">Incentives</dt>
                      <dd className="font-medium text-gray-800">{pula(line.incentive)}</dd>
                    </div>
                    <div className="flex justify-between">
                      <dt className="text-gray-500">Commissions</dt>
                      <dd className="font-medium text-gray-800">{pula(line.commission)}</dd>
                    </div>
                    <div className="flex justify-between">
                      <dt className="text-gray-500">CTC</dt>
                      <dd className="text-right font-semibold text-[#0D1B2A]">
                        {pula(line.ctc)}
                        {line.source_currency && line.source_currency !== 'BWP' && (
                          <p className="text-xs font-normal text-gray-500">
                            {line.source_currency} {line.source_gross.toLocaleString('en-BW')} at {line.fx_rate}
                          </p>
                        )}
                      </dd>
                    </div>
                    <div className="flex items-center justify-between">
                      <dt className="text-gray-500">Tie-out</dt>
                      <dd className="flex items-center gap-2">
                        {line.tieout_ok ? (
                          <CheckCircle2 className="h-5 w-5 text-emerald-600" />
                        ) : (
                          <XCircle className="h-5 w-5 text-red-500" />
                        )}
                        {line.tieout_note && (
                          <span className="text-xs text-gray-500">{line.tieout_note}</span>
                        )}
                      </dd>
                    </div>
                  </dl>

                  {expandedCompany === line.company && (
                    <div className="mt-4 border-t border-gray-100 pt-3">
                      <p className="mb-2 text-[11px] uppercase tracking-[0.12em] text-gray-500">
                        Department breakdown
                      </p>
                      <div className="space-y-2">
                        {Object.entries(line.by_department)
                          .sort((a, b) => b[1].ctc - a[1].ctc)
                          .map(([department, breakdown]) => (
                            <div key={department} className="flex items-center justify-between text-sm">
                              <span className="text-gray-700">{department}</span>
                              <span className="font-medium text-[#0D1B2A]">
                                {breakdown.headcount} · {pula(breakdown.ctc)}
                              </span>
                            </div>
                          ))}
                      </div>

                      {line.company_id !== undefined && (
                        <div className="mt-4">
                          <Button
                            type="button"
                            variant="outline"
                            size="sm"
                            onClick={() => handleShowPeople(line.company_id!)}
                            disabled={peopleLoading && peopleCompanyId === line.company_id}
                          >
                            <Users className="mr-1 h-4 w-4" />
                            {peopleLoading && peopleCompanyId === line.company_id ? 'Loading…' : 'Show people'}
                          </Button>
                          {peopleError && peopleCompanyId === line.company_id && (
                            <p className="mt-2 text-sm text-gray-500">Could not load</p>
                          )}
                          {peopleForbidden && peopleCompanyId === line.company_id && (
                            <p className="mt-2 text-sm text-gray-500">Confidential — viewing is logged</p>
                          )}
                          {peopleData && peopleCompanyId === line.company_id && (
                            <div className="mt-3 overflow-x-auto">
                              <table className="w-full text-left text-sm">
                                <thead className="text-[11px] uppercase tracking-[0.12em] text-gray-500">
                                  <tr>
                                    <th className="p-2">Name</th>
                                    <th className="p-2">Department</th>
                                    <th className="p-2">Job title</th>
                                    <th className="p-2 text-right">Basic</th>
                                    <th className="p-2 text-right">Incentive</th>
                                    <th className="p-2 text-right">Commission</th>
                                    <th className="p-2 text-right">Gross</th>
                                    <th className="p-2 text-right">CTC</th>
                                  </tr>
                                </thead>
                                <tbody>
                                  {peopleData.rows.map((person) => (
                                    <tr key={person.name} className="border-t border-gray-100">
                                      <td className="p-2 font-medium text-[#0D1B2A]">{person.name}</td>
                                      <td className="p-2 text-gray-600">{person.department}</td>
                                      <td className="p-2 text-gray-600">{person.job_title}</td>
                                      <td className="p-2 text-right text-gray-600">{pula(person.basic)}</td>
                                      <td className="p-2 text-right text-gray-600">{pula(person.incentive)}</td>
                                      <td className="p-2 text-right text-gray-600">{pula(person.commission)}</td>
                                      <td className="p-2 text-right text-gray-600">{pula(person.gross)}</td>
                                      <td className="p-2 text-right font-semibold text-[#0D1B2A]">{pula(person.ctc)}</td>
                                    </tr>
                                  ))}
                                </tbody>
                              </table>
                              <p className="mt-2 text-xs text-gray-400">Confidential — viewing is logged</p>
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </>
        ) : null}
      </main>
    </div>
  );
}
