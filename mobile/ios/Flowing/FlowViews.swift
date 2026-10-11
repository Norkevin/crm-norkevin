import SwiftUI
import Charts

struct RootView: View {
    @EnvironmentObject var flow: FlowSession
    var body: some View {
        Group {
            if flow.connected {
                TabView {
                    NavigationStack { HomeView() }
                        .tabItem { Label { Text("Inicio") } icon: { Image(uiImage: WebNavigationIcon.image("home")) } }
                    NavigationStack { RecordsView(kind: "leads", title: "Leads") }
                        .tabItem { Label { Text("Leads") } icon: { Image(uiImage: WebNavigationIcon.image("leads")) } }
                    NavigationStack { TeamsView() }
                        .tabItem { Label { Text("Teams") } icon: { Image(uiImage: WebNavigationIcon.image("teams")) } }
                    NavigationStack { CalendarView() }
                        .tabItem { Label { Text("Calendario") } icon: { Image(uiImage: WebNavigationIcon.image("calendar")) } }
                    NavigationStack { MoreView() }
                        .tabItem { Label { Text("Más") } icon: { Image(uiImage: WebNavigationIcon.image("more")) } }
                        .badge(flow.unread)
                }
                .sheet(item: $flow.selectedRecord) { record in
                    NavigationStack {
                        RecordDetail(record: record)
                            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Listo") { flow.selectedRecord = nil } } }
                    }
                }
            } else {
                NavigationStack { ConnectView() }
            }
        }
        .alert("Revisa la conexión", isPresented: Binding(get: { flow.error != nil }, set: { if !$0 { flow.error = nil } })) {
            Button("Entendido", role: .cancel) { flow.error = nil }
        } message: { Text(flow.error ?? "") }
        .alert("Flowing", isPresented: Binding(get: { flow.message != nil }, set: { if !$0 { flow.message = nil } })) {
            Button("Listo", role: .cancel) { flow.message = nil }
        } message: { Text(flow.message ?? "") }
    }
}

struct ConnectView: View {
    @EnvironmentObject var flow: FlowSession
    @Environment(\.openURL) var openURL
    var body: some View {
        Form {
            Section {
                Label("Tu estudio, contigo", systemImage: "camera.aperture")
                    .font(.title2.weight(.semibold)).padding(.vertical, 8)
                Text("Consulta tus bodas, sigue tus leads y recibe los avisos de tu empresa.")
                    .foregroundStyle(.secondary)
            }
            Section {
                TextField("https://tu-flowing.com", text: $flow.serverText)
                    .keyboardType(.URL).textInputAutocapitalization(.never).autocorrectionDisabled()
                    .disabled(flow.connecting)
                if flow.pairingCode.isEmpty {
                    Button("Conectar mi cuenta") { Task { await flow.startPairing() } }
                        .disabled(flow.connecting)
                } else {
                    Text(flow.pairingCode).font(.title.monospaced().weight(.semibold))
                        .textSelection(.enabled).accessibilityLabel("Código: " + flow.pairingCode)
                    Text("Abre Flowing, inicia sesión con Google e introduce este código. Comprueba la empresa antes de autorizar.")
                    Button("Abrir Flowing para conectar") {
                        if let url = flow.base?.appendingPathComponent("mobile/connect") { openURL(url) }
                    }
                    HStack { ProgressView(); Text("Esperando tu autorización…").foregroundStyle(.secondary) }
                    Button("Cancelar", role: .cancel) { flow.cancelPairing() }
                }
            } header: { Text("Tu Flowing") } footer: {
                Text("La web sigue funcionando. La aplicación usa tu misma cuenta y respeta los permisos de cada empresa.")
            }
        }
        .navigationTitle("Flowing")
    }
}

