import SwiftUI
import UserNotifications

@MainActor final class PushBridge: ObservableObject {
    static let shared = PushBridge()
    @Published var token = ""
    @Published var notice = ""
    @Published var failure = ""
}

final class AppDelegate: NSObject, UIApplicationDelegate, UNUserNotificationCenterDelegate {
    func application(_ application: UIApplication, didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
        UNUserNotificationCenter.current().delegate = self
        return true
    }
    func application(_ application: UIApplication, didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data) {
        let value = deviceToken.map { String(format: "%02x", $0) }.joined()
        Task { @MainActor in PushBridge.shared.token = value }
    }
    func application(_ application: UIApplication, didFailToRegisterForRemoteNotificationsWithError error: Error) {
        Task { @MainActor in PushBridge.shared.failure = "No se pudo registrar este iPhone. Revisa la conexión e inténtalo de nuevo." }
    }
    func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification,
                                withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void) {
        completionHandler([.banner, .sound, .badge])
    }
    func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse,
                                withCompletionHandler completionHandler: @escaping () -> Void) {
        let id = response.notification.request.content.userInfo["notification_id"] as? String ?? ""
        Task { @MainActor in PushBridge.shared.notice = id }
        completionHandler()
    }
}

@main struct FlowingApp: App {
    @UIApplicationDelegateAdaptor(AppDelegate.self) var delegate
    @StateObject private var flow = FlowSession()
    @StateObject private var bridge = PushBridge.shared
    @Environment(\.scenePhase) private var phase
    var body: some Scene {
        WindowGroup {
            RootView().environmentObject(flow)
                .tint(Color.accentColor)
                .environment(\.locale, Locale(identifier: "es_GT"))
                .environment(\.timeZone, TimeZone(identifier: "America/Guatemala")!)
                .onReceive(bridge.$token) { token in
                    if !token.isEmpty { Task { await flow.registerDevice(token) } }
                }
                .onChange(of: bridge.failure) { _, error in flow.error = error }
                .onChange(of: bridge.notice) { _, id in
                    guard !id.isEmpty, flow.connected else { return }
                    Task { await flow.openNotice(id); bridge.notice = "" }
                }
                .onChange(of: phase) { _, state in
                    if state == .active {
                        Task {
                            await flow.refresh()
                            if flow.connected && !bridge.notice.isEmpty { await flow.openNotice(bridge.notice); bridge.notice = "" }
                        }
                    }
                }
                .onChange(of: flow.connected) { _, connected in
                    if connected && !bridge.notice.isEmpty {
                        Task { await flow.openNotice(bridge.notice); bridge.notice = "" }
                    }
                }
        }
    }
}
