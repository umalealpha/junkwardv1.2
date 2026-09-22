/**
 * The /app routes that render for a SIGNED-OUT visitor — no token check, no
 * redirect to /app/login, no tab bar.
 *
 * This is a store-submission gate, not a convenience. Google Play requires a
 * privacy policy URL and Apple a support URL, and both are opened by a reviewer
 * who has no Alpha Direct account. A page that bounces them to a sign-in screen
 * fails the review, and nothing in the build can see it: TypeScript is perfectly
 * happy with a page the shell then redirects away from.
 */
export const PUBLIC_APP_ROUTES = ['/app/login', '/app/privacy', '/app/support', '/app/delete-account'] as const

const PUBLIC = new Set<string>(PUBLIC_APP_ROUTES)

/** True when `pathname` may be viewed without a device token. Trailing slashes
 * are ignored: '/app/privacy/' is the same page as '/app/privacy'. */
export function isPublicAppRoute(pathname: string | null | undefined): boolean {
  return PUBLIC.has((pathname || '/app').replace(/\/+$/, '') || '/app')
}