struct HomeView: View {
    @EnvironmentObject var flow: FlowSession
    @Environment(\.dynamicTypeSize) private var textSize
    var body: some View {
        List {
            Section {
                HStack(spacing: 12) {
                    Image("FlowingMark").resizable().scaledToFit().frame(width: 44, height: 44)
                        .accessibilityHidden(true)
                    VStack(alignment: .leading, spacing: 4) {
                        Text(flow.overview?.tenant_name ?? "Tu estudio").font(.headline)
                        Text(Date(), format: MobileURL.dayStyle).font(.subheadline).foregroundStyle(.secondary)
                    }
                }.padding(.vertical, 4)
            }.listRowBackground(Color.clear)
            if let overview = flow.overview {
                Section {
                    LazyVGrid(columns: Array(repeating: GridItem(.flexible(), alignment: .leading),
                        count: textSize.isAccessibilitySize ? 1 : 2), spacing: 12) {
                        StudioMetric(title: "Leads abiertos", value: String(overview.lead_count), symbol: "person.badge.plus")
                        StudioMetric(title: "Trabajos activos", value: String(overview.job_count), symbol: "briefcase")
                        StudioMetric(title: "Cobrado en \(String(overview.year))", value: overview.paid.formatted(.currency(code: "GTQ")), symbol: "creditcard")
                        StudioMetric(title: "Por cobrar en \(String(overview.year))", value: overview.due.formatted(.currency(code: "GTQ")), symbol: "calendar.badge.clock")
                    }
                }.listRowBackground(Color.clear).listRowInsets(EdgeInsets())
                if let monthly = overview.monthly_paid, monthly.count == 12 {
                    Section("Cobros de \(String(overview.year))") {
                        Chart(Array(monthly.enumerated()), id: \.offset) { item in
                            LineMark(x: .value("Mes", item.offset + 1), y: .value("Cobrado", item.element))
                                .foregroundStyle(Color.accentColor)
                                .accessibilityLabel(monthName(item.offset))
                                .accessibilityValue(item.element.formatted(.currency(code: "GTQ")))
                        }
                        .chartXAxis {
                            AxisMarks(values: [1, 3, 5, 7, 9, 11]) { axis in
                                AxisValueLabel { if let month = axis.as(Int.self) { Text(monthName(month - 1)) } }
                            }
                        }
                        .frame(height: textSize.isAccessibilitySize ? 220 : 170)
                        .accessibilityLabel("Cobros por mes en quetzales")
                        Text("Pagos registrados como pagados, según su fecha de cobro.")
                            .font(.footnote).foregroundStyle(.secondary)
                    }
                }
                Section("Próximos trabajos") {
                    ForEach(overview.upcoming ?? []) { record in
                        NavigationLink { RecordDetail(record: record) } label: { RecordRow(record: record) }
                    }
                    if (overview.upcoming ?? []).isEmpty {
                        Text("No hay trabajos próximos programados.").foregroundStyle(.secondary)
                    }
                }
            } else {
                Section {
                    if flow.busy { ProgressView("Cargando tu estudio…") }
                    else { Button("Cargar resumen") { Task { await flow.refresh() } } }
                }
            }
            Section("Para revisar") {
                NavigationLink { NoticeView() } label: { Label("Notificaciones · \(flow.unread) sin leer", systemImage: "bell") }
                NavigationLink { RecordsView(kind: "clients", title: "Clientes") } label: { Label("Clientes", systemImage: "person.crop.circle") }
                NavigationLink { PaymentsView() } label: { Label("Pagos", systemImage: "creditcard") }
                NavigationLink { RecordsView(kind: "jobs", title: "Trabajos") } label: { Label("Trabajos", systemImage: "briefcase") }
            }
            Section {
                Text("Tus datos se actualizan desde Flowing. Desliza hacia abajo para actualizar.").font(.footnote).foregroundStyle(.secondary)
            }
        }
        .navigationTitle("Inicio")
        .toolbar { InboxButton() }
        .refreshable { await flow.refresh() }
        .task { await flow.refresh() }
    }
    private func monthName(_ index: Int) -> String {
        ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"][max(0, min(11, index))]
    }
}

struct StudioMetric: View {
    let title: String
    let value: String
    let symbol: String
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Image(systemName: symbol).foregroundStyle(Color.accentColor).font(.title3)
                .accessibilityHidden(true)
            Text(value).font(.title3.weight(.semibold)).monospacedDigit().fixedSize(horizontal: false, vertical: true)
            Text(title).font(.subheadline).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity, minHeight: 108, alignment: .leading)
        .padding(16).background(Color(uiColor: .secondarySystemGroupedBackground), in: RoundedRectangle(cornerRadius: 16))
        .accessibilityElement(children: .combine)
    }
}

struct InboxButton: ToolbarContent {
    @EnvironmentObject var flow: FlowSession
    var body: some ToolbarContent {
        ToolbarItem(placement: .topBarTrailing) {
            NavigationLink { NoticeView() } label: { Image(systemName: flow.unread > 0 ? "bell.badge" : "bell") }
                .accessibilityLabel("Notificaciones, \(flow.unread) sin leer")
        }
    }
}

struct RecordRow: View {
    let record: CRMRecord
    var body: some View {
        HStack(spacing: 12) {
            Text(String(record.name.prefix(1)).uppercased())
                .font(.subheadline.weight(.semibold)).foregroundStyle(Color.accentColor)
                .frame(width: 40, height: 40).background(Color.accentColor.opacity(0.10), in: Circle())
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 4) {
                Text(record.name).font(.body.weight(.medium))
                if let date = MobileCalendar.rangeLabel(record) {
                    Text(date).font(.footnote).foregroundStyle(.primary)
                }
                if let type = record.calendarTypeLabel {
                    Text(type).font(.footnote.weight(.semibold))
                }
                if !record.status.isEmpty { Text(record.status).font(.footnote).foregroundStyle(.secondary) }
            }
        }.padding(.vertical, 4)
    }
}

