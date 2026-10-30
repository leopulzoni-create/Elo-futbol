from db import get_connection as db_get_connection
# equipos.py
import streamlit as st
from datetime import datetime, timedelta
import unicodedata
import random
from collections import defaultdict
import itertools

DB_NAME = "elo_futbol.db"  # nombre exact


# -------------------------
# Conexión y utilidades
# -------------------------
def get_connection():
    # wrapper por compatibilidad con el resto del proyecto
    return db_get_connection()


def sin_acentos(texto: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", texto)
        if unicodedata.category(c) != "Mn"
    )


# español sin tildes para evitar problemas de render
DIAS_ES = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]


def parsear_fecha(fecha_str):
    if fecha_str is None:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(fecha_str, fmt)
        except ValueError:
            continue
    return None


def formatear_hora(hora_int):
    """
    Espera un entero tipo HHMM (ej: 1900 -> '19:00').
    Si viene None o invalido, devuelve '19:00' por defecto.
    """
    try:
        if hora_int is None:
            return "19:00"
        s = str(int(hora_int))
        if len(s) <= 2:
            hh = int(s)
            mm = 0
        else:
            s = s.zfill(4)[-4:]
            hh = int(s[:2])
            mm = int(s[2:])
        if not (0 <= hh <= 23 and 0 <= mm <= 59):
            return "19:00"
        return f"{hh:02d}:{mm:02d}"
    except Exception:
        return "19:00"


# -------------------------
# Datos desde la DB
# -------------------------
def obtener_partidos_abiertos():
    """
    Devuelve filas con:
      id, numero_publico AS np, fecha, hora, cancha_nombre
    """
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT p.id,
               p.numero_publico AS np,
               p.fecha,
               p.hora,
               IFNULL(c.nombre,'Sin asignar') AS cancha_nombre
        FROM partidos p
        LEFT JOIN canchas c ON p.cancha_id = c.id
        WHERE p.tipo = 'abierto'
          AND p.ganador IS NULL
          AND p.diferencia_gol IS NULL
        ORDER BY p.fecha ASC, p.hora ASC
    """)
    rows = cur.fetchall()
    conn.close()
    return rows


def obtener_jugadores_partido_full(partido_id: int):
    """
    Devuelve lista de dicts con:
    { pj_id, jugador_id, nombre, elo (elo_actual), bloque, confirmado, equipo, camiseta }
    """
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT pj.id AS pj_id,
               j.id  AS jugador_id,
               j.nombre AS nombre,
               COALESCE(j.elo_actual, 1000) AS elo,
               pj.bloque AS bloque,
               pj.confirmado_por_jugador AS confirmado,
               pj.equipo AS equipo,
               pj.camiseta AS camiseta
        FROM partido_jugadores pj
        JOIN jugadores j ON j.id = pj.jugador_id
        WHERE pj.partido_id = ?
        ORDER BY pj.id
    """, (partido_id,))
    rows = cur.fetchall()
    conn.close()

    jugadores = [{
        "pj_id": r["pj_id"],
        "jugador_id": r["jugador_id"],
        "nombre": r["nombre"],
        "elo": float(r["elo"]) if r["elo"] is not None else 1000.0,
        "bloque": r["bloque"],
        "confirmado": r["confirmado"],
        "equipo": r["equipo"],
        "camiseta": r["camiseta"],
    } for r in rows]
    return jugadores


def obtener_partido_info(partido_id: int):
    """
    Devuelve (numero_publico, fecha_dt, hora_str, cancha_nombre) del partido.
    """
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT p.numero_publico AS np,
               p.fecha,
               p.hora,
               IFNULL(c.nombre,'Sin asignar') AS cancha_nombre
        FROM partidos p
        LEFT JOIN canchas c ON p.cancha_id = c.id
        WHERE p.id = ?
    """, (partido_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return None, None, None, None
    fecha_dt = parsear_fecha(row["fecha"])
    hora_str = formatear_hora(row["hora"])
    return row["np"], fecha_dt, hora_str, row["cancha_nombre"]


# =========================
# (CAMBIO 1) Edición de roster desde "Generar equipos"
# =========================
CUPO_PARTIDO = 10


def obtener_jugadores_activos():
    """
    Jugadores disponibles para que el admin complete el roster desde esta pantalla.
    """
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, nombre
        FROM jugadores
        WHERE estado IS NULL OR estado = 'activo'
        ORDER BY nombre ASC
    """)
    rows = cur.fetchall()
    conn.close()
    return rows


