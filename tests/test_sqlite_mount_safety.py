"""Regresion del incidente de SQLite sobre volumen montado (agosto 2026).

QUE PASO: durante la preparacion del paquete de cutover, una herramienta
abrio artifacts/shadow_legacy_20260712.db con sqlite3.connect() IN-PLACE,
directamente sobre la ruta del volumen montado de Windows. La conexion
fallo con "disk I/O error" y dejo el archivo TRUNCADO A 0 BYTES. Se pudo
recuperar solo porque era un artefacto derivado (regenerable desde el
fixture); si hubiera sido la unica copia de algo real, se habria perdido.

POR QUE PASA: SQLite necesita bloqueos de archivo y escrituras auxiliares
(-wal, -shm, journal) incluso para operaciones aparentemente de solo
lectura. Sobre un volumen de red/FUSE esos bloqueos pueden fallar a mitad
de camino y dejar el archivo en un estado inconsistente o vacio.

LA REGLA: cualquier herramienta de este repo que abra un .db de shadow
debe hacer una de estas dos cosas (o ambas):
  a) COPY-FIRST: copiar el .db a un temporal LOCAL y abrir la copia.
  b) MODE=RO: abrir con URI 'file:...?mode=ro' (read-only real, SQLite no
     intenta crear journal ni tomar bloqueos de escritura).

Estos tests son ESTATICOS y NO DESTRUCTIVOS: leen el codigo fuente con
ast/regex, no abren ninguna base de datos, no tocan ningun archivo. Su
unico objetivo es que este error no vuelva a entrar al repo sin que
alguien lo note.

NOTA sobre migrate_json_to_v5_shadow.py: ese script SI escribe un .db
legitimamente (es su trabajo: crear la shadow DB). Esta exento de la
regla de solo-lectura, pero se verifica aparte que su --db-path nunca
pueda apuntar a data/crm.db (la produccion).
"""
import ast
import os
import re

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Herramientas que LEEN shadow DBs y por lo tanto deben usar copy-first
# o mode=ro. Si en el futuro se agrega otra herramienta que lea .db,
# agregarla aca (o mejor: que el test de descubrimiento de abajo la
# encuentre solo).
READERS_QUE_DEBEN_SER_SEGUROS = [
    'controlled_cutover.py',
]

# Scripts que SI pueden escribir un .db, con su justificacion.
ESCRITORES_AUTORIZADOS = {
    'migrate_json_to_v5_shadow.py': 'Su proposito es CREAR la shadow DB.',
}


def _leer(path):
    with open(path, 'r', encoding='utf-8') as fh:
        return fh.read()


def _archivos_python_del_repo():
    """Todos los .py del repo, excluyendo tests, venv y cache."""
    encontrados = []
    for dirpath, dirnames, filenames in os.walk(REPO_ROOT):
        dirnames[:] = [d for d in dirnames if d not in (
            '__pycache__', '.git', 'venv', '.venv', 'node_modules', 'tests')]
        for name in filenames:
            if name.endswith('.py'):
                encontrados.append(os.path.join(dirpath, name))
    return encontrados


@pytest.mark.parametrize('script', READERS_QUE_DEBEN_SER_SEGUROS)
def test_lectores_de_shadow_db_usan_copy_first_o_mode_ro(script):
    """La proteccion concreta: controlled_cutover.py (y cualquier lector
    futuro que se agregue a la lista) debe abrir los .db de forma segura."""
    path = os.path.join(REPO_ROOT, script)
    assert os.path.exists(path), f'No existe {script}'
    src = _leer(path)

    usa_mode_ro = 'mode=ro' in src
    usa_copy_first = bool(re.search(r'shutil\.copy2?\(', src)) and 'tempfile' in src

    assert usa_mode_ro or usa_copy_first, (
        f'{script} abre bases de datos SQLite pero no usa mode=ro ni copy-first. '
        'Ver el docstring de este archivo: abrir un .db in-place sobre el volumen '
        'montado ya trunco un archivo a 0 bytes una vez.')