struct RecordsView: View {
    @EnvironmentObject var flow: FlowSession
    let kind: String
    let title: String
    @State private var records: [CRMRecord] = []
    @State private var nextOffset: Int?
    @State private var search = ""
    @State private var loading = false
    @State private var loadError: String?
    @State private var status = "Todos"
    var filtered: [CRMRecord] {
        records.filter { (search.isEmpty || $0.name.localizedCaseInsensitiveContains(search)) && (status == "Todos" || $0.status == status) }
    }
    var body: some View {
        List {
            if !records.isEmpty {
                Section {
                    Picker("Estado", selection: $status) {
                        Text("Todos").tag("Todos")
                        ForEach(Array(Set(records.map(\.status))).filter { !$0.isEmpty }.sorted(), id: \.self) { Text($0).tag($0) }
                    }
                }
            }
            ForEach(filtered) { record in NavigationLink { RecordDetail(record: record) } label: { RecordRow(record: record) } }
            if let nextOffset {
                Button("Cargar más") { Task { await load(offset: nextOffset) } }.disabled(loading)
            }
            if loading { ProgressView("Cargando…") }
            if let loadError { Section { Text(loadError); Button("Reintentar") { Task { await load() } } } }
        }
        .overlay {
            if filtered.isEmpty && !loading && loadError == nil {
                ContentUnavailableView(search.isEmpty ? "Aún no hay registros" : "Sin resultados", systemImage: "tray", description: Text("Los registros de esta empresa aparecerán aquí."))
            }
        }
        .navigationTitle(title).searchable(text: $search, prompt: "Buscar en los registros cargados")
        .toolbar { InboxButton() }
        .refreshable { await load() }.task { await load() }
    }
    private func load(offset: Int = 0) async {
        loading = true; loadError = nil
        defer { loading = false }
        do {
            let page: RecordPage = try await flow.call("/api/mobile/records/\(kind)?offset=\(offset)")
            records = offset == 0 ? page.records : records + page.records
            nextOffset = page.next_offset
        } catch { loadError = error.localizedDescription }
    }
}

struct RecordDetail: View {
    @EnvironmentObject var flow: FlowSession
    @Environment(\.openURL) var openURL
    let record: CRMRecord
    var body: some View {
        List {
            Section {
                Text(record.name).font(.title2.weight(.semibold))
                if !record.status.isEmpty { Label(record.status, systemImage: "checkmark.circle") }
                if let type = record.calendarTypeLabel { Label(type, systemImage: record.isTentative ? "clock" : "calendar.badge.checkmark") }
                if let date = MobileCalendar.rangeLabel(record) { Label(date, systemImage: "calendar") }
                if !record.location.isEmpty { Label(record.location, systemImage: "mappin.and.ellipse") }
            }
            if !record.email.isEmpty || !record.phone.isEmpty {
                Section("Contacto") {
                    if !record.email.isEmpty { LabeledContent("Correo", value: record.email).textSelection(.enabled) }
                    if !record.phone.isEmpty { LabeledContent("Teléfono", value: record.phone).textSelection(.enabled) }
                }
            }
            Section {
                Button("Abrir ficha completa en Flowing", systemImage: "arrow.up.right.square") {
                    guard let base = flow.base else { return }
                    let url = record.kind == "calendar" ? base.appendingPathComponent("calendar")
                        : base.appendingPathComponent(record.kind).appendingPathComponent(record.id)
                    openURL(url)
                }
            } footer: {
                Text("Documentos, edición y operaciones del CRM continúan disponibles en la web durante esta primera etapa.")
            }
        }
        .navigationTitle(record.kind == "jobs" ? "Trabajo" : "Detalle")
        .navigationBarTitleDisplayMode(.inline)
    }
}

