/**
 * ifrs17-parity.mjs — the TS cockpit engine and the Python engine must agree to
 * the cent, at base AND off-base. Committed (not scratch) because Fable, 2026-08-25,
 * found that base-only parity certified nothing: the first lever drag was where the
 * twins diverged and a basis switch inverted the sign of profit.
 *
 * Run:  node --experimental-strip-types frontend/scripts/ifrs17-parity.mjs
 * CI/pre-ship: exit code is 0 only if every case matches the expected values,
 * which are the same ones ifrs17/tests/test_parity.py holds Python to.
 */
import { BASE, SEGMENT_IDS, compute } from '../src/lib/ifrs17Model.ts'

const on = new Set(SEGMENT_IDS)
let fails = 0
const near = (got, want, tol, label) => {
  if (!Number.isFinite(got) || Math.abs(got - want) > tol) {
    console.log(`FAIL ${label}: ${got} vs ${want}`); fails++
  } else console.log(`ok   ${label}`)
}

// Base -> the report.
const base = compute(BASE, on)
near(base.profitBeforeTax, 12_864_243, 2, 'base PBT')
near(base.insuranceContractLiabilities, 49_927_070, 1, 'base ICL')

// 🔴 Off-base, the sign test. RA 6%->8% must LOWER profit by 2% of the LIC BE.
const ra8 = compute({ ...BASE, raPct: 0.08 }, on)
near(ra8.profitBeforeTax, 12_864_243 - 626_001.92, 3, 'RA 8% LOWERS PBT by 626,002')
if (ra8.profitBeforeTax >= base.profitBeforeTax) {
  console.log('FAIL raising the risk adjustment raised profit — sign inverted'); fails++
} else console.log('ok   raising RA lowers profit')

// A tiny nudge must not jump the result.
const nudge = compute({ ...BASE, raPct: 0.06 + 1e-7 }, on)
near(Math.abs(nudge.profitBeforeTax - base.profitBeforeTax), 0, 10, 'tiny RA nudge no jump')

// Expense apportionment up -> profit down.
const attr95 = compute({ ...BASE, attributableSharePct: 0.95 }, on)
if (attr95.profitBeforeTax >= base.profitBeforeTax) { console.log('FAIL attr up raised profit'); fails++ }
else console.log('ok   raising expense apportionment lowers profit')

// JBB is the one lever that adds to profit (commission received) — unchanged.
const jbb = compute({ ...BASE, jbbCommissionPct: 0.41 }, on)
near(jbb.profitBeforeTax - base.profitBeforeTax, 8_712_090, 100, 'JBB 41% raises PBT')

console.log(fails === 0 ? '\nIFRS17 TS PARITY: PASS' : `\nIFRS17 TS PARITY: FAIL (${fails})`)
process.exit(fails === 0 ? 0 : 1)
