import Foundation
import SwiftUI
import Security
import UserNotifications

private struct Pair: Decodable { let device_secret: String; let code: String }
private struct PairResult: Decodable { let status: String; let token: String? }
private struct Target: Decodable { let record: CRMRecord? }
private struct PreferenceResult: Decodable { let preferences: [String: Bool] }
private struct EmptyResponse: Decodable {}
private struct DeviceStatus: Decodable { let push_enabled: Bool; let hide_details: Bool; let revision: Int }
private struct DeviceResult: Decodable { let revision: Int }

enum FlowError: LocalizedError {
    case message(String)
    var errorDescription: String? { if case .message(let value) = self { return value }; return nil }
}

enum TokenVault {
    static let service = "Flowing.Mobile.Session"
    static func query(_ host: String) -> [String: Any] {
        [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service,
         kSecAttrAccount as String: host]
    }
    static func read(_ host: String) -> String? {
        var attributes = query(host)
        attributes[kSecReturnData as String] = true
        var result: CFTypeRef?
        guard SecItemCopyMatching(attributes as CFDictionary, &result) == errSecSuccess,
              let data = result as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }
    static func save(_ token: String, host: String) throws {
        remove(host)
        var attributes = query(host)
        attributes[kSecValueData as String] = Data(token.utf8)
        attributes[kSecAttrAccessible as String] = kSecAttrAccessibleWhenUnlockedThisDeviceOnly
        guard SecItemAdd(attributes as CFDictionary, nil) == errSecSuccess else {
            throw FlowError.message("No se pudo guardar la sesión de forma segura. Vuelve a conectar.")
        }
    }
    static func remove(_ host: String) { SecItemDelete(query(host) as CFDictionary) }
}

// Never follows a redirect with an Authorization header or forwards it to a login page.
final class NoRedirect: NSObject, URLSessionTaskDelegate {
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) { completionHandler(nil) }
}

@MainActor final class FlowSession: ObservableObject {
    @Published var serverText = UserDefaults.standard.string(forKey: "FlowingServer") ?? ""
    @Published var connected = false
    @Published var pairingCode = ""
    @Published var connecting = false
    @Published var overview: Overview?
    @Published var notices: [Notice] = []
    @Published var unread = 0
    @Published var nextNotice: Int?
    @Published var preferences: [String: Bool] = [:]
    @Published var error: String?
    @Published var message: String?
    @Published var selectedRecord: CRMRecord?
    @Published var hideDetails = true
    @Published var pushActive = false
    @Published var busy = false
    private var token: String?
    private var generation = 0
    private var disconnecting = false
    private var registrationGeneration: Int?
    private var registeredGeneration: Int?
    private var registrationInteractive = false
    private var deviceGeneration = 0
    private var deviceRevision = 0
    private var pairingTask: Task<Void, Never>?
    private let transport = URLSession(configuration: .ephemeral, delegate: NoRedirect(), delegateQueue: nil)
    var base: URL? { MobileURL.server(serverText) }

    init() {
        if let base { token = TokenVault.read(base.absoluteString); connected = token != nil }
    }