struct CalendarView: View {
    @EnvironmentObject var flow: FlowSession
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.dynamicTypeSize) private var textSize
    @State private var selected = Date()
    @State private var records: [CRMRecord] = []
    @State private var loading = false
    @State private var loadError: String?
    @State private var loadedMonth = ""
    @State private var latestRequest = UUID()
    private var calendar: Calendar { MobileCalendar.calendar }
    private var month: String { MobileCalendar.monthKey(selected) }
    private var days: [Date] { MobileCalendar.days(in: selected) }
    private var monthStyle: Date.FormatStyle {
        Date.FormatStyle(locale: Locale(identifier: "es_GT"), calendar: calendar, timeZone: calendar.timeZone)
            .month(.wide).year()
    }
    var body: some View {
        ScrollViewReader { proxy in
            List {
                if textSize.isAccessibilitySize { selectedEvents }
                Section {
                    VStack(spacing: 16) {
                        Text(selected.formatted(monthStyle)).font(.title3.weight(.semibold))
                            .accessibilityAddTraits(.isHeader)
                        HStack {
                            Button { changeMonth(-1) } label: { Image(systemName: "chevron.left").frame(minWidth: 44, minHeight: 44) }
                                .accessibilityLabel("Mes anterior")
                            Spacer()
                            Button { selected = Date() } label: { Text("Hoy").frame(minWidth: 44, minHeight: 44) }
                            Spacer()
                            Button { changeMonth(1) } label: { Image(systemName: "chevron.right").frame(minWidth: 44, minHeight: 44) }
                                .accessibilityLabel("Mes siguiente")
                        }
                        if textSize.isAccessibilitySize {
                            VStack(spacing: 4) { ForEach(days, id: \.self) { day in dayButton(day) } }
                        } else {
                            ScrollView(.horizontal) {
                                LazyVGrid(columns: Array(repeating: GridItem(.fixed(44)), count: 7), spacing: 4) {
                                    ForEach(Array(["L", "M", "M", "J", "V", "S", "D"].enumerated()), id: \.offset) { _, label in
                                        Text(label).font(.caption).foregroundStyle(.secondary).accessibilityHidden(true)
                                    }
                                    if let first = days.first {
                                        ForEach(0..<((calendar.component(.weekday, from: first) + 5) % 7), id: \.self) { _ in
                                            Color.clear.frame(width: 44, height: 48).accessibilityHidden(true)
                                        }
                                    }
                                    ForEach(days, id: \.self) { day in dayButton(day) }
                                }.frame(width: 332)
                            }.scrollIndicators(.hidden)
                        }
                        ViewThatFits(in: .horizontal) {
                            HStack(spacing: 16) { legend }
                            VStack(alignment: .leading, spacing: 8) { legend }
                        }.font(.footnote)
                    }.padding(.vertical, 4)
                }.listRowInsets(EdgeInsets(top: 12, leading: 12, bottom: 12, trailing: 12))
                if !textSize.isAccessibilitySize { selectedEvents }
            }.navigationTitle("Calendario").toolbar { InboxButton() }
                .refreshable { await load(month: month) }
                .task(id: month) { await load(month: month) }
                .onChange(of: scenePhase) { _, phase in
                    if phase == .active { Task { await load(month: month) } }
                }
                .onChange(of: selected) { _, _ in
                    if textSize.isAccessibilitySize { proxy.scrollTo("calendar-events", anchor: .top) }
                }
        }
    }

    private var selectedEvents: some View {
        Section(selected.formatted(MobileURL.dayStyle)) {
            let events = records.filter { $0.includes(day: selected) }
            ForEach(events, id: \.self) { record in NavigationLink { RecordDetail(record: record) } label: { RecordRow(record: record) } }
            if loading { ProgressView("Cargando calendario…") }
            else if let loadError { Text(loadError); Button("Reintentar") { Task { await load(month: month) } } }
            else if events.isEmpty { Text("No hay reservas, eventos, bloqueos ni fechas tentativas para este día.").foregroundStyle(.secondary) }
        }.id("calendar-events")
    }

    @ViewBuilder private var legend: some View {
        HStack(spacing: 6) { Circle().fill(Color.accentColor).frame(width: 7, height: 7); Text("Fecha ocupada") }
        HStack(spacing: 6) { Circle().stroke(Color.accentColor, lineWidth: 1.5).frame(width: 7, height: 7); Text("Fecha tentativa") }
    }

    private func dayButton(_ day: Date) -> some View {
        let chosen = calendar.isDate(day, inSameDayAs: selected)
        let events = records.filter { $0.includes(day: day) }
        let occupied = events.filter { !$0.isTentative }.count
        let tentative = events.filter(\.isTentative).count
        let ink = chosen ? Color.white : Color.accentColor
        return Button { selected = day } label: {
            Group {
                if textSize.isAccessibilitySize {
                    VStack(alignment: .leading, spacing: 6) {
                        Text(day, format: MobileURL.dayStyle).font(.body.weight(.semibold))
                        Text(availability(occupied: occupied, tentative: tentative)).font(.subheadline)
                    }.frame(maxWidth: .infinity, alignment: .leading).padding(12)
                } else {
                    VStack(spacing: 5) {
                        Text(String(calendar.component(.day, from: day))).font(.body)
                            .fontWeight(calendar.isDate(day, inSameDayAs: Date()) ? .bold : .regular)
                        HStack(spacing: 4) {
                            if occupied > 0 { Circle().fill(ink).frame(width: 6, height: 6) }
                            if tentative > 0 { Circle().stroke(ink, lineWidth: 1.5).frame(width: 6, height: 6) }
                        }.frame(height: 8)
                    }.frame(minWidth: 44, minHeight: 48)
                }
            }.foregroundStyle(chosen ? Color.white : Color.primary)
                .background(chosen ? Color.accentColor : Color.clear, in: RoundedRectangle(cornerRadius: 10))
        }.buttonStyle(.plain)
            .accessibilityLabel(day.formatted(MobileURL.dayStyle) + ", " + availability(occupied: occupied, tentative: tentative))
            .accessibilityHint("Muestra los eventos de esta fecha.")
            .accessibilityAddTraits(chosen ? .isSelected : [])
    }

    private func availability(occupied: Int, tentative: Int) -> String {
        if loadError != nil { return "Disponibilidad sin cargar" }
        if loading || loadedMonth != month { return "Cargando disponibilidad" }
        var parts: [String] = []
        if occupied > 0 { parts.append("Ocupado: \(occupied) \(occupied == 1 ? "evento o bloqueo" : "eventos o bloqueos")") }
        if tentative > 0 { parts.append("\(tentative) \(tentative == 1 ? "fecha tentativa" : "fechas tentativas")") }
        return parts.isEmpty ? "Sin fechas apartadas" : parts.joined(separator: ", ")
    }

    private func changeMonth(_ offset: Int) {
        if let start = calendar.dateInterval(of: .month, for: selected)?.start,
           let next = calendar.date(byAdding: .month, value: offset, to: start) { selected = next }
    }

    private func load(month requestedMonth: String) async {
        let request = UUID()
        latestRequest = request
        if loadedMonth != requestedMonth { records = [] }
        loading = true; loadError = nil
        defer { if latestRequest == request { loading = false } }
        do {
            let page: CalendarPage = try await flow.call("/api/mobile/calendar?month=\(requestedMonth)")
            guard latestRequest == request, !Task.isCancelled, month == requestedMonth else { return }
            guard page.month == requestedMonth else { throw FlowError.message("El servidor devolvió otro mes. Vuelve a cargar el calendario.") }
            records = page.records; loadedMonth = requestedMonth
        } catch is CancellationError { }
        catch {
            guard latestRequest == request, month == requestedMonth else { return }
            records = []; loadError = error.localizedDescription
        }
    }
}

