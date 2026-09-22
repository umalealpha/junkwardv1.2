// Alpha Direct ERP — Page-contextual quotes library
// Minimum 15 quotes per major page, 5+ for sub-pages

const pageQuotes: Record<string, string[]> = {
  home: [
    "You're back! The books were getting lonely!",
    "Ready to save the day, one policy at a time!",
    "Welcome to HQ! The numbers await your command!",
    "Alpha team assembled! Let's go!",
    "The Pula stops here! Let's get to work!",
    "Insurance heroes never rest... but coffee helps!",
    "Look who's here! The finance department just got stronger!",
    "Botswana's finest financial heroes at your service!",
    "Every great insurance company starts with a great login!",
    "Dumela! Ready to make some financial magic happen?",
    "The Alpha squad has been waiting for you!",
    "Finance mode: ACTIVATED! Let's go!",
    "Back at it again! The ledger missed you!",
    "The financial fortress awaits its commander!",
    "Cape on, calculator ready — let's do this!",
    "Another day, another chance to balance the books!",
    "The hero returns! Gaborone's finest is back!",
    "Re Dumeditse! The finance engine is humming!",
    "Grab your cape — there are numbers to crunch!",
    "Welcome back, champion! The reports are fresh!",
  ],

  dashboard: [
    "Let me check the numbers... looking MAAASSSIVE!",
    "Counting every last thebe! Almost done...",
    "Dashboard time! Let's review the financial kingdom!",
    "Numbers looking strong! The shield is proud!",
    "Pulling the latest data — hang on to your cape!",
    "Financial overview coming in hot!",
    "Every Pula accounted for. Every thebe tracked!",
    "The squad ran the numbers — you're gonna love this!",
    "Dashboard powered up! Let's see what we're working with!",
    "Gross written premium loading... and it's looking GOOD!",
    "Alpha Boy says: these numbers are worth dabbing for!",
    "Claims vs premiums — let the battle begin!",
    "Cash position check: we're still flying high!",
    "Reinsurance recovery? That's money coming HOME!",
    "The finance dashboard: where heroes check their stats!",
    "Loading your financial superpowers...",
  ],

  invoices: [
    "Ka-ching! Let's see who owes us!",
    "Every invoice tells a story. This one says: PAY ME!",
    "Sorting invoices... alphabetically or by vibes?",
    "Invoice time! Time to make it rain (Pula)!",
    "The superhero's real power? Getting paid on time!",
    "Overdue invoices beware — the squad is HERE!",
    "Faster invoicing, faster payments, faster service!",
    "Let's turn those drafts into Posted invoices!",
    "Alpha Lady says: no invoice goes unfollowed!",
    "Bill them! Bill them all! (Professionally, of course.)",
    "INV-2026-AWESOME coming right up!",
    "Dear customer: please pay. Love, the AD squad.",
    "Premium invoices are our love language.",
    "The invoice list: a superhero's to-do list!",
    "Time to chase that money! Capes ready!",
  ],

  banking: [
    "That's... MAAASSSIVE! Let's reconcile!",
    "Matching transactions like a hero matches capes to outfits!",
    "Let's make these bank lines behave!",
    "Bank recon mode: ACTIVATED!",
    "Every cent must be accounted for. Every thebe!",
    "FNB statements? We eat those for breakfast!",
    "Auto-matching engaged! Stand back!",
    "The shield protects your bank balance!",
    "Unmatched lines? Not on our watch!",
    "Reconciliation is our middle name! (Not really, but still.)",
    "Statement imported! Time to match and dispatch!",
    "Alpha Boy vs unmatched transactions: Round 1!",
    "Balancing the books, one bank line at a time!",
    "The squad never leaves a transaction unmatched!",
    "Bank recon: boring for others, heroic for us!",
  ],

  "quick-entry": [
    "Quick entry! Because superheroes don't wait!",
    "Faster, cheaper, smarter entry!",
    "Zap! Your transaction is almost done!",
    "Speed mode: ON! Let's record this!",
    "Quick like lightning, accurate like a laser!",
    "Alpha Lady doesn't have time for slow forms!",
    "One entry, two entry, three entry — done!",
    "The fastest entry in all of Botswana!",
    "Quick entry: saving you time since 2026!",
    "Need speed? You came to the right page!",
    "Blink and you'll miss it — that's how fast we are!",
    "The express lane of financial transactions!",
    "No capes required for quick entry... but they help!",
    "Record it, post it, done. Superhero style!",
    "Alpha Direct: where quick doesn't mean careless!",
  ],

  settings: [
    "Welcome to HQ — handle with care!",
    "The engine room of Alpha Direct. Mind the buttons!",
    "Where the real magic happens behind the scenes.",
    "Settings: the superhero's utility belt!",
    "Chart of accounts? Audit log? We've got it all!",
    "Careful in here — great power, great responsibility!",
    "Alpha Boy says: only admins beyond this point!",
    "System configuration mode: engaged!",
    "The backbone of your financial system lives here!",
    "Fiscal periods, tax rates, users — the holy trinity!",
    "This is where finance admins become finance heroes!",
    "Settings page: no cape required, just admin access!",
    "Configure wisely, young hero!",
    "The control room of the AD financial universe!",
    "Behind every great dashboard is a well-configured settings page!",
  ],

  "trial-balance": [
    "Debits = Credits. That's the superhero's creed!",
    "Trial balance loading... please be balanced!",
    "If it doesn't balance, Alpha Boy will find out why!",
    "Opening + movements = closing. Simple as that!",
    "The ultimate test: does it balance?",
    "Trial balance: where every account tells its truth!",
    "Alpha Lady checks the TB before anyone else!",
    "A balanced TB is a happy TB!",
    "Dr + Cr = Harmony in the financial universe!",
  ],

  "profit-loss": [
    "Revenue minus expenses = the moment of truth!",
    "P&L time! Please be profitable, please be profitable...",
    "Alpha Boy says: keep revenue UP, expenses DOWN!",
    "The profit dance is about to begin!",
    "Income statement loading... fingers crossed!",
    "Gross result looking good? Time to celebrate!",
    "Net profit is the real superhero here!",
    "P&L: the report every CFO reads first!",
    "Revenue is vanity, profit is sanity!",
  ],

  "balance-sheet": [
    "Assets = Liabilities + Equity. The golden equation!",
    "Balance sheet: the financial snapshot of truth!",
    "Alpha Lady says: assets strong, equity growing!",
    "A=L+E — the superhero's formula!",
    "Checking the health of the company, one account at a time!",
    "Total equity looking solid? That's the goal!",
  ],

  "ar-aging": [
    "Who owes us? Let's find out!",
    "AR Aging: where overdue invoices can't hide!",
    "Current, 31-60, 61-90, 90+... the buckets of destiny!",
    "Alpha Boy is coming for those overdue payments!",
    "Receivables aging? More like receivables hunting!",
  ],

  "ap-aging": [
    "What do WE owe? Time to check!",
    "AP Aging: paying our heroes (vendors) on time!",
    "Vendor bills waiting patiently... or not so patiently!",
    "The squad always pays its debts!",
  ],

  "cash-position": [
    "Show me the money! All of it!",
    "Cash is king! Let's see the kingdom!",
    "BWP balance check: how's our vault looking?",
    "Alpha Boy guards the cash with his life!",
    "Cash position: the heartbeat of the business!",
  ],

  exceptions: [
    "Every exception tells a story — let's investigate!",
    "The CFO's radar is locked on!",
    "Exceptions found? Nothing gets past the Alpha squad!",
    "Flagging the unusual — that's what heroes do!",
    "Exception hunting mode: ACTIVATED!",
    "Red flags? Orange flags? We catch them ALL!",
    "The Alpha squad never ignores a warning sign!",
  ],

  "tax-calendar": [
    "BURS deadlines wait for no hero!",
    "VAT, PAYE, WHT — the tax trio!",
    "Tax calendar: because penalties are the real villain!",
    "Stay compliant, stay heroic!",
    "Due dates approaching? The squad is on it!",
  ],

  default: [
    "Another page, another adventure!",
    "Shield Hero is watching over your data!",
    "Faster, cheaper, smarter insurance!",
    "The AD family has your back!",
    "Let's do this! One Pula at a time!",
    "Cape: on. Mask: ready. Let's go!",
    "Every page is a new mission for the squad!",
    "Loading with superhero speed!",
    "Alpha Direct: where insurance meets awesome!",
    "The financial universe at your fingertips!",
    "Heroes don't skip pages — they conquer them!",
    "Botswana's insurance heroes, at your service!",
  ],
};