def _reset_equipos_y_camisetas(partido_id: int):
    """
    Si cambia el roster, invalidamos equipos/camisetas para evitar inconsistencias.
    (No toca el matchmaking; solo deja la DB consistente.)
    """
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE partido_jugadores SET equipo = NULL, camiseta = NULL WHERE partido_id = ?",
        (partido_id,),
    )
    conn.commit()
    conn.close()


def agregar_jugadores_a_partido(partido_id: int, jugador_ids):
    if not jugador_ids:
        return
    conn = get_connection()
    cur = conn.cursor()
    for jid in jugador_ids:
        cur.execute(
            """
            INSERT INTO partido_jugadores (partido_id, jugador_id, confirmado_por_jugador)
            VALUES (?, ?, 0)
            """,
            (partido_id, int(jid)),
        )
    conn.commit()
    conn.close()
    _reset_equipos_y_camisetas(partido_id)


def quitar_jugador_de_partido(partido_id: int, jugador_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM partido_jugadores WHERE partido_id = ? AND jugador_id = ?",
        (partido_id, int(jugador_id)),
    )
    conn.commit()
    conn.close()
    _reset_equipos_y_camisetas(partido_id)


# -------------------------
# Camisetas
# -------------------------
JERSEYS = ("clara", "oscura")


def obtener_camiseta_equipo(partido_id: int, equipo: int):
    """
    Devuelve 'clara' / 'oscura' si hay al menos un registro con camiseta
    válida para ese equipo. Si no hay nada, devuelve None.

    NOTA: asumimos que siempre escribimos la camiseta de forma uniforme
    para todo el equipo, así que alcanza con leer una sola fila.
    """
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT camiseta
        FROM partido_jugadores
        WHERE partido_id = ?
          AND equipo = ?
          AND camiseta IS NOT NULL
          AND camiseta <> ''
        LIMIT 1
    """, (partido_id, equipo))
    row = cur.fetchone()
    conn.close()

    if not row:
        return None

    val = str(row[0]).lower()
    if val in JERSEYS:
        return val
    return None


def asignar_camiseta_equipo(partido_id: int, equipo: int, camiseta: str):
    if camiseta not in JERSEYS:
        return
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        UPDATE partido_jugadores
           SET camiseta = ?
         WHERE partido_id = ? AND equipo = ?
    """, (camiseta, partido_id, equipo))
    conn.commit()
    conn.close()


def limpiar_camiseta_equipo(partido_id: int, equipo: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        UPDATE partido_jugadores
           SET camiseta = NULL
         WHERE partido_id = ? AND equipo = ?
    """, (partido_id, equipo))
    conn.commit()
    conn.close()


def intercambiar_camisetas(partido_id: int):
    """
    Alterna 'clara' <-> 'oscura' para todos los jugadores
    de ambos equipos (1 y 2) de ese partido.
    """
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        UPDATE partido_jugadores
           SET camiseta = CASE
                WHEN camiseta = 'clara' THEN 'oscura'
                WHEN camiseta = 'oscura' THEN 'clara'
                ELSE camiseta
           END
         WHERE partido_id = ?
           AND equipo IN (1, 2)
    """, (partido_id,))
    conn.commit()
    conn.close()


# -------------------------
# Bloques (duplas/tríos) a partir de 'bloque'
# -------------------------
def construir_bloques(jugadores):
    """
    Arma bloques indivisibles a partir de pj.bloque.
    Normaliza pj.bloque para evitar errores (espacios, "0", etc).
    """
    grupos = defaultdict(list)
    singles = []

    for j in jugadores:
        b = j.get("bloque", None)

        if b is None:
            singles.append(j)
            continue

        b_str = str(b).strip()
        if b_str == "" or b_str == "0":
            singles.append(j)
            continue

        grupos[b_str].append(j)

    bloques = list(grupos.values())
    bloques.extend([[s] for s in singles])

    bloques.sort(key=lambda bl: (-len(bl), -sum(float(x.get("elo", 0) or 0) for x in bl)))
    return bloques