struct NoticeView: View {
    @EnvironmentObject var flow: FlowSession
    @State private var unreadOnly = false
    var body: some View {
        List {
            Picker("Mostrar", selection: $unreadOnly) { Text("Todas").tag(false); Text("Sin leer").tag(true) }.pickerStyle(.segmented)
            ForEach(flow.notices.filter { !unreadOnly || !$0.read }) { notice in
                Button { Task { await flow.openNotice(notice.id) } } label: {
                    HStack(alignment: .top, spacing: 12) {
                        Image(systemName: notice.kind == "payment" ? "creditcard" : notice.kind == "teams" ? "person.2" : "bell")
                            .foregroundStyle(Color.accentColor).frame(width: 32)
                        VStack(alignment: .leading, spacing: 6) {
                            Text(notice.title).fontWeight(notice.read ? .regular : .semibold).foregroundStyle(.primary)
                            Text(Date(timeIntervalSince1970: notice.created), format: MobileURL.momentStyle)
                                .font(.footnote).foregroundStyle(Color(uiColor: .label))
                        }
                        if !notice.read { Image(systemName: "circle.fill").font(.caption2).accessibilityLabel("Sin leer") }
                    }.padding(.vertical, 6)
                }
            }
            if flow.nextNotice != nil { Button("Cargar más") { Task { await flow.loadMoreNotices() } } }
            if flow.notices.isEmpty && !flow.busy { ContentUnavailableView("Todo al día", systemImage: "bell", description: Text("Los nuevos avisos de esta empresa aparecerán aquí.")) }
            if flow.busy { ProgressView("Actualizando avisos…") }
        }.navigationTitle("Notificaciones")
            .toolbar { ToolbarItem(placement: .topBarTrailing) { Button("Marcar todas") { Task { await flow.markAllRead() } }.disabled(flow.unread == 0) } }
            .refreshable { await flow.refresh() }.task { await flow.refresh() }
    }
}

struct PaymentsView: View {
    @EnvironmentObject var flow: FlowSession
    @State private var payments: [Payment] = []
    @State private var summary: [String: Double] = [:]
    @State private var nextOffset: Int?
    @State private var loading = false
    @State private var loadError: String?
    @State private var year = 0
    var body: some View {
        List {
            if year > 0 {
                Section("Resumen de \(String(year))") {
                    LabeledContent("Cobrado") { Text(summary["paid"] ?? 0, format: .currency(code: "GTQ")).monospacedDigit() }
                    LabeledContent("Por cobrar") { Text(summary["due"] ?? 0, format: .currency(code: "GTQ")).monospacedDigit() }
                }
            }
            Section("Historial de pagos") {
                ForEach(payments) { payment in
                    VStack(alignment: .leading, spacing: 4) {
                        Text(payment.name).fontWeight(.medium)
                        Text(payment.amount, format: .currency(code: "GTQ")).monospacedDigit()
                        Text(payment.status + " · " + (MobileURL.weddingDate(payment.date)?.formatted(MobileURL.dayStyle) ?? payment.date))
                            .font(.footnote).foregroundStyle(.secondary)
                    }.padding(.vertical, 4)
                }
                if let nextOffset { Button("Cargar más") { Task { await load(offset: nextOffset) } }.disabled(loading) }
                if loading { ProgressView("Cargando pagos…") }
                if let loadError { Text(loadError); Button("Reintentar") { Task { await load() } } }
                if payments.isEmpty && !loading && loadError == nil { Text("No hay pagos en esta empresa.").foregroundStyle(.secondary) }
            }
        }.navigationTitle("Pagos").refreshable { await load() }.task { await load() }
    }
    private func load(offset: Int = 0) async {
        loading = true; loadError = nil
        defer { loading = false }
        do {
            let page: PaymentPage = try await flow.call("/api/mobile/payments?offset=\(offset)")
            payments = offset == 0 ? page.payments : payments + page.payments
            nextOffset = page.next_offset; summary = page.summary; year = page.year
        } catch { loadError = error.localizedDescription }
    }
}

