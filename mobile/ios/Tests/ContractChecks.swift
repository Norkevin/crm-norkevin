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
        let legacy = Data(#"{"id":"j1","kind":"jobs","name":"Boda ficticia","status":"Confirmado","date":"2026-10-30","email":"","phone":"","location":""}"#.utf8)
        let oldRecord = try JSONDecoder().decode(CRMRecord.self, from: legacy)
        precondition(oldRecord.end_date == nil && oldRecord.event_type == nil && !oldRecord.isTentative)
        let calendarData = Data(#"{"month":"2026-11","records":[{"id":"b1","kind":"calendar","name":"Bloqueo ficticio","status":"Bloqueado","date":"2026-10-30","end_date":"2026-11-02","event_type":"block","email":"","phone":"","location":""}]}"#.utf8)
        let month = try JSONDecoder().decode(CalendarPage.self, from: calendarData)
        let block = month.records[0]
        precondition(month.month == "2026-11" && block.end_date == "2026-11-02" && block.calendarTypeLabel == "Bloqueo de fechas")
        for day in ["2026-10-30", "2026-10-31", "2026-11-01", "2026-11-02"] {
            precondition(block.includes(day: MobileURL.weddingDate(day)!), "Inclusive date range lost a day")
        }
        for day in ["2026-10-29", "2026-11-03"] {
            precondition(!block.includes(day: MobileURL.weddingDate(day)!), "Calendar range leaked outside its dates")
        }
        precondition(MobileCalendar.rangeLabel(block)?.contains(" al ") == true)
        let tentativeData = Data(#"{"id":"l1","kind":"leads","name":"Consulta ficticia","status":"En cola","date":"2026-10-30","end_date":"2026-10-29","event_type":"lead","email":"","phone":"","location":""}"#.utf8)
        let tentative = try JSONDecoder().decode(CRMRecord.self, from: tentativeData)
        precondition(tentative.isTentative && tentative.calendarTypeLabel == "Fecha tentativa")
        precondition(tentative.includes(day: MobileURL.weddingDate("2026-10-30")!) && !tentative.includes(day: MobileURL.weddingDate("2026-10-29")!))
        let utcBoundary = ISO8601DateFormatter().date(from: "2026-11-01T02:00:00Z")!
        precondition(MobileCalendar.monthKey(utcBoundary) == "2026-10", "Month must follow the CRM's Guatemala time zone")
        let february = MobileCalendar.days(in: MobileURL.weddingDate("2024-02-15")!)
        precondition(february.count == 29 && february.last == MobileURL.weddingDate("2024-02-29"))
        precondition(MobileCalendar.calendar.firstWeekday == 2)
        print("28 native contract checks passed (Foundation only; not an iOS build).")
    }
}
