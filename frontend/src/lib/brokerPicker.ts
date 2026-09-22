/**
 * C3 — the broker typeahead inside Add Policy.
 *
 * Searches a broker's canonical name, its short name AND every Graphite alias,
 * because Finance knows brokers by the tab name ("Redhill") while Graphite files
 * them as "Hilrange Enterprises t/a Redhill". Inactive brokers are never
 * offered. The result is always a real broker record — there is no free-text
 * path, so a typed name can never reach the register.
 */
export interface PickableBroker {
  id: string
  name: string
  short_name: string
  is_active: boolean
  aliases: string[]
}

export interface BrokerMatch<B extends PickableBroker = PickableBroker> {
  broker: B
  /** The alias that matched, when it was not the broker's own name. */
  via: string | null
}

const norm = (s: string) => s.toLowerCase().replace(/\s+/g, ' ').trim()

export function matchBrokers<B extends PickableBroker>(
  brokers: B[], query: string, limit = 8,
): BrokerMatch<B>[] {
  const q = norm(query)
  const active = brokers.filter(b => b.is_active)
  if (!q) return active.slice(0, limit).map(broker => ({ broker, via: null }))
  const out: { m: BrokerMatch<B>; rank: number }[] = []
  for (const b of active) {
    const own = [b.name, b.short_name].map(norm)
    if (own.some(n => n.startsWith(q))) { out.push({ m: { broker: b, via: null }, rank: 0 }); continue }
    if (own.some(n => n.includes(q))) { out.push({ m: { broker: b, via: null }, rank: 1 }); continue }
    const alias = b.aliases.find(a => norm(a).includes(q))
    if (alias) out.push({ m: { broker: b, via: alias }, rank: 2 })
  }
  return out
    .sort((a, b) => a.rank - b.rank || a.m.broker.name.localeCompare(b.m.broker.name))
    .slice(0, limit)
    .map(x => x.m)
}
