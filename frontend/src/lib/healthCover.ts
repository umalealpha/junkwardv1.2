/**
 * lib/healthCover.ts — Alpha Direct Health Care plan + benefit data.
 *
 * Ported from Steven Diaz's design-canvas mockup (2026-08-07) into Omni.
 *
 * ⚠️ EVERY FIGURE BELOW WAS TRANSCRIBED BY HAND from the Alpha Direct Health
 * Care Benefits Booklet 2025/26 plan tables. It has NOT been reconciled against
 * a machine-readable source, and the Health product owner has not signed it off
 * line by line. Treat it as UNVERIFIED until they do — the page says so on
 * screen. Do not quote these limits to a member or an employer until that
 * sign-off exists. Rows are [single, family].
 */

export const NC = 'Not covered';
export const PLANS = [
  { id: 'lite', name: 'Lite', price: '76', tag: 'Wellbeing services and foundational protection for emergency care and hospitalisation.' },
  { id: 'essential', name: 'Essential', price: '281', tag: 'Everyday healthcare access, backed by trusted urgent care and hospitalisation.' },
  { id: 'core', name: 'Core', price: '529', tag: 'Comprehensive protection supporting your health at every stage.' },
  { id: 'premier', name: 'Premier', price: '941', tag: 'An elevated level of health partnership.' },
  { id: 'status', name: 'Status', price: '1,448', tag: 'Extraordinary health experiences.' }
];
export const IDX = { lite: 0, essential: 1, core: 2, premier: 3, status: 4 };
const WELL = 'Wellness Programme';
const SUBHOSP = 'Subject to hospitalisation limit';
const SUBIN = 'Subject to overall inpatient limit';
const SUBOUT = 'Subject to overall outpatient limit';
const ALL = (v: string) => [v, v, v, v, v];

// Every figure below is transcribed from the Alpha Direct Health Care
// Benefits Booklet 2025/26 plan tables. Rows are [single, family].
export const REGIONS = {
  mind: {
    title: 'Mind & mental health',
    rows: [
      { name: 'Hospitalisation due to mental diseases', v: [NC, NC, ['20,000', '20,000'], ['40,000', '40,000'], ['60,000', '80,000']] },
      { name: 'Emotional wellbeing support', v: [NC, 'Covered', 'Covered', 'Covered', 'Covered'] }
    ]
  },
  eyes: {
    title: 'Eyes & vision',
    rows: [
      { name: 'Vision aids and optical consultation (per 24 months)', v: [NC, '3,500 per person', '5,000 per person', '6,500 per person', '6,500 per person'] },
      { name: 'Refractive laser surgery (LASIK)', v: [NC, NC, ['3,000', '3,600'], ['5,000', '6,000'], ['15,000', '20,000']] },
      { name: 'Glaucoma screening', v: ALL('Covered') }
    ]
  },
  teeth: {
    title: 'Teeth & jaw',
    rows: [
      { name: 'Dental consultation and treatment', v: [NC, ['25,000', '35,000'], ['40,000', '50,000'], ['60,000', '80,000'], ['60,000', '80,000']] },
      { name: 'Hospitalisation due to dental diseases', v: [NC, NC, SUBHOSP, ['80,000', '100,000'], ['150,000', '200,000']] },
      { name: 'Maxillofacial surgery in hospital', v: [NC, NC, ['15,000', '18,000'], SUBHOSP, 'Subject to dental hospitalisation limit'] }
    ]
  },
  heart: {
    title: 'Heart, chest & screening',
    rows: [
      { name: 'Screening for cardiovascular diseases', v: ALL('Covered') },
      { name: 'Cancer screening', v: ALL('Covered') },
      { name: 'Vaccination and malaria prophylaxis', v: ALL('Covered') },
      { name: 'Annual health assessment', v: ALL(WELL) }
    ],
    female: [
      { name: 'Breast health screening (mammogram, 40+)', v: ALL(WELL) }
    ]
  },
  blood: {
    title: 'Blood & chronic care',
    rows: [
      { name: 'Prescription and chronic medicines', v: [NC, ['10,000', '15,000'], ['20,000', '30,000'], ['40,000', '50,000'], SUBOUT] },
      { name: 'AlphaCare™ Chronic Disease Programme', v: [NC, 'Covered', 'Covered', 'Covered', 'Covered'] },
      { name: 'HIV pathology', v: [NC, '4,000 per person', '4,000 per person', '4,000 per person', '10,000 per person'] },
      { name: 'ARV medication', v: [NC, '15,000 per person', '15,000 per person', '15,000 per person', '20,000 per person'] },
      { name: 'Dread disease', v: [NC, NC, SUBIN, SUBIN, ['2,000,000', '3,000,000']] },
      { name: 'Blood count, diabetes, kidney and liver screening', v: ALL(WELL) }
    ]
  },
  bones: {
    title: 'Bones, joints & mobility',
    rows: [
      { name: 'Allied health', v: [NC, ['3,000', '5,000'], ['6,000', '10,000'], ['10,000', '15,000'], ['50,000', '75,000']] },
      { name: 'Alternative treatments', v: [NC, ['4,000', '6,000'], ['7,500', '11,000'], ['13,000', '20,000'], ['20,000', '30,000']] },
      { name: 'Prosthesis in hospital', v: [NC, NC, ['15,000', '18,000'], ['60,000', '75,000'], ['90,000', '130,000']] },
      { name: 'Osteoporosis screening', v: ALL('Covered') },
      { name: 'Bone density scan (per 2 years, from 40)', v: ALL(WELL) }
    ]
  },
  bodyWoman: {
    title: 'Maternity & women’s health',
    rows: [
      { name: 'Global maternity benefit', v: [NC, '20,000 per person', 'Covered', 'Covered', 'Covered'] },
      { name: 'Birthing unit', v: [NC, '1,000 per person', 'Covered', 'Covered', 'Covered'] },
      { name: 'Ultrasound scans', v: [NC, '2 scans (2D only)', '3 scans', '3 scans', '4 scans'] },
      { name: 'Antenatal classes', v: [NC, '4 classes', '4 classes', '4 classes', '4 classes'] },
      { name: 'AlphaMama™ Maternity Programme', v: [NC, 'Covered', 'Covered', 'Covered', 'Covered'] },
      { name: 'Contraceptive medication (per female 18+)', v: [NC, '1,000', '1,500', '2,000', '3,000'] },
      { name: 'Infertility diagnostic', v: [NC, NC, NC, '10,000', '75,000'] },
      { name: 'Cervical health screening (25+)', v: ALL(WELL) },
      { name: 'HPV vaccination (females 13–26)', v: ALL(WELL) }
    ]
  },
  bodyMan: {
    title: 'Men’s health',
    rows: [
      { name: 'Safe male circumcision (per male insured)', v: [NC, '2,000', '2,000', '2,000', '2,000'] },
      { name: 'Infertility diagnostic', v: [NC, NC, NC, '10,000', '75,000'] },
      { name: 'Prostate health screening (PSA test, 40+)', v: ALL(WELL) }
    ]
  }
};

