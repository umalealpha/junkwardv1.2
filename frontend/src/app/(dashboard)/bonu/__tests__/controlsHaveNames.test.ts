/**
 * Every form control and every icon-only button on a BONU screen must have a
 * name a person can perceive.
 *
 * Why a SOURCE scan rather than a browser audit. The QC battery runs axe
 * against the live pages and found 154 unnamed controls, which is how this got
 * noticed (CFO instruction, 9 Sep 2026: fix them across all BONU screens). But
 * axe can only judge what happens to be ON SCREEN at that moment, and it
 * missed a whole class: the "log a movement" row (only rendered when a case is
 * expanded, and the register was empty on prod), the bill allocation editor,
 * the schedule's in-cell editors, and its firm filter (hidden unless the sheet
 * has a firm column). Those were every bit as unnamed. A source scan sees the
 * code that has not run yet, needs no browser, no token and no deploy, and
 * runs in CI on every change.
 *
 * What counts as a name here mirrors what actually reaches a user:
 *   - `aria-label`               — the control names itself
 *   - `aria-labelledby`          — named by other visible text
 *   - `placeholder`              — a real, if weak, name for a text box, and
 *                                  what axe already accepts
 *   - `title`                     — the hover name, used by the icon buttons
 *   - `id="x"` **only when a `htmlFor="x"` exists in the same file**
 *   - `id={id}` **only when the control is inside a `<Field>`**, whose
 *                                  association the render tests below prove
 *   - being INSIDE a `<label>` that carries text (implicit labelling)
 *
 * An `id` on its own is NOT a name, and an earlier version of this scan
 * treated it as one — see the render tests below for how that was caught.
 *
 * If this test fails, do NOT add the file to an ignore list. Either wrap the
 * control in `<Field label="…">{(id) => <input id={id} …/>}</Field>` when it
 * has a visible label, or give it its own `aria-label` when it does not.
 */
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { Field } from '../_shared'

const BONU_DIR = join(__dirname, '..')

function tsxFiles(dir: string): string[] {
  const out: string[] = []
  for (const entry of readdirSync(dir)) {
    if (entry === '__tests__' || entry === 'node_modules') continue
    const full = join(dir, entry)
    if (statSync(full).isDirectory()) out.push(...tsxFiles(full))
    else if (entry.endsWith('.tsx')) out.push(full)
  }
  return out
}

/**
 * Attributes that give a control a perceivable name.
 *
 * `(^|[\s{])` and not `\b`: a hyphen is a word boundary, so `\btitle=` also
 * matches `data-title=` and `\bid=` matches `data-id=` — neither of which names
 * anything. The attribute has to start a token. (Fable, /fabe gate,
 * 9 Sep 2026; no BONU source relied on it, but the hole was real.)
 *
 * `id` is deliberately NOT in this list. An id only names a control when a
 * `<label htmlFor>` points at it, and an id on its own proves nothing —
 * `hasName()` below checks the pairing instead.
 */
const NAMED = /(^|[\s{])(aria-label|aria-labelledby|placeholder|title)\s*=/

/** The value of a hard-coded `id="..."`, or '' when there is none. */
const HARD_ID = /(^|[\s{])id\s*=\s*"([^"]+)"/

