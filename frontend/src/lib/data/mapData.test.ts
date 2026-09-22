import { describe, it, expect } from 'vitest'
import geo from './botswana-geo.json'
import providers from './providers.json'

// Guards the offline provider map: the data extracted from Steven's provider-map.html,
// and the direction of the dependency-free projection used by ProviderMap.tsx. If the
// extraction or the projection regresses, these go red.

const BBOX = { minLon: 19.9, maxLon: 29.4, minLat: -27.0, maxLat: -17.7 }

describe('provider-map data extraction', () => {
  it('carries the full Botswana geometry', () => {
    const g = geo as any
    expect(g.districts.length).toBe(10)
    expect(g.rivers.length).toBe(48)
    expect(g.pans.length).toBe(2)
    expect(g.country.length).toBeGreaterThan(0)
  })

  it('carries all 247 practices across 34 towns', () => {
    const p = providers as any
    expect(p.providers.length).toBe(247)
    expect(p.towns.length).toBe(34)
    expect(p.practiceCount).toBe(247)
  })

  it('every town + practice sits inside Botswana', () => {
    const p = providers as any
    for (const t of [...p.towns, ...p.providers]) {
      expect(t.lng).toBeGreaterThanOrEqual(BBOX.minLon)
      expect(t.lng).toBeLessThanOrEqual(BBOX.maxLon)
      expect(t.lat).toBeGreaterThanOrEqual(BBOX.minLat)
      expect(t.lat).toBeLessThanOrEqual(BBOX.maxLat)
    }
  })
})

// Mirror of ProviderMap's projection, to assert the map is not flipped/mirrored.
function rings(x: unknown, out: number[][][] = []): number[][][] {
  if (Array.isArray(x) && x.length && Array.isArray(x[0]) && typeof (x[0] as number[])[0] === 'number') out.push(x as number[][])
  else if (Array.isArray(x)) for (const e of x) rings(e, out)
  return out
}
function project() {
  const cpts: number[][] = []
  rings((geo as any).country).forEach(r => r.forEach(p => cpts.push(p)))
  const lons = cpts.map(p => p[0]), lats = cpts.map(p => p[1])
  const minLon = Math.min(...lons), maxLon = Math.max(...lons), minLat = Math.min(...lats), maxLat = Math.max(...lats)
  const cos = Math.cos(((minLat + maxLat) / 2) * Math.PI / 180)
  const W = 1000, scale = W / ((maxLon - minLon) * cos), H = (maxLat - minLat) * scale
  return { W, H, proj: (lon: number, lat: number) => [(lon - minLon) * cos * scale, (maxLat - lat) * scale] as [number, number] }
}

describe('projection orientation', () => {
  const { W, H, proj } = project()
  const town = (name: string) => (providers as any).towns.find((t: any) => t.town === name)

  it('Gaborone (south-east) lands in the lower-right quadrant', () => {
    const g = town('GABORONE'); expect(g).toBeTruthy()
    const [x, y] = proj(g.lng, g.lat)
    expect(x).toBeGreaterThan(W / 2)   // east
    expect(y).toBeGreaterThan(H / 2)   // south
  })

  it('Maun (north) lands in the upper half (north is not flipped to the bottom)', () => {
    const m = town('MAUN'); expect(m).toBeTruthy()
    const [, y] = proj(m.lng, m.lat)
    expect(y).toBeLessThan(H / 2)
  })
})