export const LIMITS = {
  lite: { inS: '100,000', inF: '120,000', outS: NC, outF: NC },
  essential: { inS: '100,000', inF: '120,000', outS: '30,000', outF: '40,000' },
  core: { inS: '250,000', inF: '300,000', outS: '50,000', outF: '75,000' },
  premier: { inS: '1,000,000', inF: '1,200,000', outS: '100,000', outF: '150,000' },
  status: { inS: '2,000,000', inF: '3,000,000', outS: '150,000', outF: '200,000' }
};


export type PlanId = 'lite' | 'essential' | 'core' | 'premier' | 'status'
export type Cell = string | [string, string]
export interface BenefitRow { name: string; v: Cell[] }
export interface Region { title: string; rows: BenefitRow[]; female?: BenefitRow[] }

/** Hotspot positions as % of the figure box, per gender (from the mockup). */
export const HOTSPOTS: Record<'woman' | 'man', { key: string; x: number; y: number }[]> = {
  woman: [
    { key: 'mind',  x: 49.7, y: 6.4 },  { key: 'eyes',  x: 58.3, y: 12.5 },
    { key: 'teeth', x: 45.3, y: 16.6 }, { key: 'heart', x: 56.7, y: 32.3 },
    { key: 'blood', x: 23.0, y: 56.4 }, { key: 'body',  x: 50.3, y: 49.2 },
    { key: 'bones', x: 39.7, y: 71.4 },
  ],
  man: [
    { key: 'mind',  x: 50.7, y: 6.4 },  { key: 'eyes',  x: 57.0, y: 11.4 },
    { key: 'teeth', x: 45.3, y: 15.0 }, { key: 'heart', x: 60.3, y: 28.3 },
    { key: 'blood', x: 25.0, y: 56.4 }, { key: 'body',  x: 50.0, y: 49.2 },
    { key: 'bones', x: 39.3, y: 71.4 },
  ],
}

export const REGION_ORDER = ['mind', 'eyes', 'teeth', 'heart', 'blood', 'body', 'bones'] as const

/** 'body' resolves to the gender-specific region. */
export function regionFor(key: string, gender: 'woman' | 'man'): Region {
  if (key === 'body') return (REGIONS as Record<string, Region>)[gender === 'woman' ? 'bodyWoman' : 'bodyMan']
  return (REGIONS as Record<string, Region>)[key]
}

/** Add the Pula sign only to bare money values, never to words like 'Covered'. */
export const money = (s: string) => (/^[\d,]+( per person)?$/.test(s) ? 'P' + s : s)