# -------------------------
# Guardar / limpiar bloques definidos por el admin (auto-guardado)
# -------------------------
def limpiar_bloques(partido_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE partido_jugadores SET bloque = NULL WHERE partido_id = ?", (partido_id,))
    conn.commit()
    conn.close()


def set_bloque_por_nombres(partido_id: int, nombres: list, bloque_id: int):
    if not nombres:
        return
    conn = get_connection()
    cur = conn.cursor()
    for nombre in nombres:
        cur.execute("""
            UPDATE partido_jugadores
               SET bloque = ?
             WHERE partido_id = ?
               AND jugador_id = (SELECT id FROM jugadores WHERE nombre = ? LIMIT 1)
        """, (bloque_id, partido_id, nombre))
    conn.commit()
    conn.close()


def _guardar_companeros_si_valido(partido_id, duo1, duo2, trio1, trio2):
    ok_tamaños = (
        (len(duo1) in (0, 2)) and (len(duo2) in (0, 2)) and
        (len(trio1) in (0, 3)) and (len(trio2) in (0, 3))
    )
    if not ok_tamaños:
        st.warning("Tamaños inválidos: la dupla debe tener 2 y el trío 3 jugadores.")
        return False

    seleccionados = [*duo1, *duo2, *trio1, *trio2]
    solapados = [n for n in seleccionados if seleccionados.count(n) > 1]
    if solapados:
        st.error(f"Jugadores repetidos en grupos: {sorted(set(solapados))}")
        return False

    limpiar_bloques(partido_id)
    set_bloque_por_nombres(partido_id, duo1, 1)
    set_bloque_por_nombres(partido_id, duo2, 2)
    set_bloque_por_nombres(partido_id, trio1, 3)
    set_bloque_por_nombres(partido_id, trio2, 4)
    st.toast("Compañeros guardados.", icon="✅")
    return True


def ui_definir_bloques(partido_id: int, jugadores_nombres: list):
    st.markdown("### 🧩 Definir compañeros (opcional)")
    st.caption("Hasta **2 duplas** y **2 tríos**. No se permiten solapamientos. (Se guarda automáticamente)")

    current = defaultdict(list)
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT j.nombre, pj.bloque
        FROM partido_jugadores pj
        JOIN jugadores j ON j.id = pj.jugador_id
        WHERE pj.partido_id = ? AND pj.bloque IS NOT NULL
        ORDER BY j.nombre
    """, (partido_id,))
    for nombre, b in cur.fetchall():
        current[str(b).strip()].append(nombre)
    conn.close()

    if "bloques_ui" not in st.session_state:
        st.session_state.bloques_ui = {
            "duo1": current.get("1", []),
            "duo2": current.get("2", []),
            "trio1": current.get("3", []),
            "trio2": current.get("4", []),
        }

    def _on_change_guardar():
        duo1 = st.session_state.get("duo1_ms", [])
        duo2 = st.session_state.get("duo2_ms", [])
        trio1 = st.session_state.get("trio1_ms", [])
        trio2 = st.session_state.get("trio2_ms", [])
        if _guardar_companeros_si_valido(partido_id, duo1, duo2, trio1, trio2):
            st.session_state.bloques_ui = {"duo1": duo1, "duo2": duo2, "trio1": trio1, "trio2": trio2}
            st.rerun()

    col1, col2 = st.columns(2)
    with col1:
        st.multiselect(
            "Dupla 1 (2 jugadores)", jugadores_nombres,
            default=st.session_state.bloques_ui["duo1"], key="duo1_ms",
            on_change=_on_change_guardar
        )
        st.multiselect(
            "Trío 1 (3 jugadores)", jugadores_nombres,
            default=st.session_state.bloques_ui["trio1"], key="trio1_ms",
            on_change=_on_change_guardar
        )
    with col2:
        st.multiselect(
            "Dupla 2 (2 jugadores)", jugadores_nombres,
            default=st.session_state.bloques_ui["duo2"], key="duo2_ms",
            on_change=_on_change_guardar
        )
        st.multiselect(
            "Trío 2 (3 jugadores)", jugadores_nombres,
            default=st.session_state.bloques_ui["trio2"], key="trio2_ms",
            on_change=_on_change_guardar
        )


# -------------------------
# Validación dura de bloques (nunca permitir romperlos)
# -------------------------
def _build_block_rules_from_bloques(bloques):
    """
    groups: key -> set(nombres) para bloques de tamaño > 1
    """
    groups = {}
    for bi, bl in enumerate(bloques):
        if len(bl) <= 1:
            continue
        names = [p["nombre"] for p in bl]
        groups[f"G{bi}"] = set(names)
    return groups


def _violates_blocks(lista10, groups):
    """
    True si algún bloque (dupla/trío) queda partido entre Equipo 1 y Equipo 2.
    """
    if not groups:
        return False

    t1 = set([n for n in lista10[:5] if n])
    t2 = set([n for n in lista10[5:] if n])

    for members in groups.values():
        in1 = len(members & t1)
        in2 = len(members & t2)
        if in1 > 0 and in2 > 0:
            return True
    return False


def _filter_options_by_blocks(opciones, bloques):
    groups = _build_block_rules_from_bloques(bloques)
    if not groups:
        return opciones

    out = []
    seen = set()
    for lista10 in opciones:
        if _violates_blocks(lista10, groups):
            continue
        key = matchup_key(lista10)  # evita espejadas
        if key in seen:
            continue
        seen.add(key)
        out.append(lista10)
    return out


# -------------------------
# Keys de match (evitar duplicados y espejadas)
# -------------------------
def equipos_set_key(lista10):
    """
    Devuelve (team1, team2) como frozensets (ignora orden interno).
    OJO: team1 y team2 siguen diferenciados por lado (1 vs 2).
    """
    team1 = frozenset([n for n in lista10[:5] if n])
    team2 = frozenset([n for n in lista10[5:] if n])
    return (team1, team2)


def matchup_key(lista10):
    """
    Key CANÓNICA del match:
    - ignora orden dentro de cada equipo
    - ignora swap Equipo1<->Equipo2
    => evita opciones idénticas o espejadas.
    """
    t1, t2 = equipos_set_key(lista10)
    return frozenset((t1, t2))


def _name2elo_from_bloques(bloques):
    m = {}
    for b in bloques:
        for p in b:
            m[p["nombre"]] = int(p.get("elo", 0) or 0)
    return m


def _diff_real(lista10, name2elo):
    team1 = [n for n in lista10[:5] if n]
    team2 = [n for n in lista10[5:] if n]
    elo1 = int(sum(name2elo.get(n, 0) for n in team1))
    elo2 = int(sum(name2elo.get(n, 0) for n in team2))
    return abs(elo1 - elo2), elo1, elo2


# -------------------------
# Enumeración EXACTA respetando bloques
# -------------------------
def _enumerar_opciones_exactas_por_bloques(bloques, name2elo, n_opciones=12, diff_max=350):
    """
    Enumeración EXACTA de opciones respetando bloques (duplas/tríos).
    Devuelve top N por ΔELO real.

    - Cada bloque es indivisible (va entero al Equipo 1 o al 2).
    - Equipo 1 debe sumar exactamente 5 jugadores.
    - Para evitar duplicados por swap E1/E2, fijamos un bloque ancla en E1.
    """
    if not bloques:
        return [], []

    sizes = [len(b) for b in bloques]
    total = sum(sizes)
    if total != 10:
        return [], []

    def bloque_min_nombre(bl):
        return min(p["nombre"] for p in bl)

    anchor_i = min(range(len(bloques)), key=lambda i: bloque_min_nombre(bloques[i]))
    anchor_size = sizes[anchor_i]
    if anchor_size > 5:
        return [], []

    need = 5 - anchor_size
    indices_rest = [i for i in range(len(bloques)) if i != anchor_i]

    bloque_names = []
    for bl in bloques:
        ns = [p["nombre"] for p in bl]
        bloque_names.append(ns)

    candidatos = []  # (diff, elo1, elo2, lista10)

    if need == 0:
        team1_names = sorted(bloque_names[anchor_i])
        team2_names = []
        for i in indices_rest:
            team2_names.extend(bloque_names[i])
        team2_names = sorted(team2_names)

        if len(team1_names) != 5 or len(team2_names) != 5:
            return [], []

        elo1 = int(sum(name2elo.get(n, 0) for n in team1_names))
        elo2 = int(sum(name2elo.get(n, 0) for n in team2_names))
        diff = abs(elo1 - elo2)
        candidatos.append((diff, elo1, elo2, team1_names + team2_names))

    else:
        for r in range(0, len(indices_rest) + 1):
            for comb in itertools.combinations(indices_rest, r):
                if sum(sizes[i] for i in comb) != need:
                    continue

                team1_indices = {anchor_i, *comb}
                team2_indices = set(range(len(bloques))) - team1_indices

                team1_names = []
                for i in team1_indices:
                    team1_names.extend(bloque_names[i])
                team2_names = []
                for i in team2_indices:
                    team2_names.extend(bloque_names[i])

                if len(team1_names) != 5 or len(team2_names) != 5:
                    continue

                team1_names = sorted(team1_names)
                team2_names = sorted(team2_names)

                elo1 = int(sum(name2elo.get(n, 0) for n in team1_names))
                elo2 = int(sum(name2elo.get(n, 0) for n in team2_names))
                diff = abs(elo1 - elo2)

                candidatos.append((diff, elo1, elo2, team1_names + team2_names))

    if not candidatos:
        return [], []

    candidatos.sort(key=lambda x: x[0])

    dentro = [c for c in candidatos if c[0] <= diff_max]
    base = dentro if len(dentro) >= n_opciones else candidatos

    base = base[:min(n_opciones, len(base))]
    opciones = [c[3] for c in base]
    diffs = [c[0] for c in base]
    return opciones, diffs


def generar_opciones_unicas(
    bloques,
    n_opciones=12,
    diff_max=350,
    max_busquedas=1200,
    intentos_por_busqueda=3500
):
    """
    Genera hasta n_opciones opciones distintas, priorizando las de menor ΔELO REAL.

    - Si NO hay duplas/tríos (10 singles): calcula TODAS las combinaciones únicas (126) y devuelve top N.
    - Si SÍ hay duplas/tríos: enumeración EXACTA por bloques y devuelve top N.

    max_busquedas/intentos_por_busqueda quedan por compatibilidad.
    """
    if not bloques:
        return [], []

    name2elo = _name2elo_from_bloques(bloques)

    # 10 singles -> 126 combinaciones únicas
    if len(bloques) == 10 and all(len(b) == 1 for b in bloques):
        names = [b[0]["nombre"] for b in bloques]
        anchor = names[0]
        others = names[1:]

        candidatos = []
        for comb in itertools.combinations(others, 4):
            team1 = [anchor] + list(comb)
            team2 = [n for n in names if n not in team1]

            team1 = sorted(team1)
            team2 = sorted(team2)

            elo1 = int(sum(name2elo.get(n, 0) for n in team1))
            elo2 = int(sum(name2elo.get(n, 0) for n in team2))
            diff = abs(elo1 - elo2)

            candidatos.append((diff, elo1, elo2, team1 + team2))

        candidatos.sort(key=lambda x: x[0])
        dentro = [c for c in candidatos if c[0] <= diff_max]
        base = dentro if len(dentro) >= n_opciones else candidatos

        base = base[:min(n_opciones, len(base))]
        opciones = [c[3] for c in base]
        diffs = [c[0] for c in base]

        # blindaje extra (por las dudas)
        opciones = _filter_options_by_blocks(opciones, bloques)
        diffs = diffs[:len(opciones)]
        return opciones, diffs

    # Con bloques -> exacto por bloques
    opciones, diffs = _enumerar_opciones_exactas_por_bloques(
        bloques,
        name2elo,
        n_opciones=n_opciones,
        diff_max=diff_max
    )

    # blindaje extra
    opciones = _filter_options_by_blocks(opciones, bloques)
    diffs = diffs[:len(opciones)]
    return opciones, diffs


# -------------------------
# Guardar / borrar equipos elegidos
# -------------------------
def guardar_opcion(partido_id: int, combinacion):
    conn = get_connection()
    cur = conn.cursor()

    for idx, nombre in enumerate(combinacion):
        if not nombre:
            continue
        equipo_val = 1 if idx < 5 else 2
        cur.execute(
            """
            UPDATE partido_jugadores
               SET equipo = ?
             WHERE partido_id = ?
               AND jugador_id = (SELECT id FROM jugadores WHERE nombre = ? LIMIT 1)
            """,
            (equipo_val, partido_id, nombre),
        )

    admin_username = "desconocido"
    user = getattr(st.session_state, "user", None)
    try:
        if isinstance(user, dict):
            admin_username = user.get("username") or admin_username
        elif user is not None and hasattr(user, "get"):
            admin_username = user.get("username", admin_username)
    except Exception:
        pass

    cur.execute(
        """
        UPDATE partidos
           SET equipos_generados_por = ?
         WHERE id = ?
        """,
        (admin_username, partido_id),
    )

    conn.commit()
    conn.close()


def borrar_equipos_confirmados(partido_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        UPDATE partido_jugadores
           SET equipo = NULL, camiseta = NULL
         WHERE partido_id = ?
    """, (partido_id,))
    conn.commit()
    conn.close()