    func call<T: Decodable>(_ path: String, method: String = "GET", body: [String: Any]? = nil) async throws -> T {
        guard let base, let url = MobileURL.endpoint(base: base, path: path) else {
            throw FlowError.message("Introduce la dirección HTTPS de tu Flowing.")
        }
        let requestGeneration = generation
        let requestToken = token
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if let token { request.setValue("Bearer " + token, forHTTPHeaderField: "Authorization") }
        if let body {
            request.httpBody = try JSONSerialization.data(withJSONObject: body)
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        let (data, response) = try await transport.data(for: request)
        guard !Task.isCancelled, requestGeneration == generation, requestToken == token, self.base == base else {
            throw CancellationError()
        }
        guard let http = response as? HTTPURLResponse else { throw FlowError.message("El servidor no respondió. Reintenta.") }
        if http.statusCode == 401 && connected {
            forgetSession()
        }
        guard (200...299).contains(http.statusCode) else {
            let details = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
            throw FlowError.message(details?["error"] as? String ?? "No se pudo completar la solicitud. Reintenta.")
        }
        return try JSONDecoder().decode(T.self, from: data)
    }

    func startPairing() async {
        guard base != nil else { error = "Introduce la dirección HTTPS de tu Flowing."; return }
        cancelPairing()
        connecting = true
        error = nil
        do {
            let pair: Pair = try await call("/api/mobile/pair/start", method: "POST", body: [:])
            pairingCode = pair.code
            pairingTask = Task { [weak self] in
                guard let self else { return }
                do {
                    for _ in 0..<120 {
                        try await Task.sleep(for: .seconds(5))
                        let result: PairResult = try await call("/api/mobile/pair/poll", method: "POST", body: ["device_secret": pair.device_secret])
                        if let newToken = result.token {
                            token = newToken
                            generation += 1
                            do { try TokenVault.save(newToken, host: base!.absoluteString) }
                            catch {
                                let _: EmptyResponse? = try? await call("/api/mobile/logout", method: "POST", body: [:])
                                token = nil
                                throw error
                            }
                            UserDefaults.standard.set(serverText, forKey: "FlowingServer")
                            connected = true
                            connecting = false
                            pairingCode = ""
                            await refresh()
                            return
                        }
                    }
                    throw FlowError.message("El código venció. Genera uno nuevo.")
                } catch is CancellationError { }
                catch { report(error) }
                connecting = false
            }
        } catch { report(error); connecting = false }
    }

    func cancelPairing() { pairingTask?.cancel(); pairingTask = nil; connecting = false; pairingCode = "" }

    func refresh() async {
        guard connected && !disconnecting else { return }
        let currentGeneration = generation
        let currentDeviceGeneration = deviceGeneration
        error = nil
        busy = true
        defer { busy = false }
        do {
            overview = try await call("/api/mobile/overview")
            let inbox: Inbox = try await call("/api/mobile/notifications")
            notices = inbox.notifications; unread = inbox.count; nextNotice = inbox.next_before
            let pref: PreferenceResult = try await call("/api/mobile/preferences")
            preferences = pref.preferences
            let device: DeviceStatus = try await call("/api/mobile/device")
            guard currentDeviceGeneration == deviceGeneration else { return }
            pushActive = device.push_enabled; hideDetails = device.hide_details
            deviceRevision = device.revision
            try? await UNUserNotificationCenter.current().setBadgeCount(unread)
            if pushActive && registeredGeneration != generation {
                let permission = await UNUserNotificationCenter.current().notificationSettings()
                if currentGeneration == generation && currentDeviceGeneration == deviceGeneration && connected
                    && pushActive && !disconnecting && permission.authorizationStatus == .authorized {
                    registrationGeneration = generation
                    registrationInteractive = false
                    UIApplication.shared.registerForRemoteNotifications()
                }
            }
        } catch { report(error) }
    }

    func loadMoreNotices() async {
        guard let nextNotice else { return }
        do {
            let page: Inbox = try await call("/api/mobile/notifications?before=\(nextNotice)")
            notices.append(contentsOf: page.notifications); self.nextNotice = page.next_before
        } catch { report(error) }
    }

    func openNotice(_ id: String) async {
        do {
            let target: Target = try await call("/api/mobile/notifications/\(MobileURL.segment(id))/target")
            let _: EmptyResponse = try await call("/api/mobile/notifications/read", method: "POST", body: ["ids": [id]])
            selectedRecord = target.record
            if target.record == nil { message = "Prueba recibida en Flowing." }
            await refresh()
        } catch { report(error) }
    }

    func markAllRead() async {
        do {
            // Read every page, not just the first 50 rows visible on screen.
            var cursor: Int?
            repeat {
                let suffix = cursor.map { "?unread=1&before=\($0)" } ?? "?unread=1"
                let page: Inbox = try await call("/api/mobile/notifications" + suffix)
                let _: EmptyResponse = try await call("/api/mobile/notifications/read", method: "POST", body: ["ids": page.notifications.map(\.id)])
                cursor = page.next_before
            } while cursor != nil
            await refresh()
        } catch { report(error) }
    }

    func preference(_ kind: String, enabled: Bool) async {
        do {
            let result: PreferenceResult = try await call("/api/mobile/preferences", method: "POST", body: [kind: enabled])
            preferences = result.preferences
        } catch { report(error) }
    }

    func enablePush() async {
        let currentGeneration = generation
        let currentDeviceGeneration = deviceGeneration
        do {
            let device: DeviceStatus = try await call("/api/mobile/device")
            guard currentDeviceGeneration == deviceGeneration else { return }
            deviceRevision = device.revision
            hideDetails = device.hide_details
            let granted = try await UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound, .badge])
            guard connected && !disconnecting && currentGeneration == generation && currentDeviceGeneration == deviceGeneration else { return }
            if granted {
                registrationGeneration = generation
                registrationInteractive = true
                UIApplication.shared.registerForRemoteNotifications()
            }
            else { error = "Las notificaciones están desactivadas. Puedes activarlas en Ajustes del iPhone." }
        } catch { report(error) }
    }

    func registerDevice(_ value: String) async {
        guard connected && registrationGeneration == generation else { return }
        let currentDeviceGeneration = deviceGeneration
        do {
            let environment = Bundle.main.object(forInfoDictionaryKey: "APNSEnvironment") as? String ?? "sandbox"
            let result: DeviceResult = try await call("/api/mobile/device", method: "POST", body: ["token": value, "environment": environment, "hide_details": hideDetails, "revision": deviceRevision])
            guard currentDeviceGeneration == deviceGeneration && registrationGeneration == generation else { return }
            deviceRevision = result.revision
            pushActive = true
            registeredGeneration = generation
            if registrationInteractive { message = "iPhone registrado. Envía una prueba para comprobar la recepción." }
        } catch { report(error) }
    }

    func pushTest() async {
        do {
            let _: EmptyResponse = try await call("/api/mobile/push/test", method: "POST", body: [:])
            message = "Prueba en cola. Cierra Flowing y comprueba el aviso en tu iPhone."
        } catch { report(error) }
    }

    func disablePush() async {
        deviceGeneration += 1
        registrationGeneration = nil
        registeredGeneration = nil
        do {
            let result: DeviceResult = try await call("/api/mobile/device", method: "DELETE")
            deviceRevision = result.revision
            pushActive = false
            registrationGeneration = nil
            registeredGeneration = nil
            message = "Este iPhone ya no recibirá avisos de esta cuenta."
        } catch { report(error) }
    }

    func privacy(_ hidden: Bool) async {
        deviceGeneration += 1
        registrationGeneration = nil
        do {
            let result: DeviceResult = try await call("/api/mobile/device/privacy", method: "POST", body: ["hide_details": hidden])
            deviceRevision = result.revision
            hideDetails = hidden
        } catch { report(error) }
    }

    func logout() async {
        generation += 1
        registrationGeneration = nil
        registeredGeneration = nil
        disconnecting = true
        defer { disconnecting = false }
        do {
            let _: EmptyResponse = try await call("/api/mobile/logout", method: "POST", body: [:])
            forgetSession()
        } catch { self.error = "No se pudo desconectar el servidor. Reintenta con conexión para revocar los avisos." }
    }

    private func report(_ failure: Error) {
        if !(failure is CancellationError) { error = failure.localizedDescription }
    }

    private func forgetSession() {
        generation += 1
        registrationGeneration = nil
        registeredGeneration = nil
        deviceGeneration += 1
        deviceRevision = 0
        if let base { TokenVault.remove(base.absoluteString) }
        token = nil; connected = false; overview = nil; notices = []; unread = 0
        preferences = [:]; pushActive = false; selectedRecord = nil; error = nil; message = nil
        Task { try? await UNUserNotificationCenter.current().setBadgeCount(0) }
    }
}
