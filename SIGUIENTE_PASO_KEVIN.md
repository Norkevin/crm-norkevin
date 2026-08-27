# Siguiente paso — actualizado 26 de agosto de 2026

Estado del código: **`WINDOWS_VALIDATED_GREEN`**

Corrida de Windows del 26-ago (`20260826_181617`): **14/14 fases PASS**,
`exit_code 0`, gate `READY_FOR_CONTROLLED_CUTOVER`. 742 tests en
`full_suite`, 222 en `daily_usage`, cero fallas en ningún log. Correo
saliente sigue apagado (`DISABLE_OUTBOUND_EMAIL`, sin envíos reales en
`mail_outbox`). Cubre, además de lo del 21-ago (más abajo): responsive
completo, consistencia visual, la ficha de cliente con el modelo N y
fricción de uso diario (estados vacíos, dashboard con ambos novios, sin
marcas fijas en datos por defecto).

No hay ninguna acción pendiente de Kevin en esta sección. Sigo directo con
el siguiente bloque del backlog (`POST_CUTOVER_BACKLOG.md`: locación,
orden/filtros, y los puntos que quedaron abiertos de clientes múltiples y
estados de pago) sin esperar otra autorización.

Lo de abajo queda como registro histórico de la corrida del 21-ago.

---

## 1. La corrida final del 21-ago (ya ejecutada, referencia histórica)

Doble clic en **`run_windows_validation_launcher.bat`**.

Tarda entre 1 y 3 minutos y no pide nada. Cubre las 14 fases más la suite
completa. La corrida anterior (`20260821_030147`) cerró **13 de 14 en
verde**; el único fallo fue `full_suite`, con 3 tests, y los tres ya están
corregidos:

| Test | Causa | Corrección |
|---|---|---|
| `test_no_double_quoted_onclick_with_tojson` | **Bug real.** El botón "Quitar" de un cliente tenía `onclick="…\|tojson…"` con comillas dobles: `tojson` mete comillas dobles y cortaba el atributo. El botón no hacía nada y no daba error visible | Comillas simples. Y la guarda ahora vigila cualquier `on*=`, no sólo `onclick` — porque el mismo bug estaba también en el `onchange` del selector de rol |
| `test_job_page_shows_add_buttons_for_empty_secondary_and_planner_slots` | Test viejo. Buscaba los dos botones fijos del modelo de 3 slots | Ahora comprueba `+ Agregar cliente` y que estén los cinco roles del modelo N |
| `test_job_page_shows_linked_secondary_and_planner_with_their_info` | Test viejo. Buscaba la etiqueta `(Segundo cliente)` | Ahora comprueba nombre, correo, **teléfono** y el rol `Pareja`, y que a `pareja` **no** se le muestre "no recibe documentos" (sí recibe) |

## 2. Reiniciar el CRM

Cerrar la ventana del CRM y volver a hacer doble clic en
**`abrir_crm.bat`**.

El proceso que está sirviendo `localhost:8765` arrancó a las **02:55** y,
con `FLASK_DEBUG=0`, no recarga solo. Todo lo de hoy —la ficha de cliente
arreglada, las páginas de error, el resumen de pagos fuera de las
pestañas, el filtro de workflows por marca— entra recién con el reinicio.

Sin riesgo: los datos viven en `data/`, no en el proceso. El correo
saliente sigue apagado (STAGE 1) y el `.bat` mata primero lo que esté
ocupando el puerto 8765, así que no vuelve a pasar lo del 21-ago (el
proceso viejo sirviendo código viejo sin avisar).

---

## 3. Los 5 chequeos visuales

Son cosas que sólo se confirman mirando. Cinco, ni uno más.

### 1. Ficha de una boda con 4 personas
Abrir una boda y agregar cuatro clientes con roles distintos (principal,
pareja, wedding planner, contacto).

- ¿Se ven los cuatro, cada uno con su rol en un desplegable?
- ¿El botón **Quitar** funciona? *(éste es el que estaba roto)*
- ¿Cambiar el rol en el desplegable lo guarda?
- ¿Al planner y al contacto les aparece **"no recibe documentos"**, y a la
  pareja **no**?

