"""Clasificacion de enlaces publicos: cuales siguen vivos y cuales no.

PREPARADO, NO ACTIVADO. Nada de esto rota, migra ni desactiva un enlace.
Sirve para decidir con numeros en vez de a ojo.

--------------------------------------------------------------------------
Que cuenta como "activo"
--------------------------------------------------------------------------

Kevin fue explicito: **no se define solo por la fecha de la boda.** Una boda
de hace dos anios con saldo pendiente sigue teniendo un enlace vivo, y una
boda del mes que viene ya cerrada puede no tenerlo.

Un recurso es POTENCIALMENTE ACTIVO si cumple CUALQUIERA de estas:

  1. el job todavia no tiene Job Complete marcado a mano;
  2. hay saldo pendiente;
  3. hay una factura pendiente o parcialmente pagada;
  4. el contrato sigue siendo relevante para consulta;
  5. el cuestionario sigue pendiente;
  6. la cotizacion no esta expirada ni rechazada definitivamente;
  7. el portal del cliente sigue habilitado;
  8. el enlace tuvo actividad reciente, si tenemos ese dato;
  9. el job todavia tiene tareas pendientes.

Cualquiera basta. La logica es deliberadamente pesimista: para declarar algo
inactivo hay que poder demostrarlo, no suponerlo.

--------------------------------------------------------------------------
Las tres respuestas posibles
--------------------------------------------------------------------------

  ACTIVO           -- se cumple al menos una condicion. Rotar su enlace
                      romperia algo que alguien esta usando.
  INACTIVO         -- se pudo evaluar TODO lo que aplica y nada dio activo.
  REVIEW_REQUIRED  -- no se pudo determinar con seguridad.

REVIEW_REQUIRED no es un empate ni un "probablemente no": es la respuesta
correcta cuando falta informacion. Kevin: "si no podemos determinar con
seguridad si algo sigue siendo usado, clasificalo como REVIEW_REQUIRED".
Meterlo en INACTIVO seria convertir una duda en permiso para desactivar.
"""
import os
from datetime import datetime, timedelta

ACTIVO = 'ACTIVO'
INACTIVO = 'INACTIVO'
REVISAR = 'REVIEW_REQUIRED'

# Cuanto tiempo cuenta como "actividad reciente". Configurable porque Kevin
# todavia no decidio el periodo -- y explicitamente pidio no decidirlo ahora.
DIAS_ACTIVIDAD_RECIENTE = int(os.environ.get('PUBLIC_LINK_RECENT_DAYS', '90'))

# Cuanto sobrevive un enlace legacy como alias despues de la migracion.
# Etapa 3: "no quiero decidir todavia cuanto dura ese periodo. Dejalo
# configurable". 0 = sin limite, que es el valor por defecto a proposito:
# ninguna desactivacion ocurre por el mero paso del tiempo si nadie lo pide.
DIAS_ALIAS_LEGACY = int(os.environ.get('LEGACY_LINK_ALIAS_DAYS', '0'))

# Estados de cotizacion que cierran el tema para siempre. Todo lo demas
# (incluido un estado desconocido) deja la cotizacion viva.
QUOTE_CERRADA = {'rechazada', 'rejected', 'declinada', 'declined',
                 'expirada', 'expired', 'superada', 'cancelada', 'cancelled'}

# Estados de cuestionario ya respondido.
CUESTIONARIO_TERMINADO = {'completed', 'completado', 'respondido', 'answered'}


def _fecha(valor):
    """Parsea una fecha tolerando los formatos que conviven en los datos."""
    if not valor:
        return None
    texto = str(valor)[:19]
    for formato in ('%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d'):
        try:
            return datetime.strptime(texto[:len(datetime.now().strftime(formato))],
                                     formato)
        except ValueError:
            continue
    return None


def _reciente(valor, dias=None):
    f = _fecha(valor)
    if f is None:
        return False
    return (datetime.now() - f) <= timedelta(days=dias or DIAS_ACTIVIDAD_RECIENTE)


def _monto(valor):
    try:
        return float(valor or 0)
    except (TypeError, ValueError):
        return 0.0


class Contexto:
    """Lo que hace falta saber alrededor de un recurso para clasificarlo.

    Se pasa explicito en vez de que el modulo consulte el store: asi la
    clasificacion es una funcion pura, se puede probar sin base de datos, y
    no puede leerse por accidente datos de la otra empresa.
    """

    def __init__(self, job=None, pagos=None, tareas_pendientes=None,
                 cliente=None):
        self.job = job
        self.pagos = pagos
        self.tareas_pendientes = tareas_pendientes
        self.cliente = cliente


def _evaluar_job(ctx, razones, dudas):
    """Condiciones 1 y 9: el job y sus tareas."""
    if ctx.job is None:
        dudas.append('no se encontro el job del que cuelga')
        return
    completo = ctx.job.get('job_complete')
    if completo is False:
        razones.append('el job no tiene Job Complete marcado')
    elif completo is None:
        # Nadie lo marco ni en un sentido ni en el otro. La fecha NO alcanza
        # para decidirlo: eso es justo lo que Kevin pidio no hacer.
        dudas.append('Job Complete nunca se marco a mano')

    if ctx.tareas_pendientes is None:
        dudas.append('no se pudo saber si quedan tareas pendientes')
    elif ctx.tareas_pendientes > 0:
        razones.append(f'el job tiene {ctx.tareas_pendientes} tarea(s) pendiente(s)')


