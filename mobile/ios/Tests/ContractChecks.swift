import Foundation
import FlowingContracts

@main struct ContractChecks {
    static func main() throws {
        precondition(MobileURL.server("https://flow.example") != nil)
        for value in ["http://flow.example", "https://user:password@flow.example", "https://flow.example/private", "https://flow.example?token=secret"] {
            precondition(MobileURL.server(value) == nil, "Unsafe server URL accepted")
        }
        let base = MobileURL.server("https://flow.example")!
        precondition(MobileURL.endpoint(base: base, path: "https://other.example/api/mobile/overview") == nil)
        precondition(MobileURL.endpoint(base: base, path: "//other.example/api/mobile/overview") == nil)
        precondition(MobileURL.endpoint(base: base, path: "/api/mobile/notifications?before=12") != nil)
        precondition(MobileURL.endpoint(base: base, path: "/api/mobile/../../login") == nil)
        precondition(MobileURL.endpoint(base: base, path: "/api/mobile/%2e%2e/login") == nil)
        precondition(MobileURL.segment("a/b?token=x") == "a%2Fb%3Ftoken%3Dx")
        precondition(MobileURL.weddingDate("2026-02-30") == nil)
        precondition(MobileURL.weddingDate("2026-10-10") != nil)
        let data = Data(#"{"notifications":[{"id":"notice","kind":"lead","title":"Nuevo lead","created":1790000000,"read":false,"target_kind":"leads","target_id":"l1"}],"count":1,"next_before":12}"#.utf8)
        let page = try JSONDecoder().decode(Inbox.self, from: data)
        precondition(page.notifications.first?.target_id == "l1" && page.next_before == 12 && !page.notifications[0].read)
        print("14 native contract checks passed (Foundation only; not an iOS build).")
    }
}