struct MoreView: View {
    @EnvironmentObject var flow: FlowSession
    @State private var confirmLogout = false
    var body: some View {
        List {
            Section {
                Label(flow.overview?.tenant_name ?? "Flowing", systemImage: "building.2")
                if let email = flow.overview?.user_email { Text(email).font(.subheadline).foregroundStyle(.secondary).textSelection(.enabled) }
            }
            Section {
                NavigationLink { RecordsView(kind: "clients", title: "Clientes") } label: { Label("Clientes", systemImage: "person.crop.circle") }
                NavigationLink { PaymentsView() } label: { Label("Pagos", systemImage: "creditcard") }
                NavigationLink { RecordsView(kind: "jobs", title: "Trabajos") } label: { Label("Trabajos", systemImage: "briefcase") }
                NavigationLink { NoticeView() } label: { Label("Notificaciones", systemImage: "bell") }
                NavigationLink { NotificationSettings() } label: { Label("Configuración de avisos", systemImage: "gearshape") }
            }
            Section { Button("Cerrar sesión", role: .destructive) { confirmLogout = true } }
        }.navigationTitle("Más")
            .confirmationDialog("¿Desconectar esta cuenta del iPhone?", isPresented: $confirmLogout, titleVisibility: .visible) {
                Button("Cerrar sesión", role: .destructive) { Task { await flow.logout() } }
                Button("Cancelar", role: .cancel) {}
            }
    }
}

struct NotificationSettings: View {
    @EnvironmentObject var flow: FlowSession
    let kinds = [("lead", "Leads nuevos"), ("payment", "Pagos registrados"), ("wedding", "Bodas próximas"), ("teams", "Respuestas de Teams"), ("mail", "Correos no entregados")]
    var body: some View {
        Form {
            Section("Bandeja de Flowing") {
                Text("Los avisos de tu empresa se sincronizan al abrir la app y al actualizar la bandeja.")
                NavigationLink("Ver mis notificaciones") { NoticeView() }
            }
            Section {
                LabeledContent("Avisos con la app cerrada", value: flow.pushAvailable ? (flow.pushActive ? "Activados" : "Desactivados") : "Sin habilitar")
                Toggle("Ocultar detalles en pantalla bloqueada", isOn: Binding(get: { flow.hideDetails }, set: { value in Task { await flow.privacy(value) } }))
                Button(flow.pushActive ? "Actualizar permiso y privacidad" : "Activar notificaciones") { Task { await flow.enablePush() } }
                    .disabled(!flow.pushAvailable)
                Button("Enviar prueba a este iPhone") { Task { await flow.pushTest() } }
                    .disabled(!flow.pushAvailable || !flow.pushActive)
                Button("Desactivar avisos en este iPhone", role: .destructive) { Task { await flow.disablePush() } }
                    .disabled(!flow.pushActive)
            } header: { Text("Este iPhone") } footer: {
                Text(flow.pushAvailable
                    ? "Activa los avisos y prueba la recepción con la aplicación cerrada. La bandeja conserva tus avisos aunque desactives push."
                    : "La bandeja funciona. Los avisos en pantalla bloqueada requieren habilitar push en el servidor y una versión del iPhone firmada para recibirlos.")
            }
            Section("Tipos de aviso") {
                ForEach(kinds, id: \.0) { kind, label in
                    Toggle(label, isOn: Binding(get: { flow.preferences[kind] ?? true }, set: { value in Task { await flow.preference(kind, enabled: value) } }))
                }
            }
        }.navigationTitle("Notificaciones").navigationBarTitleDisplayMode(.inline)
    }
}


