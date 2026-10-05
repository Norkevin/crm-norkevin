# Diseño de Flow CRM

Plataforma de trabajo moderna y luminosa. La jerarquía, la alineación y el contraste dan elegancia; se descartan fondos crema, verdes grisáceos y tratamientos editoriales en el portal.

## Sistema compartido

`static/flow-tokens.css` concentra la paleta, tipografía, escala, estados y compatibilidad con los nombres `--sn-*`. Manrope local, títulos de 28–32 px, cuerpo de 14–16 px, secundarios legibles y numerales tabulares.

Fondo #F7F9FC, superficies blancas, texto #172B4D, secundarios #596A80 y bordes #E2E8F0. CRM usa azul #2563EB y Teams violeta #7252C7. Comparten superficies, controles, proporciones y sidebar #142536. La identidad de las empresas y sus documentos se conserva; el portal toma el acento configurado por empresa.

Controles de 40 px en escritorio y al menos 44 px en móvil. Radios de 8–10 px en controles y 12–16 px en paneles. Bordes finos, sombras reservadas para menús y diálogos, foco visible, estados seleccionados, carga, error y deshabilitado. El modo oscuro es opcional y usa los mismos componentes.

## Composición

Listas con filtros en una banda, nombres legibles y estados con semántica. Detalles con una acción principal, acciones secundarias visibles y evento separado. Dashboard y pagos mantienen cálculos, períodos y significados; los colores de alerta no representan series financieras neutrales. Teams mantiene visibles costos incompletos y márgenes parciales.

Portal con saludo sans de 32 px, evento compacto, recorrido legible, siguiente acción real, documentos por pestañas y resumen de pagos. Login con una superficie dividida e integrada, marca y Google como único acceso. Calendario con controles agrupados y un botón de fecha accesible por día.

## Adaptación y seguridad

Español en la interfaz y fechas completas como «5 de octubre de 2026», sin alterar fechas almacenadas ni zonas horarias. Los ejes de gráficas mantienen abreviaturas españolas dentro de su período visible. Los campos de fecha conservan su control nativo.

Móvil mantiene el encabezado y el menú estables, conserva la posición de las listas, respeta movimiento reducido y permite alcanzar los botones de los diálogos. No se modifican rutas, permisos, aislamiento, datos, workflows, cálculos ni comunicaciones como parte del diseño. Las pruebas usan datos sintéticos y no envían mensajes.
