# Catálogo de venues y propuestas comerciales de viáticos

Fecha de consulta: **13 de octubre de 2026**, Guatemala. Archivo de datos versionado: `data/seeds/venues_guatemala.default.json`.

## Resultado verificable

La investigación web inicial documentó **55 candidatos**: **48 venues con ubicación corroborada** y **7 candidatos inicialmente retenidos** por datos incompletos, problemas de acceso o contradicciones. La parte verificada cubre **23 municipios y 14 departamentos**. Los alias no se cuentan como venues. Los hostales internos de IRTRA se agrupan en un solo complejo y no inflan el conteo.

El listado posterior del usuario amplía la semilla a **166 lugares: 48 verificados y 118 pendientes**. El catálogo combinado incluye referencias en **20 departamentos**. Los 118 adicionales no tienen tarifa asignada. La ampliación y sus límites se detallan al final de este documento.

De los 48 venues, **35 tienen clasificación comercial aprobada para Norkevin** según las instrucciones del usuario: **23 exentos, 8 de nivel 1, 1 de nivel 2 y 3 de nivel 3**. Otros **13 venues tienen geografía verificada y tarifa provisional**: 5 de nivel 1, 6 de nivel 2 y 2 de nivel 3. Se incluyen además **9 propuestas de reglas de destino** pendientes de aprobación.

**Ninguna aprobación se traslada a Astral.** El campo `policy_brand` indica `norkevin`. El consumidor debe crear o conservar políticas independientes por marca.

## Criterio de verificación y límites

1. Se priorizaron sitios de los establecimientos, sus motores oficiales de reservas y el directorio del Buró de Convenciones de Guatemala. Cada registro guarda `source_url` y `source_urls`. El directorio común contiene fichas individuales con nombre, dirección y espacios de eventos; no se usó una dirección genérica del Buró como dirección del hotel.
2. `verification_status=verified` indica que la identidad y la ubicación consignadas tienen evidencia concordante. No certifica el estado de operación del negocio, disponibilidad para una boda específica, tarifas del hotel ni condiciones de uso de sus espacios.
3. Se guardaron direcciones con la precisión que permite la evidencia. Jardines del Lago y Villa Santa Catarina tienen municipio y entorno identificados, sin inventar números de calle. Casa Xara se identifica por Molino Helvetia y Tecpán; la distancia publicada a la capital no se transformó en dirección kilométrica.
4. Solo La Casa del Mundo tiene coordenadas incorporadas: **14.73600483, -91.21614838**, publicadas en los datos estructurados de su motor de reservas Cloudbeds, enlazado desde su sitio. Los otros 47 venues guardan coordenadas nulas. No se usaron centroides municipales como coordenadas del venue.
5. Algunos proveedores devolvieron 403, timeout o páginas que requieren JavaScript. Se conservan las limitaciones en `verification_notes` cuando la corroboración secundaria solo estuvo disponible indexada. No se registraron como verificados candidatos cuya única evidencia quedó incompleta.

Las fuentes comprueban geografía; **no aprueban precios de Norkevin**. `approval_status` representa la política comercial del usuario. Los niveles propuestos son recomendaciones iniciales para revisión, no fórmulas de kilómetros ni afirmaciones tarifarias ante clientes.

## Las 48 ubicaciones corroboradas

