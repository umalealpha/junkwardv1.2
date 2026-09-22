import { describe, expect, it } from 'vitest'
import { activeRowKey, deepestHref, routeModule, type NavModuleLike } from '../navActive'

const people: NavModuleLike = {
  key: 'people',
  sections: [
    { title: 'My HR', items: [{ label: 'My Leave', href: '/hris/leave' }, { label: 'My Profile', href: '/hris/profile' }] },
    { title: 'HR', items: [{ label: 'HRIS Home', href: '/hris' }, { label: 'Leave Admin', href: '/hris/leave' }] },
  ],
}
const hrAdminFirst: NavModuleLike = { key: 'admin', sections: [{ title: 'HR', items: [{ label: 'HRIS Home', href: '/hris' }] }] }
const all = (ms: NavModuleLike[]) => ms.flatMap(m => m.sections.flatMap(s => s.items.map(i => i.href)))

describe('the current route decides the menu', () => {
  it('picks the most specific link, not a prefix', () => {
    expect(deepestHref(all([people]), '/hris/profile')).toBe('/hris/profile')
    expect(deepestHref(all([people]), '/hris/profile/edit')).toBe('/hris/profile')
    expect(deepestHref(all([people]), '/hrisx')).toBe('')
  })

  it('opens the module holding that link, even if another module prefix-matches first', () => {
    const d = deepestHref(all([hrAdminFirst, people]), '/hris/profile')
    expect(routeModule([hrAdminFirst, people], d)).toBe('people')
  })

  it('lights one row when a page is listed twice', () => {
    expect(activeRowKey(people.sections, '/hris/leave')).toBe('My HR|/hris/leave|My Leave')
  })

  it('ignores query strings and header placeholders', () => {
    const m: NavModuleLike = { key: 'x', sections: [{ title: 'S', items: [{ label: 'H', href: '#', header: true }, { label: 'Q', href: '/a?tab=1' }] }] }
    expect(deepestHref(all([m]), '/a')).toBe('/a')
    expect(routeModule([m], '/a')).toBe('x')
  })
})
