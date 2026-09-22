/* Omni approvals Web Push service worker (CFO 2026-07-23 wow-feature).
 * Shows the nudge and, on tap, focuses an open approvals tab or opens one. */
self.addEventListener('push', function (event) {
  var data = {}
  try { data = event.data ? event.data.json() : {} } catch (e) { data = {} }
  var title = data.title || 'Omni'
  var options = {
    body: data.body || '',
    icon: '/icon-192.png',
    badge: '/icon-192.png',
    data: { url: data.url || '/app/approve' },
    vibrate: [80, 40, 80],
  }
  event.waitUntil(self.registration.showNotification(title, options))
})

self.addEventListener('notificationclick', function (event) {
  event.notification.close()
  // Default landing is the Omni staff app (CFO 2026-09-03); a payload `url` still wins.
  var url = (event.notification.data && event.notification.data.url) || '/app/approve'
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function (wins) {
      for (var i = 0; i < wins.length; i++) {
        if (wins[i].url.indexOf(url) !== -1 && 'focus' in wins[i]) return wins[i].focus()
      }
      if (self.clients.openWindow) return self.clients.openWindow(url)
    })
  )
})