def _evaluar_dinero(ctx, razones, dudas):
    """Condiciones 2 y 3: saldo y facturas."""
    if ctx.pagos is None:
        dudas.append('no se pudieron leer los pagos')
        return
    pendiente = sum(_monto(p.get('amount')) for p in ctx.pagos
                    if p.get('status') != 'Pagado')
    if pendiente > 0:
        razones.append(f'queda saldo pendiente ({pendiente:,.2f})')
    parciales = [p for p in ctx.pagos
                 if p.get('status') in ('Pendiente', 'Late', 'Parcial')]
    if parciales:
        razones.append(f'{len(parciales)} factura(s) pendiente(s) o parcial(es)')


def clasificar_quote(quote, ctx):
    """Condicion 6, mas las del job y el dinero."""
    razones, dudas = [], []
    estado = str(quote.get('status') or '').strip().lower()
    if not estado:
        dudas.append('la cotizacion no tiene estado')
    elif estado not in QUOTE_CERRADA:
        razones.append(f'la cotizacion sigue viva (estado "{estado}")')
    _evaluar_job(ctx, razones, dudas)
    _evaluar_dinero(ctx, razones, dudas)
    _evaluar_actividad(quote, razones)
    return _resolver(razones, dudas)


def clasificar_contract(contract, ctx):
    """Condicion 4.

    Un contrato firmado NO deja de ser relevante cuando la boda termina: es
    el documento al que el cliente vuelve si hay un reclamo. Se trata como
    consultable mientras el job no este cerrado, y firmado + job cerrado se
    marca para revision en vez de darlo por muerto -- esa es una decision
    legal, no tecnica.
    """
    razones, dudas = [], []
    firmado = contract.get('signed') or contract.get('signed_at')
    if not firmado:
        razones.append('el contrato todavia no esta firmado')
    else:
        dudas.append('contrato firmado: sigue siendo consultable por el cliente')
    _evaluar_job(ctx, razones, dudas)
    _evaluar_dinero(ctx, razones, dudas)
    _evaluar_actividad(contract, razones)
    return _resolver(razones, dudas)


def clasificar_questionnaire(questionnaire, ctx):
    """Condicion 5."""
    razones, dudas = [], []
    estado = str(questionnaire.get('status') or '').strip().lower()
    if not estado:
        dudas.append('el cuestionario no tiene estado')
    elif estado not in CUESTIONARIO_TERMINADO:
        razones.append(f'el cuestionario sigue pendiente (estado "{estado}")')
    _evaluar_job(ctx, razones, dudas)
    _evaluar_actividad(questionnaire, razones)
    return _resolver(razones, dudas)


def clasificar_portal(cliente, ctx):
    """Condicion 7: el portal del cliente.

    El portal no es de un job sino de una persona, y da acceso a TODO lo
    suyo. Por eso basta con que el cliente tenga un job vivo o dinero
    pendiente para que el portal siga siendo necesario.
    """
    razones, dudas = [], []
    habilitado = cliente.get('portal_enabled')
    if habilitado is False:
        pass  # apagado a mano: no cuenta como activo por si mismo
    elif habilitado is None:
        dudas.append('no hay una marca de portal habilitado/deshabilitado')
    else:
        razones.append('el portal del cliente esta habilitado')
    _evaluar_job(ctx, razones, dudas)
    _evaluar_dinero(ctx, razones, dudas)
    _evaluar_actividad(cliente, razones)
    return _resolver(razones, dudas)


def _evaluar_actividad(record, razones):
    """Condicion 8: actividad reciente, SI la tenemos.

    No estar aca no vuelve nada inactivo -- la ausencia de un dato nunca es
    evidencia de que algo dejo de usarse.
    """
    for campo in ('last_viewed_at', 'last_accessed_at', 'viewed_at',
                  'opened_at', 'updated_at'):
        if _reciente(record.get(campo)):
            razones.append(f'tuvo actividad reciente ({campo})')
            return


def _resolver(razones, dudas):
    """Junta el veredicto.

    El orden importa: una razon activa gana sobre cualquier duda (si algo
    esta vivo, esta vivo). Solo cuando no hay ninguna razon activa la duda
    decide entre INACTIVO y REVIEW_REQUIRED.
    """
    if razones:
        return {'estado': ACTIVO, 'razones': razones, 'dudas': dudas}
    if dudas:
        return {'estado': REVISAR, 'razones': [], 'dudas': dudas}
    return {'estado': INACTIVO, 'razones': [],
            'dudas': [], 'nota': 'se pudo evaluar todo y nada indica uso'}


CLASIFICADORES = {
    'quote': clasificar_quote,
    'contract': clasificar_contract,
    'questionnaire': clasificar_questionnaire,
    'portal': clasificar_portal,
}


def clasificar(tipo, record, ctx):
    """Punto de entrada. Un tipo desconocido va a revision, no a inactivo."""
    fn = CLASIFICADORES.get(tipo)
    if fn is None:
        return {'estado': REVISAR, 'razones': [],
                'dudas': [f'tipo de recurso desconocido: {tipo}']}
    return fn(record, ctx or Contexto())
