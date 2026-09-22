import { describe, expect, it } from 'vitest'
import { matchBrokers } from '../brokerPicker'

const brokers = [
  { id: '1', name: 'Redhill', short_name: 'Redhill', is_active: true,
    aliases: ['Hilrange Enterprises t/a Redhill Risk', 'Hildrage Enterprises'] },
  { id: '2', name: 'Kgare Insurance Brokers', short_name: 'Kgare', is_active: true,
    aliases: ['Kgare Insurance Brokers (Pty) Ltd'] },
  { id: '3', name: 'Gone Broking', short_name: 'Gone', is_active: false,
    aliases: ['Hilrange Old Book'] },
]

describe('matchBrokers (C3 Add Policy typeahead)', () => {
  it('finds a broker by its canonical name', () => {
    expect(matchBrokers(brokers, 'kgare').map(m => m.broker.id)).toEqual(['2'])
  })
  it('finds a broker by a Graphite alias and says which alias matched', () => {
    const hits = matchBrokers(brokers, 'hildrage')
    expect(hits.map(m => m.broker.id)).toEqual(['1'])
    expect(hits[0].via).toBe('Hildrage Enterprises')
  })
  it('never offers an inactive broker, even when its alias matches', () => {
    expect(matchBrokers(brokers, 'hilrange').map(m => m.broker.id)).toEqual(['1'])
    expect(matchBrokers(brokers, 'gone')).toEqual([])
    expect(matchBrokers(brokers, '').map(m => m.broker.id)).toEqual(['1', '2'])
  })
  it('ignores case and surrounding whitespace', () => {
    expect(matchBrokers(brokers, '  REDHILL ').map(m => m.broker.id)).toEqual(['1'])
  })
  it('returns nothing for a name no broker carries — no free text', () => {
    expect(matchBrokers(brokers, 'Somebody New Ltd')).toEqual([])
  })
})
