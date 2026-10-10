import SwiftUI
import Charts

struct RootView: View {
    @EnvironmentObject var flow: FlowSession
    var body: some View {
        Group {
            if flow.connected {
                TabView {
                    NavigationStack { HomeView() }
                        .tabItem { Label("Inicio", systemImage: "house") }
                    NavigationStack { RecordsView(kind: "leads", title: "Leads") }
                        .tabItem { Label("Leads", systemImage: "person.2") }
                    NavigationStack { RecordsView(kind: "jobs", title: "Trabajos") }
                        .tabItem { Label("Trabajos", systemImage: "camera") }
                    NavigationStack { CalendarView() }
                        .tabItem { Label("Calendario", systemImage: "calendar") }
                    NavigationStack { MoreView() }
                        .tabItem { Label("Más", systemImage: "line.3.horizontal") }
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
            Section("Tu Flowing") {
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
            } footer: {
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
                        StudioMetric(title: "Trabajos activos", value: String(overview.job_count), symbol: "camera")
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
                if let date = MobileURL.weddingDate(record.date) {
                    Text(date, format: MobileURL.dayStyle).font(.footnote).foregroundStyle(.secondary)
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
                if let date = MobileURL.weddingDate(record.date) {
                    Label(date.formatted(MobileURL.dayStyle), systemImage: "calendar")
                }
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
    @State private var selected = Date()
    @State private var records: [CRMRecord] = []
    @State private var loading = false
    @State private var loadError: String?
    private var calendar: Calendar {
        var value = Calendar(identifier: .gregorian)
        value.timeZone = TimeZone(identifier: "America/Guatemala")!
        return value
    }
    var body: some View {
        List {
            DatePicker("Fecha", selection: $selected, displayedComponents: .date)
                .datePickerStyle(.graphical)
                .environment(\.timeZone, calendar.timeZone)
            Section(selected.formatted(MobileURL.dayStyle)) {
                let events = records.filter { MobileURL.weddingDate($0.date).map { calendar.isDate($0, inSameDayAs: selected) } ?? false }
                ForEach(events) { record in NavigationLink { RecordDetail(record: record) } label: { RecordRow(record: record) } }
                if loading { ProgressView("Cargando calendario…") }
                else if let loadError { Text(loadError); Button("Reintentar") { Task { await load() } } }
                else if events.isEmpty { Text("No hay eventos para esta fecha.").foregroundStyle(.secondary) }
            }
        }.navigationTitle("Calendario").toolbar { InboxButton() }
            .refreshable { await load() }.task { await load() }
    }
    private func load() async {
        loading = true; loadError = nil
        defer { loading = false }
        do {
            var all: [CRMRecord] = []
            for kind in ["jobs", "calendar"] {
                var offset: Int? = 0
                while let current = offset {
                    let page: RecordPage = try await flow.call("/api/mobile/records/\(kind)?offset=\(current)")
                    all.append(contentsOf: page.records); offset = page.next_offset
                }
            }
            records = all
        } catch { loadError = error.localizedDescription }
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
                                .font(.footnote).foregroundStyle(.secondary)
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
                        Text(payment.status + " · " + payment.date).font(.footnote).foregroundStyle(.secondary)
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
            Section("Este iPhone") {
                Toggle("Ocultar detalles en pantalla bloqueada", isOn: Binding(get: { flow.hideDetails }, set: { value in Task { await flow.privacy(value) } }))
                Button(flow.pushActive ? "Actualizar permiso y privacidad" : "Activar notificaciones") { Task { await flow.enablePush() } }
                Button("Enviar prueba a este iPhone") { Task { await flow.pushTest() } }
                Button("Desactivar avisos en este iPhone", role: .destructive) { Task { await flow.disablePush() } }
            } footer: { Text("Activa los avisos y prueba la recepción con la aplicación cerrada. Los avisos se conservan en la bandeja aunque desactives push.") }
            Section("Tipos de aviso") {
                ForEach(kinds, id: \.0) { kind, label in
                    Toggle(label, isOn: Binding(get: { flow.preferences[kind] ?? true }, set: { value in Task { await flow.preference(kind, enabled: value) } }))
                }
            }
        }.navigationTitle("Notificaciones").navigationBarTitleDisplayMode(.inline)
    }
}
