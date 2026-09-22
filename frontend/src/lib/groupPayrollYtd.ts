// Year-to-date cost to company for the Group Payroll Report.
// The financial year runs July to June (ADIC management accounts).

export interface CtcPoint {
  period: string; // YYYY-MM
  ctc: number | string;
}

export function financialYearStart(period: string): string {
  const [year, month] = period.split('-').map(Number);
  return `${month >= 7 ? year : year - 1}-07`;
}

/** Sum of saved months from the start of the financial year up to and including `period`. */
export function yearToDate(points: CtcPoint[], period: string): { total: number; months: number; from: string } {
  const from = financialYearStart(period);
  const inYear = points.filter((p) => p.period >= from && p.period <= period);
  return {
    total: inYear.reduce((sum, p) => sum + Number(p.ctc), 0),
    months: inYear.length,
    from,
  };
}
