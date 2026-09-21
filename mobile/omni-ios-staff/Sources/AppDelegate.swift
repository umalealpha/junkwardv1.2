import UIKit
import UserNotifications

/// Omni — Alpha Direct staff app.
/// The screens are the live web app at omni.alphadirect.co.bw/app. Everything
/// the web cannot do on iPhone is done natively here: alerts (APNs), Face ID
/// unlock, and the camera/photo pickers.
@main
final class AppDelegate: UIResponder, UIApplicationDelegate {
    var window: UIWindow?

    func application(_ application: UIApplication,
                     didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]?) -> Bool {
        UNUserNotificationCenter.current().delegate = self

        let root = WebViewController()
        let window = UIWindow(frame: UIScreen.main.bounds)
        window.rootViewController = root
        window.backgroundColor = Brand.navy
        window.makeKeyAndVisible()
        self.window = window

        // A notification tapped from the lock screen opens the approvals list.
        if let info = launchOptions?[.remoteNotification] as? [AnyHashable: Any] {
            root.pendingDeepLink = PushRouter.path(for: info)
        }
        return true
    }

    // MARK: alerts

    /// Called once the staff member says yes to alerts, from the web page.
    func registerForPushNotifications() {
        UNUserNotificationCenter.current()
            .requestAuthorization(options: [.alert, .badge, .sound]) { granted, _ in
                guard granted else { return }
                DispatchQueue.main.async { UIApplication.shared.registerForRemoteNotifications() }
            }
    }

    func application(_ application: UIApplication,
                     didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data) {
        let token = deviceToken.map { String(format: "%02x", $0) }.joined()
        (window?.rootViewController as? WebViewController)?.deliverPushToken(token)
    }

    func application(_ application: UIApplication,
                     didFailToRegisterForRemoteNotificationsWithError error: Error) {
        NSLog("Omni: alert registration failed — %@", error.localizedDescription)
    }
}

extension AppDelegate: UNUserNotificationCenterDelegate {
    // Show the alert even while Omni is open.
    func userNotificationCenter(_ center: UNUserNotificationCenter,
                                willPresent notification: UNNotification,
                                withCompletionHandler completionHandler:
                                @escaping (UNNotificationPresentationOptions) -> Void) {
        completionHandler([.banner, .sound, .badge])
    }

    // Tapping an alert jumps to the right screen.
    func userNotificationCenter(_ center: UNUserNotificationCenter,
                                didReceive response: UNNotificationResponse,
                                withCompletionHandler completionHandler: @escaping () -> Void) {
        let path = PushRouter.path(for: response.notification.request.content.userInfo)
        (window?.rootViewController as? WebViewController)?.open(path: path)
        completionHandler()
    }
}

enum PushRouter {
    /// The server may name a screen in the payload; approvals is the sensible default
    /// because that is what almost every alert is about.
    static func path(for userInfo: [AnyHashable: Any]) -> String {
        if let p = userInfo["path"] as? String, p.hasPrefix("/app") { return p }
        return "/app/approve"
    }
}

enum Brand {
    static let navy = UIColor(red: 0x07/255, green: 0x14/255, blue: 0x26/255, alpha: 1)
    static let orange = UIColor(red: 0xF4/255, green: 0xA6/255, blue: 0x23/255, alpha: 1)
}