struct TeamsView: View {
    @EnvironmentObject var flow: FlowSession
    @State private var page: TeamPage?
    @State private var section = 0
    @State private var phase = "upcoming"
    @State private var query = ""
    @State private var loading = false
    @State private var failure: String?
    var body: some View {
        List {
            Picker("Teams", selection: $section) {
                Text("Coberturas").tag(0); Text("Equipo").tag(1)
            }.pickerStyle(.segmented)
            if let page, page.enabled {
                if section == 0 {
                    Picker("Eventos", selection: $phase) {
                        Text("Próximos").tag("upcoming"); Text("Pasados").tag("past"); Text("Todos").tag("all")
                    }
                    let jobs = page.jobs.filter { (phase == "all" || $0.phase == phase) && (query.isEmpty || $0.name.localizedCaseInsensitiveContains(query)) }
                    ForEach(jobs) { job in
                        NavigationLink { TeamJobDetail(job: job) } label: {
                            VStack(alignment: .leading, spacing: 6) {
                                Text(job.name).fontWeight(.semibold)
                                Text(MobileURL.weddingDate(job.date)?.formatted(MobileURL.dayStyle) ?? "Fecha pendiente")
                                    .font(.subheadline).foregroundStyle(.secondary)
                                Text("\(job.assignments.count) coberturas · \(job.assignments.filter { $0.status == "aceptada" || $0.status == "realizada" }.count) aceptadas")
                                    .font(.footnote).foregroundStyle(.secondary)
                            }.padding(.vertical, 4)
                        }
                    }
                    if jobs.isEmpty { ContentUnavailableView("Sin coberturas", systemImage: "person.3", description: Text("No hay eventos de Teams en este filtro.")) }
                } else {
                    ForEach(page.members.filter { query.isEmpty || $0.name.localizedCaseInsensitiveContains(query) }) { member in
                        NavigationLink { TeamMemberDetail(member: member, jobs: page.jobs) } label: {
                            VStack(alignment: .leading, spacing: 6) {
                                Text(member.name).fontWeight(.semibold)
                                Text(member.role + (member.active ? "" : " · Inactivo")).font(.subheadline).foregroundStyle(.secondary)
                            }.padding(.vertical, 4)
                        }
                    }
                    if page.members.isEmpty { ContentUnavailableView("Tu equipo", systemImage: "person.3", description: Text("Los miembros que agregues en Teams aparecerán aquí.")) }
                }
            } else if page != nil {
                ContentUnavailableView("Teams aún no está habilitado", systemImage: "person.3", description: Text("Activa Teams en el servidor de esta cuenta para consultar tus coberturas."))
            }
            if loading { ProgressView("Cargando Teams…") }
            if let failure { Text(failure); Button("Reintentar") { Task { await load() } } }
        }
        .navigationTitle("Teams").toolbar { InboxButton() }
        .searchable(text: $query, prompt: section == 0 ? "Buscar evento" : "Buscar miembro")
        .refreshable { await load() }.task { await load() }
    }
    private func load() async {
        loading = true; failure = nil
        defer { loading = false }
        do { page = try await flow.call("/api/mobile/teams") }
        catch { page = nil; failure = error.localizedDescription }
    }
}

struct TeamJobDetail: View {
    @EnvironmentObject var flow: FlowSession
    let job: TeamJob
    var body: some View {
        List {
            Section {
                Text(job.name).font(.title2.weight(.semibold))
                Text(MobileURL.weddingDate(job.date)?.formatted(MobileURL.dayStyle) ?? "Fecha pendiente")
                if !job.location.isEmpty { Label(job.location, systemImage: "mappin.and.ellipse") }
                if job.closed { Label("Operación cerrada", systemImage: "checkmark.circle") }
            }
            Section("Equipo asignado") {
                ForEach(job.assignments) { assignment in TeamAssignmentRow(assignment: assignment) }
                if job.assignments.isEmpty { Text("Todavía no hay miembros asignados.").foregroundStyle(.secondary) }
            }
            Section("Importes de Teams") {
                LabeledContent("Costo registrado") { Text(teamMoney(job.cost)).foregroundStyle(Color(uiColor: .label)) }
                LabeledContent("Por pagar al equipo") { Text(teamMoney(job.pending)).foregroundStyle(Color(uiColor: .label)) }
                if job.incomplete { Label("Revisión de costos pendiente", systemImage: "exclamationmark.circle").foregroundStyle(.secondary) }
            }
            Section {
                if let base = flow.base {
                    Link("Gestionar cobertura en Flowing", destination: base.appendingPathComponent("teams/jobs").appendingPathComponent(job.id))
                }
                Text("Asignaciones, invitaciones y pagos se gestionan en la web. La app muestra su estado actualizado al abrir Teams.")
                    .font(.footnote).foregroundStyle(.secondary)
            }
        }.navigationTitle("Cobertura").navigationBarTitleDisplayMode(.inline)
    }
}

private func teamMoney(_ cents: Int) -> String {
    (Decimal(cents) / 100).formatted(.currency(code: "GTQ"))
}

