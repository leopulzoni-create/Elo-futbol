"""Reglas de la pantalla de equipos. No modifica ni calcula el ELO."""


def fingerprint(jugadores, separados):
    fields = ('pj_id', 'jugador_id', 'nombre', 'elo', 'bloque', 'confirmado', 'equipo', 'camiseta')
    return (tuple(sorted(tuple(j.get(k) for k in fields) for j in jugadores)),
            tuple(sorted(tuple(sorted(pair)) for pair in separados)))


def grupos_actuales(jugadores):
    grupos = {}
    for j in jugadores:
        bloque = str(j.get('bloque') or '').strip()
        if bloque and bloque != '0':
            grupos.setdefault(bloque, []).append(j['jugador_id'])
    return [g for g in grupos.values() if len(g) > 1]


def validar_reglas(ids, grupos, separados):
    ids = set(ids)
    usados = set()
    for grupo in grupos:
        miembros = set(grupo)
        if not 2 <= len(miembros) <= 5 or len(miembros) != len(grupo):
            raise ValueError('Cada grupo debe tener entre 2 y 5 jugadores distintos.')
        if not miembros <= ids:
            raise ValueError('Un integrante del grupo ya no está en el partido.')
        if usados & miembros:
            raise ValueError('Un jugador ya pertenece a otro grupo. Quitá ese grupo antes de reagruparlo.')
        usados.update(miembros)
    for pareja in separados:
        if len(pareja) != 2 or len(set(pareja)) != 2 or not set(pareja) <= ids:
            raise ValueError('Elegí dos jugadores distintos del partido para separarlos.')
        if any(set(pareja) <= set(g) for g in grupos):
            raise ValueError('Estos jugadores tienen una regla de juntos y otra de separados. Quitá una de ellas.')


def validar_equipos(equipo1, equipo2, ids, grupos, separados):
    validar_reglas(ids, grupos, separados)
    if len(equipo1) != 5 or len(equipo2) != 5:
        raise ValueError('Cada equipo debe tener exactamente 5 jugadores.')
    if len(set(equipo1 + equipo2)) != 10 or set(equipo1 + equipo2) != set(ids):
        raise ValueError('Los equipos deben incluir a los diez jugadores, sin repetir.')
    lado = set(equipo1)
    if any(0 < len(lado & set(g)) < len(g) for g in grupos):
        raise ValueError('El intercambio separaría un grupo que debe jugar junto.')
    if any((a in lado) == (b in lado) for a, b in separados):
        raise ValueError('Dos jugadores que deben jugar separados quedarían juntos.')


def opciones_validas(jugadores, separados, construir_bloques, generar):
    """Reutilizar el generador exacto; filtrar ANTES de recortar a 12."""
    nombres = {j['nombre']: j['jugador_id'] for j in jugadores}
    if len(nombres) != len(jugadores):
        raise ValueError('Hay nombres repetidos. Diferenciá los nombres de esos jugadores antes de generar equipos.')
    ids = [j['jugador_id'] for j in jugadores]
    grupos = grupos_actuales(jugadores)
    validar_reglas(ids, grupos, separados)
    if len(ids) != 10 or len(set(ids)) != 10:
        raise ValueError('Completá el partido con 10 jugadores distintos.')
    opciones, diferencias = generar(construir_bloques(jugadores),
                                    n_opciones=126 if separados else 12, diff_max=350)
    validas, diffs = [], []
    for opcion, diff in zip(opciones, diferencias):
        opcion_ids = [nombres[n] for n in opcion]
        try:
            validar_equipos(opcion_ids[:5], opcion_ids[5:], ids, grupos, separados)
        except ValueError:
            continue
        validas.append(opcion_ids)
        diffs.append(diff)
    return validas[:12], diffs[:12]


def unidades_equipo(equipo, grupos):
    """Permitir seleccionar grupos completos y jugadores libres para intercambiar."""
    unidades = [tuple(g) for g in grupos if set(g) <= set(equipo)]
    agrupados = {jid for g in unidades for jid in g}
    return unidades + [(jid,) for jid in equipo if jid not in agrupados]