### 2. El resumen de pagos no se esconde
En esa misma boda, ir cambiando entre las pestañas Facturas →
Cotizaciones → Contratos → Cuestionarios.

- ¿El bloque **Resumen de pagos** (total, pagado, pendiente, próximo pago)
  se queda visible en todas?

### 3. La ficha de la novia muestra su boda
Abrir la ficha del cliente que agregaste como **pareja** (no el
principal).

- ¿Aparece la boda en su ficha?
- ¿Aparecen también las cuotas y el contrato de esa boda?

*Este es el arreglo más importante del día: antes decía que esa persona no
tenía ninguna boda.*

### 4. Las dos marcas, lado a lado
Entrar con Astral, mirar dashboard y `/settings`. Salir, entrar con
Norkevin Photography, lo mismo.

- ¿La **pestaña del navegador** dice el nombre correcto en cada una?
  *(antes las 24 pantallas decían "ASTRAL WEDDINGS CRM")*
- ¿La actividad reciente del dashboard es sólo de esa marca?
- ¿El contador de workflows de Settings cuenta sólo lo suyo?

### 5. Un enlace roto
Escribir a mano `http://localhost:8765/jobs/esto-no-existe`.

- ¿Sale la página **404 del CRM**, con menú y botón "Volver al inicio"?
  *(antes salía la pantalla blanca de Flask, sin salida)*

---

## Lo que no toqué, a propósito

Correo real (sigue apagado), dinero real, pagos históricos, contratos
firmados, reseteo de datos, secretos, despliegue externo, e infraestructura
nueva. La migración a SQLite sigue diferida a propósito.

---

# Actualización — trabajo de producto del 21-ago

Después de los tres arreglos de arriba seguí con responsive, consistencia
y la ficha del cliente. Todo esto entra en la **misma** corrida de
validación: no hace falta una segunda.

## Móvil

El CRM ya tenía barra inferior y listas tipo tarjeta, pero faltaban tres
cosas sistémicas:

| Qué pasaba | Arreglo |
|---|---|
| **Cada campo hacía zoom en el iPhone.** Safari hace zoom automático al enfocar un input de menos de 16px, y todos los del CRM estaban en 13px. Era la fricción más repetida en teléfono | 16px en móvil, en el CRM y en las páginas del cliente |
| `.sn-btn-sm` medía **25px**. Es lo que usa la ficha del job para el rol del cliente, Quitar, Editar y + Agregar | 40px, con los demás controles a 44px |
| Los radio del **cuestionario** medían 18px. Lo contesta el cliente desde el teléfono | 24px |
| Tablas de 5 y 7 columnas con scroll lateral: dashboard, Equipo, Pagos a equipo, Configuración, y el plan de pago de la **cotización que ve el cliente** | Todas apilan como ficha etiqueta/valor |
| Las etiquetas del calendario en 9px | 11px |

## Un solo idioma

134 textos: las pestañas decían "Facturas" y la tarjeta de adentro
"Invoices"; Equipo tenía "First Name / Last Name / Date Created"; el
dashboard, "Job / Client / Status / Progress". Solo se tocó texto
visible — ningún `name=`, `value=` ni identificador de JavaScript.

## Ficha del cliente

Antes mostraba nombre, fecha y una barra de avance del workflow — que no
es el estado del evento. Ahora, para cada boda:

- **qué rol** tiene esa persona (y si no recibe documentos);
- **estado real** del evento, de `_job_estado_label`;
- **cuánto falta por cobrar**, de `_job_payment_summary`;
- **con quién más** comparte la boda, con enlace a cada ficha;
- y un resumen arriba: bodas activas, pendiente total, próximo pago.

## Chequeo visual extra

Se suma uno a los cinco de arriba: **abrí el CRM desde el teléfono** y
recorré dashboard, lista de trabajos, una boda y una ficha de cliente.
Y abrí una cotización desde WhatsApp para ver el plan de pago apilado.