| Venue | Municipio / departamento | Nivel Norkevin | Estado comercial | Fuente |
| --- | --- | --- | --- | --- |
| Finca San Cayetano | San Juan Alotenango / Sacatepéquez | 0 | Aprobado | [Ver ubicación](https://sancayetano.gt/eventos/) |
| Pensativo House Hotel | Antigua Guatemala / Sacatepéquez | 0 | Aprobado | [Ver ubicación](https://www.pensativohousehotel.com/es) |
| Camino Real Antigua | Antigua Guatemala / Sacatepéquez | 0 | Aprobado | [Ver ubicación](https://guatemalacvb.com/wp-content/uploads/sites/46/pdf/20260415-es-hoteles-venues.pdf) |
| Casa Santo Domingo | Antigua Guatemala / Sacatepéquez | 0 | Aprobado | [Ver ubicación](https://guatemalacvb.com/wp-content/uploads/sites/46/pdf/20260415-es-hoteles-venues.pdf) |
| Porta Hotel Antigua | Antigua Guatemala / Sacatepéquez | 0 | Aprobado | [Ver ubicación](https://guatemalacvb.com/wp-content/uploads/sites/46/pdf/20260415-es-hoteles-venues.pdf) |
| Soleil La Antigua | Antigua Guatemala / Sacatepéquez | 0 | Aprobado | [Ver ubicación](https://guatemalacvb.com/wp-content/uploads/sites/46/pdf/20260415-es-hoteles-venues.pdf) |
| Villa Bokéh Hotel Boutique | Antigua Guatemala / Sacatepéquez | 0 | Aprobado | [Ver ubicación](https://guatemalacvb.com/wp-content/uploads/sites/46/pdf/20260415-es-hoteles-venues.pdf) |
| AC Hotel Guatemala | Guatemala / Guatemala | 0 | Aprobado | [Ver ubicación](https://guatemalacvb.com/wp-content/uploads/sites/46/pdf/20260415-es-hoteles-venues.pdf) |
| Adriatika Hotel & Residence | Guatemala / Guatemala | 0 | Aprobado | [Ver ubicación](https://guatemalacvb.com/wp-content/uploads/sites/46/pdf/20260415-es-hoteles-venues.pdf) |
| Barceló Guatemala City | Guatemala / Guatemala | 0 | Aprobado | [Ver ubicación](https://guatemalacvb.com/wp-content/uploads/sites/46/pdf/20260415-es-hoteles-venues.pdf) |
| Casa Veranda | Guatemala / Guatemala | 0 | Aprobado | [Ver ubicación](https://guatemalacvb.com/wp-content/uploads/sites/46/pdf/20260415-es-hoteles-venues.pdf) |
| Courtyard Marriott Guatemala City | Guatemala / Guatemala | 0 | Aprobado | [Ver ubicación](https://guatemalacvb.com/wp-content/uploads/sites/46/pdf/20260415-es-hoteles-venues.pdf) |
| Hilton Garden Inn Guatemala | Guatemala / Guatemala | 0 | Aprobado | [Ver ubicación](https://guatemalacvb.com/wp-content/uploads/sites/46/pdf/20260415-es-hoteles-venues.pdf) |
| Holiday Inn Guatemala | Guatemala / Guatemala | 0 | Aprobado | [Ver ubicación](https://guatemalacvb.com/wp-content/uploads/sites/46/pdf/20260415-es-hoteles-venues.pdf) |
| Hyatt Centric Guatemala City | Guatemala / Guatemala | 0 | Aprobado | [Ver ubicación](https://guatemalacvb.com/wp-content/uploads/sites/46/pdf/20260415-es-hoteles-venues.pdf) |
| Real Intercontinental Guatemala | Guatemala / Guatemala | 0 | Aprobado | [Ver ubicación](https://guatemalacvb.com/wp-content/uploads/sites/46/pdf/20260415-es-hoteles-venues.pdf) |
| The Westin Camino Real Guatemala | Guatemala / Guatemala | 0 | Aprobado | [Ver ubicación](https://guatemalacvb.com/wp-content/uploads/sites/46/pdf/20260415-es-hoteles-venues.pdf) |
| Tikal Futura Hotel & Convention Center | Guatemala / Guatemala | 0 | Aprobado | [Ver ubicación](https://guatemalacvb.com/wp-content/uploads/sites/46/pdf/20260415-es-hoteles-venues.pdf) |
| Wyndham Garden Guatemala | Guatemala / Guatemala | 0 | Aprobado | [Ver ubicación](https://guatemalacvb.com/wp-content/uploads/sites/46/pdf/20260415-es-hoteles-venues.pdf) |
| Casa Bugambilia | San Lucas Sacatepéquez / Sacatepéquez | 0 | Aprobado | [Ver ubicación](https://www.casabugambilia.com.gt/) |
| Casa Azul Event Garden | San Lucas Sacatepéquez / Sacatepéquez | 0 | Aprobado | [Ver ubicación](https://jardincasa-azul.com/) |
| Granja Lucita | Santiago Sacatepéquez / Sacatepéquez | 1 | Provisional | [Ver ubicación](https://granjalucita.com/weddings) |
| Finca La Loma | Chimaltenango / Chimaltenango | 1 | Provisional | [Ver ubicación](https://fincalalomaoficial.com/) |
| Hotel Casa Xara | Tecpán Guatemala / Chimaltenango | 0 | Aprobado | [Ver ubicación](https://casaxara.com/accommodation/jardin-de-eventos/) |
| Hotel Atitlán | Panajachel / Sololá | 1 | Aprobado | [Ver ubicación](https://www.hotelatitlan.com/contact-location) |
| La Casa del Mundo | Santa Cruz La Laguna / Sololá | 1 | Aprobado | [Ver ubicación](https://hotels.cloudbeds.com/en/reservation/EbWilf) |
| Hotel y Centro de Convenciones Jardines del Lago | Panajachel / Sololá | 1 | Aprobado | [Ver ubicación](https://jardinesdellago.com/quienes-somos/) |
| Hotel Tolimán | San Lucas Tolimán / Sololá | 1 | Aprobado | [Ver ubicación](https://hoteltoliman.com/) |
| Regis Hotel & Spa | Panajachel / Sololá | 1 | Aprobado | [Ver ubicación](https://www.regisatitlan.com/contact-location) |
| Porta Hotel del Lago | Panajachel / Sololá | 1 | Aprobado | [Ver ubicación](https://www.portahoteldellago.com/contact-location) |
| Casa Palopó | Santa Catarina Palopó / Sololá | 1 | Aprobado | [Ver ubicación](https://casapalopo.com/connect/) |
| Hotel Villa Santa Catarina | Santa Catarina Palopó / Sololá | 1 | Aprobado | [Ver ubicación](https://www.villasdeguatemala.com/es/eventos/villa-santa-catarina) |
| Los Hostales del IRTRA Retalhuleu | San Martín Zapotitlán / Retalhuleu | 2 | Aprobado | [Ver ubicación](https://irtra.org.gt/wp-content/uploads/2022/03/InformedeOperaciones2024.pdf#page=10) |
| Hotel Camino Real Tikal | San José / Petén | 3 | Aprobado | [Ver ubicación](https://www.caminorealtikal.com.gt/) |
| Las Lagunas Boutique Hotel | Flores / Petén | 3 | Aprobado | [Ver ubicación](https://www.laslagunashotel.com/las-lagunas-boutique-hotel-contacto) |
| Hotel Villa Maya | Flores / Petén | 3 | Aprobado | [Ver ubicación](https://www.villasdeguatemala.com/es/eventos/villa-maya) |
| Hotel Villa Colonial | Antigua Guatemala / Sacatepéquez | 0 | Aprobado | [Ver ubicación](https://www.villasdeguatemala.com/es/eventos/villa-colonial) |
| Hotel Villa Caribe | Livingston / Izabal | 3 | Provisional | [Ver ubicación](https://www.villasdeguatemala.com/es/eventos/villa-caribe) |
| Hotel Verapaz | Salamá / Baja Verapaz | 1 | Provisional | [Ver ubicación](https://hotelverapaz.com/es/events/) |
| Hotel La Posada de Cobán | Cobán / Alta Verapaz | 2 | Provisional | [Ver ubicación](https://laposadacoban.com.gt/contacto/) |
| Park Hotel Resort | Santa Cruz Verapaz / Alta Verapaz | 2 | Provisional | [Ver ubicación](https://parkhotelresort.com/) |
| Latam Hotel Plaza Pradera Quetzaltenango | Quetzaltenango / Quetzaltenango | 2 | Provisional | [Ver ubicación](https://www.ghlhoteles.com/es/hoteles/guatemala/quetzaltenango/latam-hotel-plaza-pradera-quetzaltenango/) |
| Hotel Ruinas Resort | Huehuetenango / Huehuetenango | 3 | Provisional | [Ver ubicación](https://magaesla.wixsite.com/ruinasresort/salones) |
| Vista Terra | Chiquimula / Chiquimula | 2 | Provisional | [Ver ubicación](https://www.vistaterra.com.gt/) |
| Grand Caporal Hotel | Chiquimula / Chiquimula | 2 | Provisional | [Ver ubicación](https://www.grandcaporalhotel.com/) |
| Portal Toscana Hotel Chiquimula | Chiquimula / Chiquimula | 2 | Provisional | [Ver ubicación](https://portaltoscanahotelchiquimula.com/eventos-sociales-y-corporativos/) |
| Hotel y Turicentro Teculután | Teculután / Zacapa | 1 | Provisional | [Ver ubicación](https://www.guatemala.com/guias/hoteles/hotel-y-turicentro-teculutan-un-destino-con-piscinas-en-zacapa/) |
| Oceana Resort + Conventions | Iztapa / Escuintla | 1 | Provisional | [Ver ubicación](https://www.oceana.com.gt/) |

## Propuestas de destinos que necesitan aprobación

Los importes permitidos siguen siendo únicamente estos: nivel 1 Q1,500/Q2,500; nivel 2 Q2,000/Q3,500; nivel 3 Q2,500/Q4,500, en orden fotografía / fotografía y video. Esta tabla **no autoriza su aplicación automática**.

| Destino | Nivel propuesto | Justificación y alcance |
| --- | --- | --- |
| Salamá | 1 | Corredor de Baja Verapaz; propuesta inicial cercana, confirmar jornada y horario del evento. [Fuente geográfica](https://hotelverapaz.com/es/events/) |
| Zacapa | 1 | Propuesta cercana para la cabecera por corredor de Oriente; no cubre automáticamente todo el departamento. [Fuente geográfica](https://inguat.gob.gt/es/descargas-inguat-guatemala/9-planes-de-desarrollo-departamentales-y-municipales.html?download=980%3Apdt-zacapa-2023-2027) |
| Teculután | 1 | Corredor CA-9 con venue documentado en km 121; confirmar logística y horario. [Fuente geográfica](https://www.guatemala.com/guias/hoteles/hotel-y-turicentro-teculutan-un-destino-con-piscinas-en-zacapa/) |
| Cobán | 2 | Propuesta intermedia para acceso al altiplano de las Verapaces; revisar jornada y alojamiento. [Fuente geográfica](https://laposadacoban.com.gt/contacto/) |
| Quetzaltenango | 2 | Propuesta intermedia para Quetzaltenango/Xela; no extender al departamento completo. [Fuente geográfica](https://www.ghlhoteles.com/es/hoteles/guatemala/quetzaltenango/latam-hotel-plaza-pradera-quetzaltenango/) |
| Huehuetenango | 3 | Propuesta lejana por jornada y posible hospedaje; aprobar política antes de cotizar. [Fuente geográfica](https://magaesla.wixsite.com/ruinasresort/salones) |
| Chiquimula | 2 | Propuesta intermedia para Chiquimula; municipios del departamento se revisan por separado. [Fuente geográfica](https://www.grandcaporalhotel.com/) |
| Izabal | 3 | Propuesta regional lejana. Confirmar municipio y transporte terrestre/acuático; Livingston necesita revisión logística. [Fuente geográfica](https://www.villasdeguatemala.com/es/eventos/villa-caribe) |
| Escuintla | 1 | Propuesta cercana para la cabecera y revisión individual de costa. No asumir misma logística en todo el departamento. [Fuente geográfica](https://www.oceana.com.gt/) |

La propuesta de Escuintla distingue cabecera y venues costeros individualmente. La propuesta de Izabal requiere destino concreto y logística: Livingston puede implicar acceso acuático. Las fuentes de turismo respaldan la ubicación y el corredor; la elección de nivel sigue siendo una inferencia comercial sujeta a aprobación del usuario. No se calcularon tiempos en vivo ni costos reales de vehículo/hospedaje.

## Ambigüedades resueltas y candidatos retenidos

- **Finca San Cayetano**: el [sitio oficial guatemalteco](https://sancayetano.gt/eventos/) confirma km 89.5 RN-14, San Juan Alotenango. Su exención se guarda como decisión específica del usuario. No se extiende a Alotenango ni a toda la RN-14.
- **La Casa del Mundo**: Jaibalito es la localidad; el municipio es Santa Cruz La Laguna. Se guardan ambos. Su acceso habitual por lancha o a pie no invalida el nivel 1 aprobado expresamente para ese venue.
- **Hotel Tolimán**: San Lucas Tolimán, Sololá, tiene nivel 1. No se confunde con San Lucas Sacatepéquez, exento.
- **IRTRA**: el registro cargado es Los Hostales del IRTRA Retalhuleu en San Martín Zapotitlán. El alias genérico `IRTRA` se omite porque la institución también tiene sedes en Ciudad de Guatemala y Amatitlán. El [informe institucional](https://irtra.org.gt/wp-content/uploads/2022/03/InformedeOperaciones2024.pdf) diferencia esos centros.
- **Camino Real Tikal**: la dirección oficial dice parcelamiento Tayasa, San José, Petén, y también utiliza El Remate como referencia postal. Se conserva San José como municipio y se documenta la referencia; no se trata El Remate como municipio.
- **Park Hotel**: está en Santa Cruz Verapaz, no en Cobán municipio. La política sigue provisional.
- **Oceana**: el sitio oficial confirma Iztapa, Escuintla; “camino a Monterrico” se conserva como ruta y no cambia el municipio.

Los siete candidatos retenidos en la primera investigación fueron los siguientes. Casa Toribia se incorpora después como **pendiente**, gracias al listado del usuario; sus dudas de ubicación permanecen sin resolver. Los otros seis siguen fuera de la semilla.

| Candidato | Evidencia revisada | Motivo de retención |
| --- | --- | --- |
| Finca Filadelfia / Filadelfia Coffee Resort | [Registro INGUAT](https://registro.inguat.gob.gt/download/listado-establecimientos-de-hospedaje-pdf/?wpdmdl=3136), [operador en la finca](https://filadelfiacanopytours.com/pages/contact) | Registro indexado identifica Jocotenango; varios sitios usan Antigua/San Felipe. Requiere delimitar municipio y verificar qué sede de eventos opera actualmente. No se le concede exención por comercializarse como Antigua. |
| Finca La Visión Valparaíso | [Sitio del venue](https://fincalavisionvalparaiso.com/) | Sitio indexado indica Tecpán pero apertura dio 402; falta dirección suficientemente precisa y corroboración adicional. |
| Jardín Entre Cipreses | [Sitio del venue](https://jardinentrecipreses.com/eventos.html) | Dirección visible en índice, apertura 401; sin segunda fuente corroborada. |
| Casa Toribia | [Sitio del venue](https://www.casatoribia.com/) | Nombre y dirección en índice, apertura timeout; sin segunda fuente corroborada. |
| Centro Recreativo Las Fuentes | [Sitio del venue](https://lasfuenteshuehue.com/) | Sitio indexado indica Chiantla, Huehuetenango; apertura devolvió cuerpo vacío. No se clasifica como municipio Huehuetenango. |
| Hotel Costa Verde | [Sitio del hotel](https://www.hotelcostaverdegt.com/cotizareventos.htm) | Página de eventos accesible, pero dirección solo visible indexada; falta corroboración de municipio y ubicación exacta en Chulamar. |
| Hotel Pasabién | [Artículo turístico](https://www.guatemala.com/guias/hoteles/hotel-pasabien-zacapa-hospedaje-familiar-piscinas-toboganes/), [plan turístico INGUAT](https://inguat.gob.gt/es/descargas-inguat-guatemala/9-planes-de-desarrollo-departamentales-y-municipales.html?download=980%3Apdt-zacapa-2023-2027) | Artículo usa Teculután; documento INGUAT indexado sitúa Pasabién en Santa Cruz, Río Hondo. No se elige el primer resultado. |

## Comprobaciones del archivo

Se comprobó que la semilla combinada tiene **166 IDs únicos**. Los 48 verificados conservan país `GT`, niveles enteros 0–3, sus fuentes, fechas y estados comerciales. Los 118 pendientes tienen nivel, municipio, dirección, coordenadas y fecha de verificación nulos. Todos conservan su fuente principal dentro de la lista de fuentes. Solo un venue tiene coordenadas publicadas y no hay tarifas intermedias en el catálogo.

## Ampliación con el listado del usuario

Se leyó `lugares_bodas_guatemala.xlsx`, hojas `Lugares`, `Fuentes` y `Alcance`, sin modificar el original. Contiene **141 filas en 19 departamentos**, con una URL por fila. El cruce completo y la procedencia se conservan en [viaticos-listado-cruce.json](viaticos-listado-cruce.json).

- **23 coincidencias existentes** se reutilizan. Se añade únicamente el alias faltante `Park Hotel` a Park Hotel Resort y 21 URLs complementarias. Se conserva la fuente principal verificada y toda la política anterior.
- **118 lugares nuevos** se incorporan como `pending` y `proposed`, con `travel_level=null`. Sus departamentos son los declarados por el listado y todavía necesitan verificación junto con municipio y dirección.
- **50 referencias históricas de 2020** pertenecen a esos 118 nuevos. Las notas visibles en el editor conservan esta advertencia y la fila de origen.
- Los **25 venues originales ausentes del Excel** se mantienen. No se reemplaza el catálogo con el listado ni se eliminan establecimientos por no aparecer en él.

El archivo tiene 33 referencias clasificadas como sitio oficial, 30 de proveedores de bodas, 12 de directorios especializados, 9 de directorios de hoteles para bodas, 6 de redes sociales, una de registro de boda y 50 históricas. Estos tipos son metadatos del archivo, **no nuevas verificaciones independientes**. Durante el cruce solo se consultó adicionalmente la [página oficial de Park Hotel](https://parkhotelresort.com/) para resolver su variación de nombre y la ubicación de Santa Cruz Verapaz.

Se mantienen separados Santo Domingo del Cerro y Casa Santo Domingo, y Villa Colonial Jalapa y Villa Colonial de Antigua. Casa Tecpán La Colina no recibe exención por su nombre. Tampoco se convierte una referencia general al departamento Guatemala en municipio de Ciudad de Guatemala.

Este informe cubre investigación y datos semilla. La integración se probó en bases locales aisladas y sigue sin desplegar ni activar en producción. El estado técnico, los ejemplos completos y la evidencia de pruebas están en [viaticos-implementacion.txt](viaticos-implementacion.txt).
