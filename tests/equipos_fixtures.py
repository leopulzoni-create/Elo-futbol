import ast, itertools, random, unicodedata
from collections import defaultdict
from datetime import datetime, timedelta


def load_core(source):
    tree = ast.parse(source.lstrip('\ufeff'))
    names = {'construir_bloques', '_build_block_rules_from_bloques', '_violates_blocks',
             '_filter_options_by_blocks', 'equipos_set_key', 'matchup_key',
             '_name2elo_from_bloques', '_diff_real', '_enumerar_opciones_exactas_por_bloques',
             'generar_opciones_unicas', 'parsear_fecha', 'calcular_rachas_camiseta'}
    module = ast.Module(body=[n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names],
                        type_ignores=[])
    env = dict(itertools=itertools, defaultdict=defaultdict, datetime=datetime, timedelta=timedelta)
    exec(compile(module, 'equipos.py', 'exec'), env)
    return env


def roster(bloques=None):
    return [dict(jugador_id=i+1, nombre=f'Jugador {i+1:02}', elo=700+i*77,
                 bloque=(bloques or {}).get(i+1), confirmado=0, equipo=None, camiseta=None, pj_id=i+1)
            for i in range(10)]
