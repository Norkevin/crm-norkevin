import Foundation

public struct CRMRecord: Codable, Identifiable, Hashable {
    public let id: String
    public let kind: String
    public let name: String
    public let status: String
    public let date: String
    public let email: String
    public let phone: String
    public let location: String
}

public struct Overview: Decodable {
    public let tenant_name: String
    public let lead_count: Int
    public let job_count: Int
    public let paid: Double
    public let due: Double
    public let year: Int
    public let monthly_paid: [Double]?
    public let upcoming: [CRMRecord]?
    public let user_email: String?
}

public struct Notice: Decodable, Identifiable {
    public let id: String
    public let kind: String
    public let title: String
    public let created: Double
    public var read: Bool
    public let target_kind: String?
    public let target_id: String?
}

public struct Inbox: Decodable {
    public let notifications: [Notice]
    public let count: Int
    public let next_before: Int?
}

public struct RecordPage: Decodable {
    public let records: [CRMRecord]
    public let next_offset: Int?
}

public struct Payment: Decodable, Identifiable {
    public let id: String
    public let name: String
    public let status: String
    public let amount: Double
    public let date: String
}

public struct PaymentPage: Decodable {
    public let payments: [Payment]
    public let next_offset: Int?
    public let summary: [String: Double]
    public let year: Int
}

public enum MobileURL {
    public static let dayStyle = Date.FormatStyle(date: .long, time: .omitted,
        locale: Locale(identifier: "es_GT"), timeZone: TimeZone(identifier: "America/Guatemala")!)
    public static let momentStyle = Date.FormatStyle(date: .abbreviated, time: .shortened,
        locale: Locale(identifier: "es_GT"), timeZone: TimeZone(identifier: "America/Guatemala")!)
    public static func server(_ value: String) -> URL? {
        guard let url = URL(string: value.trimmingCharacters(in: .whitespacesAndNewlines)),
              url.scheme == "https", url.host != nil, url.user == nil, url.password == nil,
              url.query == nil, url.fragment == nil, url.path.isEmpty || url.path == "/" else { return nil }
        return url
    }

    public static func endpoint(base: URL, path: String) -> URL? {
        let route = path.components(separatedBy: "?").first ?? ""
        guard let decoded = route.removingPercentEncoding,
              !decoded.contains("\\"), !decoded.split(separator: "/").contains(".."),
              !decoded.unicodeScalars.contains(where: { $0.value < 32 }),
              path.hasPrefix("/api/mobile/"), !path.contains("\\"),
              let result = URL(string: path, relativeTo: base)?.absoluteURL,
              result.host == base.host, result.scheme == "https", result.port == base.port else { return nil }
        return result
    }

    public static func segment(_ value: String) -> String {
        value.addingPercentEncoding(withAllowedCharacters: .alphanumerics) ?? ""
    }

    public static func weddingDate(_ value: String) -> Date? {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = TimeZone(identifier: "America/Guatemala")
        formatter.dateFormat = "yyyy-MM-dd"
        formatter.isLenient = false
        return formatter.date(from: String(value.prefix(10)))
    }
}


public struct TeamPage: Decodable {
    public let enabled: Bool
    public let jobs: [TeamJob]
    public let members: [TeamMember]
}
public struct TeamJob: Decodable, Identifiable {
    public let id, name, date, location, phase: String
    public let cost, pending: Int
    public let incomplete, closed: Bool
    public let assignments: [TeamAssignment]
}
public struct TeamAssignment: Decodable, Identifiable {
    public let id, member_id, name, role, status, start, end: String
    public let schedule_pending, changed: Bool
    public let conflicts: Int
    public let amount, paid, pending: Int?
}
public struct TeamMember: Decodable, Identifiable {
    public let id, name, role: String
    public let active, rate_missing: Bool
    public let paid, pending: Int
}