// KPI card-specific quotes
export const kpiQuotes: Record<string, string[]> = {
  "Total Cash": [
    "Cash reserves looking healthy!",
    "That's... MAAASSSIVE!",
    "The vault is strong!",
    "Ka-ching! Sweet cash!",
  ],
  "Gross Written Premium": [
    "Premiums looking strong!",
    "Writing policies like a boss!",
    "GWP is the hero metric!",
    "Keep writing, keep earning!",
  ],
  "Total Premiums Collected": [
    "Collections on track! Ka-ching!",
    "Keep the Pula flowing!",
    "Collected and accounted for!",
    "Money in, shield up!",
  ],
  "Total Claims": [
    "Claims creeping up... watch this one!",
    "Nothing the squad can't handle!",
    "Claims managed, risk controlled!",
    "Stay vigilant, heroes!",
  ],
  "Reinsurance Ceded": [
    "Spreading risk wisely!",
    "Reinsurance doing its job!",
    "Smart risk management!",
    "Shield shared, risk halved!",
  ],
  "Reinsurance Recoveries": [
    "Getting some back! Nice!",
    "Recoveries coming through!",
    "Reinsurers paying up!",
    "Money coming home!",
  ],
};

/** Pick a random quote for the given page key (falls back to `default`). */
export function getQuote(page: string): string {
  const quotes = pageQuotes[page] ?? pageQuotes.default;
  return quotes[Math.floor(Math.random() * quotes.length)];
}

/** Map a pathname to a quote page key. Shared between TopBar and dashboard layout. */
export function getQuotePageKey(pathname: string): string {
  if (pathname === '/dashboard' || pathname === '/') return 'dashboard'
  if (pathname.startsWith('/invoices')) return 'invoices'
  if (pathname.startsWith('/banking')) return 'banking'
  if (pathname.startsWith('/quick-entry')) return 'quick-entry'
  if (pathname.startsWith('/settings') || pathname.startsWith('/accounts')) return 'settings'
  if (pathname.startsWith('/tax-calendar')) return 'tax-calendar'
  if (pathname.startsWith('/reports/trial-balance')) return 'trial-balance'
  if (pathname.startsWith('/reports/profit-loss')) return 'profit-loss'
  if (pathname.startsWith('/reports/balance-sheet')) return 'balance-sheet'
  if (pathname.startsWith('/reports/ar-aging')) return 'ar-aging'
  if (pathname.startsWith('/reports/ap-aging')) return 'ap-aging'
  if (pathname.startsWith('/reports/cash-position')) return 'cash-position'
  if (pathname.startsWith('/reports/exceptions')) return 'exceptions'
  if (pathname.startsWith('/reports')) return 'default'
  return 'default'
}
