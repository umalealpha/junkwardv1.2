import UIKit
import WebKit
import LocalAuthentication

/// Hosts the live Omni staff app and bridges it to the things only a native
/// app can do on iPhone: alerts, Face ID, and the share sheet.
final class WebViewController: UIViewController {

    static let origin = "https://omni.alphadirect.co.bw"
    static let start = URL(string: origin + "/app")!

    private var web: WKWebView!
    private let refresher = UIRefreshControl()
    private var offline: OfflineView!
    /// Off by default. A staff member turns it on in the app; forcing Face ID on
    /// everyone locks out anyone whose face fails and blocks a store reviewer.
    private static let lockKey = "omni.lockEnabled"
    private var lockEnabled: Bool { UserDefaults.standard.bool(forKey: Self.lockKey) }
    private var locked = false
    private var lockCover: UIView?

    var pendingDeepLink: String?

    // MARK: lifecycle

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = Brand.navy
        buildWebView()
        buildOfflineView()
        locked = lockEnabled
        if locked { coverForLock() }

        var url = Self.start
        if let p = pendingDeepLink, let u = URL(string: Self.origin + p) { url = u; pendingDeepLink = nil }
        web.load(URLRequest(url: url))

        NotificationCenter.default.addObserver(
            self, selector: #selector(appBecameActive),
            name: UIApplication.didBecomeActiveNotification, object: nil)
    }

    override var preferredStatusBarStyle: UIStatusBarStyle { .lightContent }

    // MARK: web view

    private func buildWebView() {
        let controller = WKUserContentController()
        controller.add(self, name: "omni")
        // Tell the page it is inside the native app, so it can hide the
        // "add to Home Screen" hint and route alerts through us.
        let flag = WKUserScript(
            source: "window.OmniNative = { platform: 'ios', version: '1.0.0' };",
            injectionTime: .atDocumentStart, forMainFrameOnly: false)
        controller.addUserScript(flag)

        let config = WKWebViewConfiguration()
        config.userContentController = controller
        config.allowsInlineMediaPlayback = true
        config.mediaTypesRequiringUserActionForPlayback = []

        web = WKWebView(frame: .zero, configuration: config)
        web.navigationDelegate = self
        web.uiDelegate = self
        web.allowsBackForwardNavigationGestures = true
        web.scrollView.contentInsetAdjustmentBehavior = .never
        web.backgroundColor = Brand.navy
        web.isOpaque = false
        web.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(web)
        NSLayoutConstraint.activate([
            web.topAnchor.constraint(equalTo: view.topAnchor),
            web.bottomAnchor.constraint(equalTo: view.bottomAnchor),
            web.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            web.trailingAnchor.constraint(equalTo: view.trailingAnchor),
        ])

        refresher.tintColor = Brand.orange
        refresher.addTarget(self, action: #selector(pullToRefresh), for: .valueChanged)
        web.scrollView.refreshControl = refresher
    }

    @objc private func pullToRefresh() { web.reload() }

    private func buildOfflineView() {
        offline = OfflineView { [weak self] in
            self?.offline.isHidden = true
            self?.web.load(URLRequest(url: Self.start))
        }
        offline.isHidden = true
        offline.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(offline)
        NSLayoutConstraint.activate([
            offline.topAnchor.constraint(equalTo: view.topAnchor),
            offline.bottomAnchor.constraint(equalTo: view.bottomAnchor),
            offline.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            offline.trailingAnchor.constraint(equalTo: view.trailingAnchor),
        ])
    }

    // MARK: Face ID

    /// Approvals and payslips are private. If the phone is passed to someone
    /// else, the screen behind the cover is never visible until Face ID passes.
    private func coverForLock() {
        guard lockCover == nil else { return }
        let cover = UIView()
        cover.backgroundColor = Brand.navy
        let label = UILabel()
        label.text = "Alpha Omni"
        label.textColor = .white
        label.font = UIFont(name: "Palatino-Roman", size: 44) ?? .systemFont(ofSize: 44)
        label.translatesAutoresizingMaskIntoConstraints = false
        cover.addSubview(label)
        NSLayoutConstraint.activate([
            label.centerXAnchor.constraint(equalTo: cover.centerXAnchor),
            label.centerYAnchor.constraint(equalTo: cover.centerYAnchor),
        ])
        cover.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(cover)
        NSLayoutConstraint.activate([
            cover.topAnchor.constraint(equalTo: view.topAnchor),
            cover.bottomAnchor.constraint(equalTo: view.bottomAnchor),
            cover.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            cover.trailingAnchor.constraint(equalTo: view.trailingAnchor),
        ])
        lockCover = cover
    }

    @objc private func appBecameActive() { unlockIfNeeded() }

    private func unlockIfNeeded() {
        guard locked, lockEnabled else { revealApp(); return }
        let context = LAContext()
        var error: NSError?
        guard context.canEvaluatePolicy(.deviceOwnerAuthentication, error: &error) else {
            // No passcode set on the device — nothing to check against.
            locked = false; revealApp(); return
        }
        context.evaluatePolicy(.deviceOwnerAuthentication,
                               localizedReason: "Unlock Omni") { [weak self] ok, _ in
            DispatchQueue.main.async {
                guard let self else { return }
                if ok { self.locked = false; self.revealApp() }
            }
        }
    }

    private func revealApp() {
        UIView.animate(withDuration: 0.25, animations: { self.lockCover?.alpha = 0 }) { _ in
            self.lockCover?.isHidden = true
        }
    }

    // MARK: bridge out to the page

    func open(path: String) {
        guard let url = URL(string: Self.origin + path) else { return }
        web.load(URLRequest(url: url))
    }

    func deliverPushToken(_ token: String) {
        let js = "window.dispatchEvent(new CustomEvent('omni-push-token',{detail:{token:'\(token)',platform:'ios'}}));"
        web.evaluateJavaScript(js)
    }
}