/** A generated id passed in from a wrapper, e.g. `id={id}` from <Field>. */
const EXPR_ID = /(^|[\s{])id\s*=\s*\{/

/**
 * Does this control have a name a person can perceive?
 *
 * The subtle case, and the reason this is a function rather than one regex:
 * an `id` is only a name if something points at it.
 *  - `id="x"` → there must be a `htmlFor="x"` in the same file.
 *  - `id={id}` → the id comes from a wrapper. The only wrapper that supplies
 *    one is the shared <Field>, whose association is proven separately by the
 *    render tests above, so this is trusted — but ONLY when the control really
 *    is inside a <Field>.
 */
function hasName(src: string, tag: string, pos: number): boolean {
  if (NAMED.test(tag)) return true
  const hard = HARD_ID.exec(tag)
  if (hard) return new RegExp(`htmlFor\\s*=\\s*"${hard[2].replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}"`).test(src)
  if (EXPR_ID.test(tag)) return insideField(src, pos)
  return false
}

/** Is the control at `pos` inside a `<Field label=...>` render-prop? */
function insideField(src: string, pos: number): boolean {
  const before = src.slice(Math.max(0, pos - 900), pos)
  const openAt = before.lastIndexOf('<Field')
  if (openAt === -1) return false
  return !before.slice(openAt).includes('</Field>')
}

/**
 * The opening tag starting at `from`, honouring nested braces so a JSX
 * expression containing `>` (an arrow function, a comparison) does not end the
 * tag early. Returns '' if the tag never closes.
 */
function openingTag(src: string, from: number): string {
  let depth = 0
  for (let i = from; i < src.length; i++) {
    const ch = src[i]
    if (ch === '{') depth++
    else if (ch === '}') depth--
    else if (ch === '>' && depth === 0) return src.slice(from, i + 1)
  }
  return ''
}

/**
 * Is the control at `pos` sitting INSIDE a <label> that carries text?
 *
 * That is implicit labelling, and it is the cleanest form there is: the words
 * name the control and clicking them focuses it, with no id to keep in step.
 * `legal/registers.tsx`, `capture/page.tsx` and the members date box all do it
 * this way, which is why the live audit scored those screens zero. A test that
 * demanded an `aria-label` on top would be pushing a redundant second copy of
 * the label text into correct code.
 */
function insideTextLabel(src: string, pos: number): boolean {
  const before = src.slice(Math.max(0, pos - 1200), pos)
  const openAt = before.lastIndexOf('<label')
  if (openAt === -1) return false
  // A </label> between that <label> and us means it closed already.
  if (before.slice(openAt).includes('</label>')) return false
  // It has to carry actual WORDS, not just wrap the control. `{...}` alone is
  // not text: `<label className={x}><input/></label>` used to pass this because
  // the className counted as content. Attributes are stripped first, so only
  // what a person would READ is considered — either literal letters or a JSX
  // expression sitting in the label's text position, e.g. `>{fl.label}<`.
  // (Fable, /fabe gate, 9 Sep 2026.)
  const afterOpenTag = before.slice(openAt).replace(/^<label[^>]*>/, '')
  const textOnly = afterOpenTag.replace(/<[^>]*>/g, ' ')
  const hasLetters = /[A-Za-z]{3}/.test(textOnly)
  const hasTextExpression = /\{\s*[A-Za-z_$][\w.$?[\]'"()\s|&:-]*\}/.test(textOnly)
  return hasLetters || hasTextExpression
}

function findAll(src: string, tag: string): { tag: string; line: number; pos: number }[] {
  const hits: { tag: string; line: number; pos: number }[] = []
  const re = new RegExp(`<${tag}[\\s>]`, 'g')
  let m: RegExpExecArray | null
  while ((m = re.exec(src)) !== null) {
    const open = openingTag(src, m.index)
    if (!open) continue
    hits.push({ tag: open, line: src.slice(0, m.index).split('\n').length, pos: m.index })
  }
  return hits
}

/**
 * Strip comments before scanning. Without this the scan reads the EXAMPLES in
 * its own guidance — the `<Field>` docstring in `_shared.tsx` shows an
 * `<input>` on purpose — and reports documentation as a defect.
 */
function stripComments(src: string): string {
  const blank = (m: string) => m.replace(/[^\n]/g, ' ')   // keep line numbers
  return src
    .replace(/\/\*[\s\S]*?\*\//g, blank)
    // A `//` that is not part of a URL (`https://`).
    .replace(/(^|[^:/])\/\/[^\n]*/g, (m, p1: string) => p1 + blank(m.slice(p1.length)))
}

const files = tsxFiles(BONU_DIR)

/**
 * The scan above accepts an `id=` on a control as proof it is named, because
 * the shared <Field> puts one there and ties its label to it. That trust has to
 * be EARNED, not assumed: if Field stopped emitting `htmlFor`, every control
 * would still carry an id, the scan would still pass, and nothing would be
 * labelled. (Found by reverting Field's association and watching the scan stay
 * green — the scan was the weak half, not the fix.) So render it and check.
 */
describe('the shared Field really ties its label to its control', () => {
  it('emits a label whose htmlFor matches the control id', () => {
    const html = renderToStaticMarkup(
      createElement(Field, {
        label: 'Date of loss',
        children: (id: string) => createElement('input', { id, type: 'date' }),
      }),
    )
    const forAttr = /<label[^>]*\sfor="([^"]+)"/.exec(html)?.[1]
    const idAttr = /<input[^>]*\sid="([^"]+)"/.exec(html)?.[1]
    expect(forAttr, `no htmlFor on the label:\n${html}`).toBeTruthy()
    expect(idAttr, `no id on the control:\n${html}`).toBeTruthy()
    expect(forAttr).toBe(idAttr)
    expect(html).toContain('Date of loss')
  })

  it('gives two Fields on one page different ids', () => {
    // Two controls sharing an id would make one label point at the other's box.
    const one = renderToStaticMarkup(
      createElement('div', null,
        createElement(Field, {
          label: 'A', key: 'a',
          children: (id: string) => createElement('input', { id }),
        }),
        createElement(Field, {
          label: 'B', key: 'b',
          children: (id: string) => createElement('input', { id }),
        })),
    )
    const ids = [...one.matchAll(/<input[^>]*\sid="([^"]+)"/g)].map(m => m[1])
    expect(ids).toHaveLength(2)
    expect(ids[0]).not.toBe(ids[1])
  })

  it('renders the hint when given one', () => {
    const html = renderToStaticMarkup(
      createElement(Field, {
        label: 'Claim received',
        hint: 'days-to-process counts from here',
        children: (id: string) => createElement('input', { id }),
      }),
    )
    expect(html).toContain('days-to-process counts from here')
  })
})

describe('BONU screens: every control has a name', () => {
  it('finds the BONU screens at all (a passing test over nothing is not a pass)', () => {
    expect(files.length).toBeGreaterThan(8)
  })

  for (const file of files) {
    const rel = file.slice(file.indexOf('bonu'))
    const src = stripComments(readFileSync(file, 'utf8'))

    it(`${rel}: inputs, selects and textareas are named`, () => {
      const unnamed: string[] = []
      for (const t of ['input', 'select', 'textarea']) {
        for (const hit of findAll(src, t)) {
          // A checkbox or radio wrapped INSIDE its <label> is named by the
          // label's own text, which is the cleanest form of all.
          if (/type="(checkbox|radio)"/.test(hit.tag)) continue
          if (/type="hidden"/.test(hit.tag)) continue
          // A pass-through wrapper (`<input {...props} />`) is not itself
          // unnamed — it takes whatever name its caller passes. The one in
          // `legal/registers.tsx` is additionally wrapped in its own <label>,
          // which is why that screen already scored zero on the live audit.
          if (/\{\s*\.\.\.\w+\s*\}/.test(hit.tag)) continue
          if (insideTextLabel(src, hit.pos)) continue
          if (!hasName(src, hit.tag, hit.pos)) {
            unnamed.push(`  ${rel}:${hit.line}  ${hit.tag.replace(/\s+/g, ' ').slice(0, 110)}`)
          }
        }
      }
      expect(unnamed.join('\n'), `unnamed control(s):\n${unnamed.join('\n')}`).toBe('')
    })

    it(`${rel}: icon-only buttons are named`, () => {
      const unnamed: string[] = []
      for (const hit of findAll(src, 'button')) {
        if (hasName(src, hit.tag, hit.pos)) continue
        // An icon-only button carries no words of its own. A body opening with
        // `<svg` counts as well as one opening with a capitalised component —
        // that was the hole (Fable, 9 Sep 2026).
        //
        // KNOWN LIMIT, chosen deliberately: a body that is ONLY an expression
        // (`{children}`, `{s.label}`, `{busy ? '…' : 'Save'}`) is not flagged.
        // Whether it renders a glyph or words cannot be known from the source,
        // and every such button in BONU today renders WORDS. Flagging them
        // would demand redundant aria-labels on correct code, and a check that
        // cries wolf is one people start ignoring — which costs more than the
        // rare `{icon}` button it would catch. The live axe pass covers that
        // case, because an expression HAS rendered by the time it runs.
        const bodyAt = hit.pos + hit.tag.length
        const body = src.slice(bodyAt, bodyAt + 200).replace(/^\s+/, '')
        const opensWithGlyph = /^(<[A-Z]|<svg[\s>])/.test(body)
        // ...and no readable text of its own before the button closes, so
        // `<Icon/> Save` is correctly left alone.
        const untilClose = body.split('</button>')[0]
        const words = untilClose.replace(/<[^>]*>/g, ' ').replace(/\{[^}]*\}/g, ' ')
        const iconOnly = opensWithGlyph && !/[A-Za-z]{2}/.test(words)
        if (iconOnly) {
          unnamed.push(`  ${rel}:${hit.line}  ${hit.tag.replace(/\s+/g, ' ').slice(0, 110)}`)
        }
      }
      expect(unnamed.join('\n'), `icon-only button(s) with no name:\n${unnamed.join('\n')}`).toBe('')
    })
  }
})