def equipos_ya_confirmados(partido_id: int):
    jugadores = obtener_jugadores_partido_full(partido_id)
    asignados = [j for j in jugadores if j["equipo"] in (1, 2)]
    if len(asignados) != 10:
        return False, [], [], 0, 0
    team1 = [j["nombre"] for j in jugadores if j["equipo"] == 1]
    team2 = [j["nombre"] for j in jugadores if j["equipo"] == 2]
    elo1 = int(sum(j["elo"] for j in jugadores if j["equipo"] == 1))
    elo2 = int(sum(j["elo"] for j in jugadores if j["equipo"] == 2))
    return True, team1, team2, elo1, elo2


# -------------------------
# Rachas de camisetas (últimos 2 meses, racha actual)
# -------------------------
def calcular_rachas_camiseta(partido_id: int, fecha_ref, jugadores=None):
    """
    Devuelve lista de dicts:
      {nombre, camiseta, veces}
    para jugadores de ESTE partido que tengan una racha
    actual de 3+ partidos con la misma camiseta ('clara' / 'oscura')
    dentro de los últimos ~2 meses (60 días) respecto a fecha_ref.
    """
    if fecha_ref is None:
        return []

    if jugadores is None:
        jugadores = obtener_jugadores_partido_full(partido_id)
    if not jugadores:
        return []
    fecha_fin = fecha_ref.date()
    fecha_ini = fecha_fin - timedelta(days=60)
    conn = get_connection()
    try:
        cur = conn.cursor()
        ids = [j["jugador_id"] for j in jugadores]
        marks = ",".join("?" for _ in ids)
        cur.execute(f"""
            SELECT pj.jugador_id, p.fecha, pj.camiseta
            FROM partidos p JOIN partido_jugadores pj ON pj.partido_id=p.id
            WHERE pj.jugador_id IN ({marks})
              AND date(p.fecha) BETWEEN ? AND ?
            ORDER BY date(p.fecha) ASC, p.id ASC
        """, ids + [fecha_ini.isoformat(), fecha_fin.isoformat()])
        historiales = defaultdict(list)
        for row in cur.fetchall():
            historiales[row["jugador_id"]].append((row["fecha"], row["camiseta"]))
    finally:
        conn.close()

    avisos = []

    for j in jugadores:
        jid = j["jugador_id"]

        rows = historiales[jid]
        if not rows:
            continue

        cams = []
        for fecha_str, cam in rows:
            dt = parsear_fecha(fecha_str)
            if dt is None:
                continue
            d = dt.date()
            if d < fecha_ini or d > fecha_fin:
                continue

            if cam is None:
                cams.append(None)
            else:
                c = str(cam).strip().lower()
                if c.startswith("clara"):
                    cams.append("clara")
                elif c.startswith("osc"):
                    cams.append("oscura")
                else:
                    cams.append(None)

        if not cams:
            continue

        last_color = None
        count = 0
        for c in reversed(cams):
            if c and (last_color is None or c == last_color):
                last_color = c
                count += 1
            else:
                break

        if last_color and count >= 3:
            avisos.append({
                "nombre": j["nombre"],
                "camiseta": last_color,
                "veces": count,
            })

    return avisos


