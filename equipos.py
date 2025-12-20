from db import get_connection as db_get_connection
# equipos.py
import streamlit as st
from datetime import datetime, timedelta
import unicodedata
import random
from collections import defaultdict
import itertools

DB_NAME = "elo_futbol.db"  # nombre exacto


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


# -------------------------
# Admin: edición rápida de roster desde "Generar equipos"
# (SIN tocar el matchmaking: solo agregar/quitar jugadores y desarmar equipos si cambia el roster)
# -------------------------
CUPO_PARTIDO = 10


def obtener_jugadores_activos():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, nombre
        FROM jugadores
        WHERE estado IS NULL OR estado = 'activo'
        ORDER BY nombre ASC
        """
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def _reset_equipos_y_camisetas(partido_id: int):
    """Si se modifica el roster, desarmamos equipos/camisetas para evitar inconsistencias."""
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
# Matchmaking: enumeración / scoring / generación 12 opciones
# -------------------------
def _equipo_set_key(lista10):
    team1 = frozenset([n for n in lista10[:5] if n])
    team2 = frozenset([n for n in lista10[5:] if n])
    return (team1, team2)


def _lista10_from_split(team1, team2):
    team1 = list(team1)
    team2 = list(team2)
    team1 += [""] * (5 - len(team1))
    team2 += [""] * (5 - len(team2))
    return team1[:5] + team2[:5]


def _diff_elo_real(lista10, name2elo):
    t1 = [n for n in lista10[:5] if n]
    t2 = [n for n in lista10[5:] if n]
    e1 = sum(name2elo.get(n, 0) for n in t1)
    e2 = sum(name2elo.get(n, 0) for n in t2)
    return abs(e1 - e2), int(e1), int(e2)


def _enumerar_opciones_exactas_por_bloques(bloques, name2elo, n_opciones=12, diff_max=350):
    """
    Enumeración exacta usando bloques indivisibles.
    Genera todas las asignaciones posibles de bloques a equipo1/equipo2 que den 5 y 5 jugadores.
    Luego ordena por ΔELO real y devuelve hasta n_opciones con ΔELO <= diff_max (si hay).
    """
    # Expandimos bloques a lista de "items": (nombres, size, elo_sum)
    items = []
    for bl in bloques:
        nombres = [x["nombre"] for x in bl]
        size = len(nombres)
        elo_sum = sum(name2elo.get(n, 0) for n in nombres)
        items.append((nombres, size, elo_sum))

    # Backtracking para elegir subconjunto de bloques que sumen size 5 para equipo1
    soluciones = []

    def bt(i, picked, size_sum, elo_sum):
        if size_sum == 5:
            team1 = []
            for idx in picked:
                team1.extend(items[idx][0])
            team2 = []
            for j in range(len(items)):
                if j not in picked:
                    team2.extend(items[j][0])
            lista10 = _lista10_from_split(team1, team2)
            diff, e1, e2 = _diff_elo_real(lista10, name2elo)
            soluciones.append((diff, e1, e2, lista10))
            return
        if size_sum > 5 or i >= len(items):
            return
        # elegir i
        bt(i + 1, picked + [i], size_sum + items[i][1], elo_sum + items[i][2])
        # no elegir i
        bt(i + 1, picked, size_sum, elo_sum)

    bt(0, [], 0, 0)

    # Quitar duplicados por simetría (team1/team2)
    vistos = set()
    uniq = []
    for diff, e1, e2, lista10 in soluciones:
        k1, k2 = _equipo_set_key(lista10)
        # orden canónico por set
        canon = tuple(sorted([tuple(sorted(k1)), tuple(sorted(k2))]))
        if canon in vistos:
            continue
        vistos.add(canon)
        uniq.append((diff, e1, e2, lista10))

    uniq.sort(key=lambda x: x[0])

    dentro = [u for u in uniq if u[0] <= diff_max]
    candidatos = dentro if len(dentro) >= n_opciones else uniq

    # Si no alcanzan, devolvemos lo que haya
    base = candidatos[:min(n_opciones, len(candidatos))]
    opts = [x[3] for x in base]
    diffs = [x[0] for x in base]
    return opts, diffs


def generar_opciones_unicas(
    bloques,
    n_opciones=12,
    diff_max=350,
):
    """
    Genera hasta n_opciones opciones distintas, priorizando las de menor ΔELO REAL.
    Usa enumeración exacta por bloques (rápido porque son 10 jugadores y pocos bloques).
    """
    # map nombre->elo
    name2elo = {}
    for bl in bloques:
        for p in bl:
            name2elo[p["nombre"]] = p["elo"]

    try:
        opts, diffs = _enumerar_opciones_exactas_por_bloques(bloques, name2elo, n_opciones=n_opciones, diff_max=diff_max)
        return opts, diffs
    except Exception:
        # fallback: si algo raro pasa, intentamos generar aleatorio con itertools
        nombres = list(name2elo.keys())
        if len(nombres) != 10:
            return [], []
        # enumerar combinaciones equipo1 size5
        combos = list(itertools.combinations(nombres, 5))
        random.shuffle(combos)
        soluciones = []
        vistos = set()

        for c in combos:
            team1 = set(c)
            team2 = set(nombres) - team1
            lista10 = _lista10_from_split(team1, team2)
            k1, k2 = _equipo_set_key(lista10)
            canon = tuple(sorted([tuple(sorted(k1)), tuple(sorted(k2))]))
            if canon in vistos:
                continue
            vistos.add(canon)
            diff, e1, e2 = _diff_elo_real(lista10, name2elo)
            soluciones.append((diff, lista10))
        soluciones.sort(key=lambda x: x[0])
        base = [s for s in soluciones if s[0] <= diff_max]
        base = base if len(base) >= n_opciones else soluciones
        base = base[:min(n_opciones, len(base))]
        return [x[1] for x in base], [x[0] for x in base]


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
def calcular_rachas_camiseta(partido_id: int, fecha_ref):
    """
    Devuelve lista de dicts:
      {nombre, camiseta, veces}
    para jugadores de ESTE partido que tengan una racha
    actual de 3+ partidos con la misma camiseta ('clara' / 'oscura')
    dentro de los últimos ~2 meses (60 días) respecto a fecha_ref.
    """
    if fecha_ref is None:
        return []

    jugadores = obtener_jugadores_partido_full(partido_id)
    if not jugadores:
        return []

    fecha_fin = fecha_ref.date()
    fecha_ini = fecha_fin - timedelta(days=60)

    conn = get_connection()
    cur = conn.cursor()

    avisos = []

    for j in jugadores:
        jid = j["jugador_id"]

        cur.execute("""
            SELECT p.fecha, pj.camiseta
            FROM partidos p
            JOIN partido_jugadores pj ON pj.partido_id = p.id
            WHERE pj.jugador_id = ?
              AND p.fecha IS NOT NULL
            ORDER BY date(p.fecha) ASC, p.id ASC
        """, (jid,))
        rows = cur.fetchall()
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

    conn.close()
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
# Panel principal
# -------------------------
def panel_generacion():
    st.subheader("⚽ Generar equipos (12 opciones / paginado 3 en 3)")

    if st.button("⬅️ Volver al menú principal", key="btn_back_top"):
        st.session_state.admin_page = None
        st.rerun()

    partidos = obtener_partidos_abiertos()
    if not partidos:
        st.info("No hay partidos abiertos.")
        return

    opciones_combo = []
    for p in partidos:
        pid = p["id"]
        np = p["np"]
        fecha_dt = parsear_fecha(p["fecha"])
        cancha = p["cancha_nombre"]
        hora_str = formatear_hora(p["hora"])
        if fecha_dt:
            dia_es = DIAS_ES[fecha_dt.weekday()]
            fecha_txt = fecha_dt.strftime("%d/%m/%y")
            etiqueta = f"N° {np} - {dia_es} {fecha_txt} {hora_str} - {cancha}"
        else:
            etiqueta = f"N° {np} - {p['fecha']} {hora_str} - {cancha}"
        opciones_combo.append((pid, etiqueta, np))

    sel = st.selectbox("Seleccioná el partido:", [t for _, t, _ in opciones_combo], key="sb_partido")
    partido_id, numero_publico = next((pid, np) for pid, t, np in opciones_combo if t == sel)

    np, fecha_dt, hora_str, cancha_nombre = obtener_partido_info(partido_id)
    numero_publico = np if np is not None else numero_publico
    if fecha_dt:
        dia_es = DIAS_ES[fecha_dt.weekday()]
        fecha_txt = fecha_dt.strftime("%d/%m/%y")
        header = f"Partido N° {numero_publico} — {dia_es} {fecha_txt} {hora_str} — {cancha_nombre}"
    else:
        header = f"Partido N° {numero_publico} — {cancha_nombre}"

    st.markdown(
        f"<div style='font-size:1.35rem; font-weight:700; line-height:1.4; margin:0.25rem 0 0.5rem;'>"
        f"{header}"
        f"</div>",
        unsafe_allow_html=True,
    )

    confirmado, team1c, team2c, elo1c, elo2c = equipos_ya_confirmados(partido_id)
    if confirmado:
        c1, c2 = st.columns(2)
        with c1:
            j1 = obtener_camiseta_equipo(partido_id, 1)
            lab1 = f"**Equipo 1 ({elo1c} ELO)** — Camiseta: {j1.capitalize() if j1 else '—'}"
            st.markdown(lab1)
            for n in team1c:
                st.write(f"- {n}")
        with c2:
            j2 = obtener_camiseta_equipo(partido_id, 2)
            lab2 = f"**Equipo 2 ({elo2c} ELO)** — Camiseta: {j2.capitalize() if j2 else '—'}"
            st.markdown(lab2)
            for n in team2c:
                st.write(f"- {n}")

        st.divider()
        st.markdown("### 👕 Camisetas")

        cam1 = obtener_camiseta_equipo(partido_id, 1) or "clara"
        cam2 = obtener_camiseta_equipo(partido_id, 2) or "oscura"

        colj1, colj2 = st.columns(2)
        with colj1:
            st.markdown(f"**Equipo 1:** Camiseta {cam1.capitalize()}")
        with colj2:
            st.markdown(f"**Equipo 2:** Camiseta {cam2.capitalize()}")

        if st.button("↔️ Intercambiar camisetas", key="btn_swap_camisetas"):
            intercambiar_camisetas(partido_id)
            st.success("Camisetas intercambiadas.")
            st.rerun()

        avisos = calcular_rachas_camiseta(partido_id, fecha_dt)
        if avisos:
            st.markdown("#### 📊 Rachas de camiseta (últimos 2 meses)")
            for a in avisos:
                color_txt = "clara" if a["camiseta"] == "clara" else "oscura"
                st.write(f"- {a['nombre']}: {a['veces']} partidos con camiseta {color_txt}")

        st.divider()
        st.markdown("### 👥 Vista para jugadores")
        render_vista_jugadores(partido_id)

        st.divider()
        st.warning("Para rehacer equipos, primero eliminá los confirmados.")
        if st.button("🗑️ Eliminar equipos confirmados", key="btn_eliminar_confirmados"):
            borrar_equipos_confirmados(partido_id)
            st.success("Equipos eliminados. Ahora podés generar nuevas opciones.")
            st.rerun()

        if st.button("⬅️ Volver al menú principal", key="btn_back_bottom_locked"):
            st.session_state.admin_page = None
            st.rerun()
        return

    jugadores = obtener_jugadores_partido_full(partido_id)
    if not jugadores:
        st.info("Todavía no hay jugadores en este partido.")
        return

    # =========================
    # Editor de roster (agregar/quitar) – cambio pedido
    # =========================
    st.markdown("### 👥 Jugadores del partido")

    total_actual = len(jugadores)
    st.caption(f"Inscriptos: **{total_actual}/{CUPO_PARTIDO}**")

    if total_actual >= CUPO_PARTIDO:
        st.success("Roster completo ✅")
    else:
        st.warning(f"Faltan {CUPO_PARTIDO - total_actual} para completar el roster.")

    cols = st.columns(2)
    for i, jp in enumerate(jugadores):
        icono = "🟢" if jp.get("confirmado") else "🔵"
        with cols[i % 2]:
            st.write(f"{icono} {jp['nombre']}")
            if st.button("Quitar", key=f"eq_quitar_{partido_id}_{jp['jugador_id']}_{i}"):
                quitar_jugador_de_partido(partido_id, jp["jugador_id"])
                # si cambia roster, invalida opciones generadas
                for k in ("_equipos_opciones", "_equipos_diffs", "_equipos_actual", "_equipos_page"):
                    st.session_state.pop(k, None)
                st.rerun()

    # Completar roster desde acá (admin)
    faltan = max(0, CUPO_PARTIDO - total_actual)
    if faltan > 0:
        st.divider()
        st.markdown("### ➕ Completar roster (admin)")
        activos = obtener_jugadores_activos()
        ids_asignados = {j["jugador_id"] for j in jugadores}
        disponibles = [r for r in activos if r["id"] not in ids_asignados]
        map_nombre_id = {r["nombre"]: r["id"] for r in disponibles}

        if not map_nombre_id:
            st.info("No hay jugadores activos disponibles para agregar.")
            return

        seleccion = st.multiselect(
            f"Seleccioná hasta {faltan} jugador(es)",
            options=list(map_nombre_id.keys()),
            key=f"eq_ms_add_{partido_id}",
        )
        if len(seleccion) > faltan:
            st.warning(f"Solo podés agregar {faltan}.")
            seleccion = seleccion[:faltan]

        if st.button("Agregar al partido", disabled=(len(seleccion) == 0), key=f"eq_btn_add_{partido_id}"):
            agregar_jugadores_a_partido(partido_id, [map_nombre_id[n] for n in seleccion])
            for k in ("_equipos_opciones", "_equipos_diffs", "_equipos_actual", "_equipos_page"):
                st.session_state.pop(k, None)
            st.rerun()

        # Sin 10, no se puede generar
        return

    # Con roster completo seguimos con el flujo normal (matchmaking intacto)
    names = [j["nombre"] for j in jugadores]

    ui_definir_bloques(partido_id, names)

    jugadores = obtener_jugadores_partido_full(partido_id)
    bloques = construir_bloques(jugadores)

    # =========================
    # Generar opciones (paginadas 3 en 3)
    # =========================
    cgen, calt = st.columns([1, 1])

    with cgen:
        if st.button("🎲 Generar opciones balanceadas", key="btn_generar_opciones"):
            with st.spinner("Buscando hasta 12 alternativas (ordenadas por ΔELO real)..."):
                opts, diffs = generar_opciones_unicas(
                    bloques,
                    n_opciones=12,
                    diff_max=350,
                )
                if not opts:
                    st.error("No se pudieron generar opciones. Revisá duplas/tríos o que haya 10 jugadores.")
                    return

                st.session_state._equipos_opciones = opts
                st.session_state._equipos_diffs = diffs
                st.session_state._equipos_actual = None
                st.session_state._equipos_page = 0
                st.rerun()

    with calt:
        if st.session_state.get("_equipos_opciones"):
            pages = max(1, (len(st.session_state._equipos_opciones) + 2) // 3)
            if st.button("➡️ Siguiente 3 opciones", key="btn_next_page"):
                st.session_state._equipos_page = (st.session_state.get("_equipos_page", 0) + 1) % pages
                st.rerun()

    if st.session_state.get("_equipos_opciones"):
        opts = st.session_state._equipos_opciones
        diffs = st.session_state._equipos_diffs
        page = st.session_state.get("_equipos_page", 0)
        start = page * 3
        end = min(start + 3, len(opts))
        cols = st.columns(3)

        elo_map = {j["nombre"]: j["elo"] for j in jugadores}

        for idx in range(start, end):
            col = cols[idx - start]
            lista = opts[idx]

            team1 = [n for n in lista[:5] if n]
            team2 = [n for n in lista[5:] if n]
            elo1 = int(sum(elo_map.get(n, 0) for n in team1))
            elo2 = int(sum(elo_map.get(n, 0) for n in team2))

            col.markdown(f"### Opción {idx + 1}")
            col.write(f"ΔELO real: **{int(diffs[idx])}**")
            col.markdown(f"**Equipo 1 ({elo1} ELO)**")
            for n in team1:
                col.write(f"- {n}")
            col.markdown(f"**Equipo 2 ({elo2} ELO)**")
            for n in team2:
                col.write(f"- {n}")

            if col.button(f"Seleccionar Opción {idx + 1}", key=f"btn_sel_opt_{idx + 1}"):
                st.session_state._equipos_actual = lista[:]
                st.success(f"Opción {idx + 1} cargada. Podés ajustar y confirmar.")
                st.rerun()

    if st.session_state.get("_equipos_actual"):
        st.divider()
        st.markdown("### ✅ Confirmar equipos")

        equipo_actual = st.session_state._equipos_actual
        team1 = [n for n in equipo_actual[:5] if n]
        team2 = [n for n in equipo_actual[5:] if n]

        elo_map = {j["nombre"]: j["elo"] for j in jugadores}
        elo1 = int(sum(elo_map.get(n, 0) for n in team1))
        elo2 = int(sum(elo_map.get(n, 0) for n in team2))

        col1, col2 = st.columns(2)
        with col1:
            st.markdown(f"**Equipo 1 ({elo1} ELO)**")
            st.write(", ".join(team1))
        with col2:
            st.markdown(f"**Equipo 2 ({elo2} ELO)**")
            st.write(", ".join(team2))

        if st.button("✅ Confirmar equipos", key="btn_confirmar_equipos"):
            if len(team1) == 5 and len(team2) == 5:
                guardar_opcion(partido_id, equipo_actual)
                if obtener_camiseta_equipo(partido_id, 1) is None:
                    asignar_camiseta_equipo(partido_id, 1, "clara")
                if obtener_camiseta_equipo(partido_id, 2) is None:
                    asignar_camiseta_equipo(partido_id, 2, "oscura")
                st.success("Equipos confirmados y guardados.")
                for k in ("_equipos_opciones", "_equipos_diffs", "_equipos_actual", "_equipos_page"):
                    st.session_state.pop(k, None)
                st.rerun()
            else:
                st.error("Cada equipo debe tener exactamente 5 jugadores.")

    if st.button("⬅️ Volver al menú principal", key="btn_back_bottom"):
        st.session_state.admin_page = None
        st.rerun()