struct TeamAssignmentRow: View {
    let assignment: TeamAssignment
    private var status: String {
        ["borrador": "Borrador", "pendiente": "Por responder", "aceptada": "Aceptada", "rechazada": "Rechazada", "reconfirmar": "Por reconfirmar", "realizada": "Realizada", "cancelada": "Cancelada"][assignment.status] ?? assignment.status
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(assignment.name).font(.headline)
            Text(assignment.role).foregroundStyle(.secondary)
            Text(status).font(.subheadline.weight(.medium))
            if assignment.schedule_pending { Text("Horario pendiente").font(.footnote).foregroundStyle(.secondary) }
            else {
                Text("\(teamMoment(assignment.start)) – \(teamMoment(assignment.end))").font(.footnote).foregroundStyle(.secondary)
            }
            if assignment.changed { Label("Fecha modificada: revisa la cobertura", systemImage: "exclamationmark.triangle").font(.footnote) }
            if assignment.conflicts > 0 { Label("Conflicto de cobertura", systemImage: "exclamationmark.triangle").font(.footnote) }
            if let amount = assignment.amount { LabeledContent("Honorarios") { Text(teamMoney(amount)).foregroundStyle(Color(uiColor: .label)) }.font(.subheadline) }
            if let pending = assignment.pending { LabeledContent("Por pagar") { Text(teamMoney(pending)).foregroundStyle(Color(uiColor: .label)) }.font(.subheadline) }
        }.padding(.vertical, 6)
    }
    private func teamMoment(_ value: String) -> String {
        ISO8601DateFormatter().date(from: value)?.formatted(MobileURL.momentStyle) ?? value
    }
}

struct TeamMemberDetail: View {
    let member: TeamMember
    let jobs: [TeamJob]
    var body: some View {
        List {
            Section {
                Text(member.name).font(.title2.weight(.semibold))
                Text(member.role)
                if !member.active { Text("Miembro inactivo").foregroundStyle(.secondary) }
            }
            Section("Honorarios") {
                LabeledContent("Pagado") { Text(teamMoney(member.paid)).foregroundStyle(Color(uiColor: .label)) }
                LabeledContent("Por pagar") { Text(teamMoney(member.pending)).foregroundStyle(Color(uiColor: .label)) }
                if member.rate_missing { Text("Tarifa pendiente de revisar").foregroundStyle(.secondary) }
            }
            ForEach(jobs.filter { job in job.assignments.contains { $0.member_id == member.id } }) { job in
                Section(job.name) {
                    ForEach(job.assignments.filter { $0.member_id == member.id }) { assignment in TeamAssignmentRow(assignment: assignment) }
                }
            }
        }.navigationTitle("Miembro").navigationBarTitleDisplayMode(.inline)
    }
}


// Navigation geometry follows the existing sidebar SVGs in templates/base.html.
// Template rendering keeps the system tab tint and selected state.
enum WebNavigationIcon {
    static func image(_ kind: String) -> UIImage {
        UIGraphicsImageRenderer(size: CGSize(width: 24, height: 24)).image { renderer in
            let c = renderer.cgContext
            c.setStrokeColor(UIColor.black.cgColor)
            c.setLineWidth(1.6); c.setLineCap(.round); c.setLineJoin(.round)
            func line(_ points: [(CGFloat, CGFloat)]) {
                guard let first = points.first else { return }
                c.move(to: CGPoint(x: first.0, y: first.1))
                for point in points.dropFirst() { c.addLine(to: CGPoint(x: point.0, y: point.1)) }
            }
            switch kind {
            case "home":
                line([(3,9),(12,2),(21,9),(21,20)])
                c.addQuadCurve(to: CGPoint(x:19,y:22), control: CGPoint(x:21,y:22))
                line([(19,22),(5,22)])
                c.addQuadCurve(to: CGPoint(x:3,y:20), control: CGPoint(x:3,y:22))
                c.addLine(to: CGPoint(x:3,y:9))
                line([(9,22),(9,12),(15,12),(15,22)])
            case "leads":
                c.addEllipse(in: CGRect(x:2,y:2,width:20,height:20))
                line([(12,6),(12,12),(16,14)])
            case "calendar":
                c.addPath(CGPath(roundedRect: CGRect(x:3,y:4,width:18,height:18), cornerWidth:2, cornerHeight:2, transform:nil))
                line([(16,2),(16,6)]); line([(8,2),(8,6)]); line([(3,10),(21,10)])
            case "teams":
                c.addEllipse(in: CGRect(x:5,y:3,width:8,height:8))
                line([(2,21),(2,19)])
                c.addCurve(to: CGPoint(x:6,y:15), control1: CGPoint(x:2,y:16.8), control2: CGPoint(x:3.8,y:15))
                c.addLine(to: CGPoint(x:12,y:15))
                c.addCurve(to: CGPoint(x:16,y:19), control1: CGPoint(x:14.2,y:15), control2: CGPoint(x:16,y:16.8))
                c.addLine(to: CGPoint(x:16,y:21))
                line([(22,21),(22,19)])
                c.addCurve(to: CGPoint(x:19,y:15.13), control1: CGPoint(x:22,y:17.2), control2: CGPoint(x:20.8,y:15.6))
                c.move(to: CGPoint(x:15,y:3.13))
                c.addCurve(to: CGPoint(x:15,y:10.88), control1: CGPoint(x:19,y:4.1), control2: CGPoint(x:19,y:9.9))
            default:
                line([(4,6),(20,6)]); line([(4,12),(20,12)]); line([(4,18),(20,18)])
            }
            c.strokePath()
        }.withRenderingMode(.alwaysTemplate)
    }
}