# -------------------------
# Vista jugadores (visual sin ELO)
# -------------------------
def render_vista_jugadores(partido_id: int):
    jugadores = obtener_jugadores_partido_full(partido_id)
    if len([j for j in jugadores if j["equipo"] in (1, 2)]) != 10:
        return

    team1 = [j["nombre"] for j in jugadores if j["equipo"] == 1]
    team2 = [j["nombre"] for j in jugadores if j["equipo"] == 2]
    cam1 = obtener_camiseta_equipo(partido_id, 1) or "clara"
    cam2 = obtener_camiseta_equipo(partido_id, 2) or "oscura"

    badge_style = """
        display:inline-block;padding:4px 10px;border-radius:999px;font-weight:600;
        border:1px solid rgba(0,0,0,0.1);margin-left:8px;
    """
    light_bg = "background:#e0e0e0;color:#222;"
    dark_bg = "background:#222;color:#fff;"

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("#### Equipo 1")
        st.markdown(
            f"<span style='{badge_style}{(light_bg if cam1=='clara' else dark_bg)}'>Camiseta {cam1.capitalize()}</span>",
            unsafe_allow_html=True
        )
        st.write("")
        for n in team1:
            st.write(f"- {n}")
    with col2:
        st.markdown("#### Equipo 2")
        st.markdown(
            f"<span style='{badge_style}{(dark_bg if cam2=='oscura' else light_bg)}'>Camiseta {cam2.capitalize()}</span>",
            unsafe_allow_html=True
        )
        st.write("")
        for n in team2:
            st.write(f"- {n}")


# -------------------------
# Selección de partido y panel
# -------------------------
def panel_generacion():
    from equipos_ui import panel_generacion as editor
    editor()
