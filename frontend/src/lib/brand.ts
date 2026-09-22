// Brand asset paths
export const BRAND = {
  logo: {
    full: '/brand/logo-full-color.png',
    white: '/brand/logo-monotone-white.png',
  },
  mascots: {
    boyDab: '/brand/mascots/boy-dab.png',
    boyStand: '/brand/mascots/boy-stand.png',
    boyMassive: '/brand/mascots/boy-massive.png',
    girlFly: '/brand/mascots/girl-fly.png',
    girlStand: '/brand/mascots/girl-stand.png',
    girlPose: '/brand/mascots/girl-pose.png',
    family: '/brand/mascots/family.png',
    // super-1 / super-3 (the "Superman" figures) removed 2026-06-04 — the
    // jumping mascot caused a headache glitch on the dashboard + loading screen.
  },
} as const;

// All mascot images as array for random selection
export const ALL_MASCOTS = Object.values(BRAND.mascots);
