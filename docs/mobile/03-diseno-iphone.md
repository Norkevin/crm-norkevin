# Dirección de la superficie iPhone

Modo: Operate. Propietario del estudio consulta y revisa acciones de su empresa durante el día. La referencia del usuario guía fondo claro, azul y navegación inferior; controles iOS mantienen sus convenciones.

## Direction contract

THESIS: Flowing acompaña la operación del estudio en iPhone con consultas nativas y avisos que abren el registro correcto.

OWN-WORLD: sistema de Flowing existente, acento azul, SF nativa, superficies semánticas adaptables, listas legibles y símbolos SF. Sin copiar cifras, identidades ni adornos del ejemplo.

STORY: conectar la empresa mediante código, consultar registros y revisar avisos; operaciones aún no migradas abren la ficha del CRM web.

FIRST VIEWPORT: título nativo, marca y empresa, fecha local, cuadrícula de cuatro métricas reales (una columna con texto accesible), gráfica nativa de cobros mensuales y lista de próximos trabajos. Barra inferior Inicio, Leads, Trabajos, Calendario, Más. Bell lleva a la bandeja.

FORM: extensión de la identidad existente y referencia fijada por el usuario; no se creó una identidad visual alternativa. List/Form/NavigationStack/TabView, fecha gráfica del sistema, estados de carga/error/vacío, Dynamic Type y safe areas.

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance.

Validación observada en Simulator iOS 26: sesión nativa conectada al fixture HTTPS sintético, dashboard con métricas del fixture, bandeja y apertura del registro correcto. Capturas reales en `screenshots/` muestran claro, oscuro y texto grande. El runtime se restituyó mediante reimportación oficial después del reinicio; instalación, ejecución y capturas funcionan. La build normal de Simulator con firma ad hoc resolvió Keychain sin cambiar seguridad del código.

La revisión estática de los `Section` fue aprobada. Los ajustes de revisión visual se aplicaron y verificaron: fechas de avisos usan `label`, con contraste 21:1 en claro y 17.01:1 en oscuro, y pagos muestran fecha larga `es_GT`. El dictamen permite entregar estos ajustes dentro del alcance de las capturas reales del Simulator iPhone 16; no equivale a validar toda la superficie. Aún no hay aprobación visual global, iPhone físico, firma de distribución ni prueba push con app cerrada. Esta etapa permite consulta nativa y mantiene operaciones completas en las fichas web. Se conserva `DESIGN.md`; no hubo rediseño global de web ni cambios en tokens.
