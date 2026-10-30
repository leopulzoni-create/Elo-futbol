"""Lecturas agrupadas y escrituras atómicas para el editor de equipos."""
from db import get_connection
from equipos_logic import fingerprint, grupos_actuales, validar_reglas, validar_equipos


ROSTER_SQL = '''SELECT pj.id AS pj_id, j.id AS jugador_id, j.nombre,
    COALESCE(j.elo_actual, 1000) AS elo, pj.bloque,
    pj.confirmado_por_jugador AS confirmado, pj.equipo, pj.camiseta
    FROM partido_jugadores pj JOIN jugadores j ON j.id=pj.jugador_id
    WHERE pj.partido_id=? ORDER BY pj.id'''


def ensure_schema():
    conn = get_connection()
    try:
        conn.cursor().execute('''CREATE TABLE IF NOT EXISTS equipo_separados (
            partido_id INTEGER NOT NULL, jugador_a INTEGER NOT NULL,
            jugador_b INTEGER NOT NULL,
            PRIMARY KEY (partido_id, jugador_a, jugador_b),
            CHECK (jugador_a < jugador_b))''')
        conn.commit()
    finally:
        conn.close()


def _leer(cur, partido_id):
    cur.execute(ROSTER_SQL, (partido_id,))
    jugadores = [dict(r) for r in cur.fetchall()]
    for j in jugadores:
        j['elo'] = float(j['elo'])
    cur.execute('SELECT jugador_a, jugador_b FROM equipo_separados WHERE partido_id=?', (partido_id,))
    separados = [tuple(r[k] for k in ('jugador_a', 'jugador_b')) for r in cur.fetchall()]
    return jugadores, separados


def cargar(partido_id):
    conn = get_connection()
    try:
        cur = conn.cursor()
        jugadores, separados = _leer(cur, partido_id)
        # Reglas de jugadores dados de baja desde otras pantallas no son aplicables.
        ids = {j['jugador_id'] for j in jugadores}
        separados = [p for p in separados if set(p) <= ids]
        activos = []
        if not (len(jugadores) == 10 and all(j['equipo'] in (1, 2) for j in jugadores)):
            cur.execute("SELECT id, nombre FROM jugadores WHERE estado IS NULL OR estado='activo' ORDER BY nombre")
            activos = [dict(r) for r in cur.fetchall()]
        return jugadores, separados, activos
    finally:
        conn.close()


def guardar(partido_id, jugadores_previos, separados_previos, accion, datos, autor='desconocido'):
    """Releer dentro de la transacción. Una sesión vieja nunca pisa otro armado."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute('BEGIN IMMEDIATE')
        actuales, separados = _leer(cur, partido_id)
        ids = {j['jugador_id'] for j in actuales}
        separados = [p for p in separados if set(p) <= ids]
        if fingerprint(actuales, separados) != fingerprint(jugadores_previos, separados_previos):
            raise ValueError('El partido cambió desde que lo abriste. Pulsá Actualizar datos y revisá nuevamente.')
        cur.execute('SELECT tipo, ganador, diferencia_gol FROM partidos WHERE id=?', (partido_id,))
        partido = cur.fetchone()
        if not partido or partido['tipo'] != 'abierto' or partido['ganador'] is not None or partido['diferencia_gol'] is not None:
            raise ValueError('El partido ya no está abierto. Actualizá los datos.')
        confirmados = len(actuales) == 10 and all(j['equipo'] in (1, 2) for j in actuales)
        if confirmados and accion not in ('eliminar', 'camisetas'):
            raise ValueError('Primero eliminá los equipos confirmados para modificarlos.')
        if accion == 'roster':
            nuevos = set(datos)
            if len(nuevos) != len(datos) or len(nuevos) > 10:
                raise ValueError('El partido admite hasta 10 jugadores distintos.')
            agregar, quitar = nuevos - ids, ids - nuevos
            if agregar:
                marks = ','.join('?' for _ in agregar)
                cur.execute(f"SELECT id FROM jugadores WHERE id IN ({marks}) AND (estado IS NULL OR estado='activo')", list(agregar))
                if {r['id'] for r in cur.fetchall()} != agregar:
                    raise ValueError('Uno de los jugadores elegidos ya no está activo.')
            cur.executemany('DELETE FROM partido_jugadores WHERE partido_id=? AND jugador_id=?',
                            [(partido_id, jid) for jid in quitar])
            cur.executemany('INSERT INTO partido_jugadores(partido_id, jugador_id, confirmado_por_jugador) VALUES (?, ?, 0)',
                            [(partido_id, jid) for jid in agregar])
            if agregar:
                # La tabla de espera puede no existir en instalaciones antiguas.
                cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='lista_espera'")
                if cur.fetchone():
                    cur.executemany('DELETE FROM lista_espera WHERE partido_id=? AND jugador_id=?',
                                    [(partido_id, jid) for jid in agregar])
            if agregar or quitar:
                cur.execute('UPDATE partido_jugadores SET equipo=NULL, camiseta=NULL WHERE partido_id=?', (partido_id,))
                cur.execute('DELETE FROM equipo_separados WHERE partido_id=? AND (jugador_a NOT IN '
                            '(SELECT jugador_id FROM partido_jugadores WHERE partido_id=?) OR jugador_b NOT IN '
                            '(SELECT jugador_id FROM partido_jugadores WHERE partido_id=?))', (partido_id, partido_id, partido_id))
        elif accion == 'reglas':
            grupos, pares = datos
            validar_reglas(ids, grupos, pares)
            cur.execute('UPDATE partido_jugadores SET bloque=NULL WHERE partido_id=?', (partido_id,))
            cur.executemany('UPDATE partido_jugadores SET bloque=? WHERE partido_id=? AND jugador_id=?',
                            [(i, partido_id, jid) for i, grupo in enumerate(grupos, 1) for jid in grupo])
            cur.execute('DELETE FROM equipo_separados WHERE partido_id=?', (partido_id,))
            cur.executemany('INSERT INTO equipo_separados VALUES(?, ?, ?)',
                            [(partido_id, *sorted(pair)) for pair in {tuple(sorted(p)) for p in pares}])
        elif accion == 'confirmar':
            validar_equipos(datos[:5], datos[5:], ids, grupos_actuales(actuales), separados)
            cur.executemany('UPDATE partido_jugadores SET equipo=?, camiseta=? WHERE partido_id=? AND jugador_id=?',
                            [(1 if i < 5 else 2, 'clara' if i < 5 else 'oscura', partido_id, jid)
                             for i, jid in enumerate(datos)])
            cur.execute('UPDATE partidos SET equipos_generados_por=? WHERE id=?', (autor, partido_id))
        elif accion == 'eliminar':
            cur.execute('UPDATE partido_jugadores SET equipo=NULL, camiseta=NULL WHERE partido_id=?', (partido_id,))
        elif accion == 'camisetas':
            if not confirmados:
                raise ValueError('Los equipos ya no están confirmados.')
            cur.execute("""UPDATE partido_jugadores SET camiseta=CASE
                WHEN camiseta='clara' THEN 'oscura' WHEN camiseta='oscura' THEN 'clara'
                ELSE camiseta END WHERE partido_id=?""", (partido_id,))
        else:
            raise ValueError('Acción desconocida.')
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
