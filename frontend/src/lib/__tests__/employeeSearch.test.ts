import { describe, expect, it } from 'vitest'
import { sameNameCount, searchEmployees } from '../employeeSearch'

const staff = [
  { eid: '1', nm: 'Kago Tshutlhedi', email: 'ktshutlhedi@alphadirect.co.bw', en: 'ADI-010' },
  { eid: '2', nm: 'Pako Kago', email: 'pkago@alphadirect.co.bw', en: 'ADI-011' },
  { eid: '3', nm: 'Same Name', email: 'same.one@alphadirect.co.bw', en: 'ADI-020' },
  { eid: '4', nm: 'Same Name', email: 'same.two@alphadirect.co.bw', en: 'ADI-021' },
  { eid: '', nm: 'No Record', email: 'none@alphadirect.co.bw', en: 'X' },
]

describe('searchEmployees', () => {
  it('finds by name, email and employee number', () => {
    expect(searchEmployees(staff, 'tshut').map(e => e.eid)).toEqual(['1'])
    expect(searchEmployees(staff, 'pkago@').map(e => e.eid)).toEqual(['2'])
    expect(searchEmployees(staff, 'adi-021').map(e => e.eid)).toEqual(['4'])
  })
  it('ranks an exact employee number or email first', () => {
    expect(searchEmployees(staff, 'ADI-011')[0].eid).toBe('2')
    expect(searchEmployees(staff, 'kago')[0].nm).toBe('Kago Tshutlhedi')
  })
  it('returns BOTH people who share a name — never collapses them', () => {
    expect(searchEmployees(staff, 'same name').map(e => e.eid).sort()).toEqual(['3', '4'])
  })
  it('never offers a row without an employee id', () => {
    expect(searchEmployees(staff, 'no record')).toEqual([])
  })
  it('counts same-name holders', () => {
    expect(sameNameCount(staff, 'same name')).toBe(2)
    expect(sameNameCount(staff, 'Pako Kago')).toBe(1)
  })
})