// MARK: - messages from the page

extension WebViewController: WKScriptMessageHandler {
    func userContentController(_ userContentController: WKUserContentController,
                               didReceive message: WKScriptMessage) {
        guard let body = message.body as? [String: Any],
              let action = body["action"] as? String else { return }
        switch action {
        case "enableAlerts":
            (UIApplication.shared.delegate as? AppDelegate)?.registerForPushNotifications()
        case "share":
            guard let text = body["text"] as? String else { return }
            let sheet = UIActivityViewController(activityItems: [text], applicationActivities: nil)
            sheet.popoverPresentationController?.sourceView = view
            present(sheet, animated: true)
        case "setLock":
            let on = body["on"] as? Bool ?? false
            UserDefaults.standard.set(on, forKey: Self.lockKey)
        case "badge":
            let count = body["count"] as? Int ?? 0
            UNUserNotificationCenter.current().setBadgeCount(count)
        default:
            break
        }
    }
}

// MARK: - navigation rules

extension WebViewController: WKNavigationDelegate, WKUIDelegate {

    /// Omni's own pages open in the app. Anything else — a supplier's website,
    /// a mailto link — goes out to Safari or Mail, so the app never becomes a
    /// general web browser.
    func webView(_ webView: WKWebView,
                 decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = navigationAction.request.url else { decisionHandler(.allow); return }
        if let scheme = url.scheme, ["mailto", "tel", "sms"].contains(scheme) {
            UIApplication.shared.open(url); decisionHandler(.cancel); return
        }
        if url.absoluteString.hasPrefix(Self.origin) || url.scheme == "about" {
            decisionHandler(.allow); return
        }
        UIApplication.shared.open(url)
        decisionHandler(.cancel)
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        refresher.endRefreshing()
        offline.isHidden = true
        unlockIfNeeded()
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        refresher.endRefreshing()
        showOffline(error)
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        refresher.endRefreshing()
        showOffline(error)
    }

    private func showOffline(_ error: Error) {
        guard (error as NSError).code != NSURLErrorCancelled else { return }
        offline.isHidden = false
        lockCover?.isHidden = true
        locked = false
    }

    // window.open(...) — keep it in the same view rather than losing it.
    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for navigationAction: WKNavigationAction,
                 windowFeatures: WKWindowFeatures) -> WKWebView? {
        if navigationAction.targetFrame == nil, let url = navigationAction.request.url {
            if url.absoluteString.hasPrefix(Self.origin) { webView.load(URLRequest(url: url)) }
            else { UIApplication.shared.open(url) }
        }
        return nil
    }
}