def test_controlled_cutover_nunca_abre_db_in_place():
    """Especifico y estricto: en controlled_cutover.py, NINGUN
    sqlite3.connect() puede recibir una ruta cruda. Todos deben pasar por
    el helper _open_sqlite_readonly(), que hace copy-first + mode=ro."""
    src = _leer(os.path.join(REPO_ROOT, 'controlled_cutover.py'))
    arbol = ast.parse(src)

    conexiones_directas = []
    for nodo in ast.walk(arbol):
        if not isinstance(nodo, ast.Call):
            continue
        func = nodo.func
        es_sqlite_connect = (
            isinstance(func, ast.Attribute) and func.attr == 'connect'
            and isinstance(func.value, ast.Name) and func.value.id == 'sqlite3')
        if not es_sqlite_connect:
            continue

        # Se permite exactamente una forma: sqlite3.connect(uri, uri=True)
        # dentro del helper, donde `uri` ya trae mode=ro y apunta a una
        # copia local.
        tiene_uri_true = any(kw.arg == 'uri' for kw in nodo.keywords)
        if not tiene_uri_true:
            conexiones_directas.append(nodo.lineno)

    assert not conexiones_directas, (
        f'controlled_cutover.py tiene sqlite3.connect() sin uri=True en las lineas '
        f'{conexiones_directas} -- eso abre el archivo in-place y puede truncarlo. '
        'Usar _open_sqlite_readonly().')


def test_helper_de_lectura_segura_hace_copia_local_y_readonly():
    """El helper en si debe seguir haciendo las dos cosas. Si alguien lo
    'simplifica' quitando la copia o el mode=ro, este test lo atrapa."""
    src = _leer(os.path.join(REPO_ROOT, 'controlled_cutover.py'))
    arbol = ast.parse(src)

    helper = next((n for n in ast.walk(arbol)
                   if isinstance(n, ast.FunctionDef) and n.name == '_open_sqlite_readonly'), None)
    assert helper is not None, 'Desaparecio _open_sqlite_readonly() de controlled_cutover.py'

    cuerpo = ast.get_source_segment(src, helper) or ''
    assert 'tempfile' in cuerpo or 'mkdtemp' in cuerpo, \
        '_open_sqlite_readonly() ya no crea un directorio temporal local (copy-first roto)'
    assert 'copy2' in cuerpo or 'copyfile' in cuerpo, \
        '_open_sqlite_readonly() ya no copia el .db antes de abrirlo (copy-first roto)'
    assert 'mode=ro' in cuerpo, \
        '_open_sqlite_readonly() ya no abre en modo read-only (mode=ro roto)'


def test_ninguna_herramienta_nueva_abre_db_de_artifacts_sin_proteccion():
    """Red de seguridad: descubre CUALQUIER .py del repo que mencione un
    .db de artifacts/ y ademas llame a sqlite3.connect(). Si aparece uno
    nuevo que no esta en la lista de lectores seguros ni en la de
    escritores autorizados, este test falla para que alguien lo revise."""
    sospechosos = []
    for path in _archivos_python_del_repo():
        nombre = os.path.relpath(path, REPO_ROOT).replace(os.sep, '/')
        if nombre in ESCRITORES_AUTORIZADOS:
            continue
        try:
            src = _leer(path)
        except (OSError, UnicodeDecodeError):
            continue
        if 'sqlite3.connect' not in src:
            continue
        menciona_db_de_artifacts = bool(re.search(r'artifacts[\\/][\w.]*\.db', src)) \
            or 'shadow_' in src
        if not menciona_db_de_artifacts:
            continue
        if 'mode=ro' in src or ('tempfile' in src and re.search(r'shutil\.copy2?\(', src)):
            continue
        sospechosos.append(nombre)

    assert not sospechosos, (
        f'Estos archivos abren un .db de artifacts/ sin copy-first ni mode=ro: '
        f'{sospechosos}. Ver el docstring de este archivo -- este patron ya '
        'destruyo un archivo una vez.')


def test_migracion_shadow_no_puede_apuntar_a_la_db_de_produccion():
    """El escritor autorizado sigue teniendo su guardia: --db-path nunca
    puede resolver a data/crm.db."""
    src = _leer(os.path.join(REPO_ROOT, 'migrate_json_to_v5_shadow.py'))
    assert 'crm.db' in src, \
        'migrate_json_to_v5_shadow.py ya no menciona crm.db -- se perdio la guardia'
    tiene_guardia = re.search(r'(abort|sys\.exit|raise|return)', src) and 'crm.db' in src
    assert tiene_guardia, (
        'migrate_json_to_v5_shadow.py debe abortar si --db-path apunta a data/crm.db')
