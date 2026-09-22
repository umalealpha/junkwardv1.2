import { describe, expect, it } from 'vitest';
import { financialYearStart, yearToDate } from '../groupPayrollYtd';

describe('group payroll year to date', () => {
  it('starts the financial year in July', () => {
    expect(financialYearStart('2026-08')).toBe('2026-07');
    expect(financialYearStart('2026-07')).toBe('2026-07');
    expect(financialYearStart('2026-06')).toBe('2025-07');
  });

  it('sums only saved months from July to the chosen month', () => {
    const points = [
      { period: '2026-05', ctc: '100.00' },
      { period: '2026-06', ctc: '200.00' },
      { period: '2026-07', ctc: '300.50' },
      { period: '2026-08', ctc: '400.25' },
      { period: '2026-09', ctc: '999.00' },
    ];
    expect(yearToDate(points, '2026-08')).toEqual({ total: 700.75, months: 2, from: '2026-07' });
    expect(yearToDate(points, '2026-06').total).toBe(300);
  });
});
