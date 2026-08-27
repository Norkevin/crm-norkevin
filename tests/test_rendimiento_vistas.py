"""Guarda de rendimiento: nada de leer el store adentro de un loop.

No mide tiempos (eso seria inestable en CI y en la maquina de Kevin).
Mira el codigo: en las vistas que se abren todo el dia, una lectura del
store DENTRO de un loop significa releer el archivo entero una vez por
fila. Con 20 bodas no se nota; con 300 la pagina se arrastra, y para
entonces ya nadie se acuerda de por que.

El patron correcto ya esta en el codigo: leer una vez antes del loop y
armar un indice (ver `_jobs_por_cliente`).
"""
import ast
import os

APP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'app.py')

# Funciones que van al disco a traer una tabla completa.
LECTURAS_DIRECTAS = {
    'list_jobs', 'list_leads', 'list_clients', 'list_payments', 'list_calendar',
    '_canonical_jobs', '_canonical_clients', '_open_leads', '_jobs_por_cliente',
    '_job_client_relations', '_relaciones_por_job',
}

# Funciones que NO parecen una lectura pero llaman a una por dentro. Este
# conjunto se calcula solo (ver _lectoras_indirectas): el 21-ago se colo un
# N+1 justamente asi -- `_job_clients_display()` no estaba en la lista, pero
# por dentro llamaba a `_job_client_relations()`, que relee `job_clients`.
# La lista de jobs terminaba leyendo la tabla una vez por fila.
LECTURAS = set(LECTURAS_DIRECTAS)


def _lectoras_indirectas(tree):
    """Funciones que alcanzan una lectura del store, a cualquier profundidad."""
    funcs = {n.name: n for n in ast.walk(tree)
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}

    def llamadas(nombre):
        salida = set()
        for n in ast.walk(funcs[nombre]):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
                salida.add(n.func.id)
            elif (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                  and n.func.attr in ('list', 'get_dict', 'get_tenant_dict')
                  and getattr(n.func.value, 'id', '') == 'store'):
                salida.add('__store__')
        return salida

    lectoras = set(LECTURAS_DIRECTAS)
    cambio = True
    while cambio:
        cambio = False
        for nombre in funcs:
            if nombre in lectoras:
                continue
            hijas = llamadas(nombre)
            if '__store__' in hijas or (hijas & lectoras):
                lectoras.add(nombre)
                cambio = True
    return lectoras

# Las pantallas que Kevin abre todos los dias.
VISTAS = {
    'index', 'dashboard', 'leads_list', 'clients_list', 'jobs_list',
    'job_detail', 'client_detail', 'payments_list', 'calendar_view',
}


# Nombres de parametro que significan "esto ya viene leido en lote, no
# vayas al disco". Una llamada que los pasa NO es un N+1.
ARGS_EN_LOTE = ('relacion', 'cache', 'por_job', '_rel', 'by_id')


def _recibe_datos_en_lote(node):
    """True si la llamada recibe datos ya leidos por quien la invoca."""
    for kw in node.keywords:
        if kw.arg and any(s in kw.arg.lower() for s in ARGS_EN_LOTE):
            return True
    for arg in node.args:
        nombre = ''
        if isinstance(arg, ast.Name):
            nombre = arg.id
        elif isinstance(arg, ast.Call) and isinstance(arg.func, ast.Attribute):
            nombre = getattr(arg.func.value, 'id', '')
        elif isinstance(arg, ast.Subscript) and isinstance(arg.value, ast.Name):
            nombre = arg.value.id
        if nombre and any(s in nombre.lower() for s in ARGS_EN_LOTE):
            return True
    return False


def _lectura(node):
    if not isinstance(node, ast.Call):
        return None
    f = node.func
    if isinstance(f, ast.Name) and f.id in LECTURAS:
        return f.id + '()'
    if (isinstance(f, ast.Attribute)
            and f.attr in ('list', 'get_dict', 'get_tenant_dict')
            and getattr(f.value, 'id', '') == 'store'):
        arg = (node.args[0].value
               if node.args and isinstance(node.args[0], ast.Constant) else '?')
        return f'store.{f.attr}({arg!r})'
    return None


def _cuerpo(node):
    if isinstance(node, (ast.For, ast.While)):
        return list(node.body)
    if isinstance(node, (ast.ListComp, ast.GeneratorExp, ast.SetComp)):
        return [node.elt]
    if isinstance(node, ast.DictComp):
        return [node.key, node.value]
    return []


def test_las_vistas_diarias_no_leen_el_store_dentro_de_un_loop():
    with open(APP, encoding='utf-8') as f:
        tree = ast.parse(f.read())

    # Se recalculan las lectoras indirectas en cada corrida: si manana
    # alguien hace que un helper nuevo lea el store, esta guarda lo ve sin
    # que haya que acordarse de agregarlo a mano.
    LECTURAS.clear()
    LECTURAS.update(_lectoras_indirectas(tree))

    ofensas = []
    revisadas = set()
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef) or fn.name not in VISTAS:
            continue
        revisadas.add(fn.name)
        for node in ast.walk(fn):
            for sub in _cuerpo(node):
                for c in ast.walk(sub):
                    r = _lectura(c)
                    if r and not _recibe_datos_en_lote(c):
                        ofensas.append(f'{fn.name}(): {r} dentro del loop de la linea {node.lineno}')

    assert not ofensas, (
        'Estas vistas releen el store una vez por fila (N lecturas de disco por '
        f'pagina): {sorted(set(ofensas))}. Lee una sola vez antes del loop y arma '
        'un indice, como hace _jobs_por_cliente().'
    )
    # Si alguien renombra una vista, este test dejaria de mirarla en silencio.
    assert revisadas == VISTAS, f'no se encontraron estas vistas en app.py: {sorted(VISTAS - revisadas)}'
