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
# Admin: edición rápida de roster (para no depender de 'Gestión de partidos')
# -------------------------
CUPO_PARTIDO = 10

def _jugadores_activos():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, nombre FROM jugadores WHERE estado = 'activo' ORDER BY nombre ASC")
    rows = cur.fetchall()
    conn.close()
    return rows

def _reset_equipos_y_camisetas(partido_id: int):
    """Si se toca el roster, desarmamos equipos/camisetas para evitar inconsistencias."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE partido_jugadores SET equipo = NULL, camiseta = NULL WHERE partido_id = ?",
        (partido_id,),
    )
    conn.commit()
    conn.close()

def _promover_desde_espera_si_hay_cupo(partido_id: int):
    """Promueve al primero de la lista de espera si hay cupo en el roster."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS c FROM partido_jugadores WHERE partido_id = ?", (partido_id,))
    c = cur.fetchone()
    total = int(c["c"]) if c and c["c"] is not None else 0
    if total >= CUPO_PARTIDO:
        conn.close()
        return False

    cur.execute(
        """
        SELECT le.jugador_id, j.nombre
          FROM lista_espera le
          JOIN jugadores j ON j.id = le.jugador_id
         WHERE le.partido_id = ?
         ORDER BY le.created_at ASC
         LIMIT 1
        """,
        (partido_id,),
    )
    prom = cur.fetchone()
    if not prom:
        conn.close()
        return False

    cur.execute(
        """
        INSERT OR IGNORE INTO partido_jugadores
            (partido_id, jugador_id, confirmado_por_jugador, camiseta, ingreso_desde_espera)
        VALUES (?, ?, 1, 'clara', 1)
        """,
        (partido_id, prom["jugador_id"]),
    )
    cur.execute(
        "DELETE FROM lista_espera WHERE partido_id = ? AND jugador_id = ?",
        (partido_id, prom["jugador_id"]),
    )
    conn.commit()
    conn.close()
    return True

def _agregar_jugador_al_partido(partido_id: int, jugador_id: int, confirmado: int = 0):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT OR IGNORE INTO partido_jugadores (partido_id, jugador_id, confirmado_por_jugador)
        VALUES (?, ?, ?)
        """,
        (partido_id, jugador_id, int(confirmado)),
    )
    conn.commit()
    conn.close()
    _reset_equipos_y_camisetas(partido_id)

def _quitar_jugador_del_partido(partido_id: int, jugador_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM partido_jugadores WHERE partido_id = ? AND jugador_id = ?",
        (partido_id, jugador_id),
    )
    conn.commit()
    conn.close()
    _reset_equipos_y_camisetas(partido_id)

def _render_roster_editor(partido_id: int):
    """Devuelve (names, total_actual). Renderiza lista con botones y un 'Agregar' rápido."""
    jugadores_partido = obtener_jugadores_partido_full(partido_id)
    total_actual = len(jugadores_partido)

    st.markdown("### 👥 Jugadores del partido")
    st.caption(f"Inscriptos: **{total_actual}/{CUPO_PARTIDO}**")
    if total_actual >= CUPO_PARTIDO:
        st.success("Roster completo ✅")

    cols = st.columns(2)
    for i, jp in enumerate(jugadores_partido):
        icono = "🟢" if jp.get("confirmado") else "🔵"
        with cols[i % 2]:
            st.write(f"{icono} {jp['nombre']}")
            if st.button("Quitar", key=f"eq_quitar_{partido_id}_{jp['jugador_id']}_{i}"):
                _quitar_jugador_del_partido(partido_id, jp["jugador_id"])
                promoted = _promover_desde_espera_si_hay_cupo(partido_id)
                if promoted:
                    st.toast("Se promovió al primero de la lista de espera.", icon="✅")
                st.session_state.pop("_equipos_opciones", None)
                st.session_state.pop("_equipos_diffs", None)
                st.session_state.pop("_equipos_actual", None)
                st.session_state.pop("_equipos_page", None)
                st.rerun()

    st.divider()

    if total_actual < CUPO_PARTIDO:
        activos = _jugadores_activos()
        disponibles = [(r["id"], r["nombre"]) for r in activos if r["id"] not in {j["jugador_id"] for j in jugadores_partido}]
        if disponibles:
            opciones = [n for _, n in disponibles]
            elegido = st.selectbox("Agregar jugador:", opciones, key=f"eq_add_sb_{partido_id}")
            jid = next(i for i, n in disponibles if n == elegido)
            if st.button("➕ Agregar", key=f"eq_add_btn_{partido_id}"):
                _agregar_jugador_al_partido(partido_id, jid, confirmado=0)
                st.session_state.pop("_equipos_opciones", None)
                st.session_state.pop("_equipos_diffs", None)
                st.session_state.pop("_equipos_actual", None)
                st.session_state.pop("_equipos_page", None)
                st.rerun()
        else:
            st.info("No hay jugadores activos disponibles para agregar.")
    else:
        st.caption("Roster completo: para cambiar, quitá a alguien.")

    names = [j["nombre"] for j in jugadores_partido]
    return names, total_actual


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
    Si bloque es NULL/'' => jugador individual.
    Devuelve lista de bloques, donde cada bloque es lista de jugadores (dicts).
    """
    grupos = defaultdict(list)
    for j in jugadores:
        b = (j.get("bloque") or "").strip()
        if not b:
            b = f"__solo__{j['jugador_id']}"
        grupos[b].append(j)
    # Orden estable
    return [grupos[k] for k in sorted(grupos.keys())]


# -------------------------
# ELO util
# -------------------------
def elo_bloque(b):
    return float(sum(j["elo"] for j in b))


def nombres_bloque(b):
    return [j["nombre"] for j in b]


# -------------------------
# Guardar equipos confirmados
# -------------------------
def guardar_equipos_confirmados(partido_id: int, combinacion):
    conn = get_connection()
    cur = conn.cursor()

    # 1) Actualizar equipos de cada jugador
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

    # 2) Registrar quién generó estos equipos
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
    if not fecha_ref:
        fecha_ref = datetime.now()
    try:
        lim = fecha_ref - timedelta(days=60)
        lim_str = lim.strftime("%Y-%m-%d")
    except Exception:
        lim_str = None

    jugadores = obtener_jugadores_partido_full(partido_id)
    ids = [j["jugador_id"] for j in jugadores]
    if not ids:
        return []

    conn = get_connection()
    cur = conn.cursor()

    placeholders = ",".join(["?"] * len(ids))
    params = ids[:]
    sql_fecha = ""
    if lim_str:
        sql_fecha = " AND date(p.fecha) >= date(?) "
        params = ids[:] + [lim_str]

    # Traemos partidos con camiseta para esos jugadores, ordenados DESC (más reciente primero)
    cur.execute(
        f"""
        SELECT pj.jugador_id,
               j.nombre,
               pj.camiseta,
               p.fecha
        FROM partido_jugadores pj
        JOIN partidos p ON p.id = pj.partido_id
        JOIN jugadores j ON j.id = pj.jugador_id
        WHERE pj.jugador_id IN ({placeholders})
          AND pj.camiseta IS NOT NULL
          AND pj.camiseta <> ''
          {sql_fecha}
        ORDER BY date(p.fecha) DESC, p.id DESC
        """,
        tuple(params),
    )
    rows = cur.fetchall()
    conn.close()

    # agrupar por jugador
    hist = defaultdict(list)
    for r in rows:
        hist[r["jugador_id"]].append((str(r["camiseta"]).lower(), r["fecha"], r["nombre"]))

    avisos = []
    for jid in ids:
        h = hist.get(jid, [])
        if not h:
            continue
        # racha actual: contar seguidos desde el más reciente
        cam0 = h[0][0]
        if cam0 not in JERSEYS:
            continue
        count = 0
        for cam, _, _nombre in h:
            if cam == cam0:
                count += 1
            else:
                break
        if count >= 3:
            nombre = h[0][2]
            avisos.append({"nombre": nombre, "camiseta": cam0, "veces": count})

    # ordenar por más racha, luego nombre
    avisos.sort(key=lambda x: (-x["veces"], sin_acentos(x["nombre"]).lower()))
    return avisos


# -------------------------
# Vista equipos para jugadores (se usa también en cargaresultados)
# -------------------------
def render_vista_jugadores(partido_id: int):
    jugadores = obtener_jugadores_partido_full(partido_id)

    def _team_info(eq):
        cams = [str(j.get("camiseta") or "").lower() for j in eq if (j.get("camiseta") or "").strip()]
        c1 = sum(1 for c in cams if c == "clara")
        c2 = sum(1 for c in cams if c == "oscura")
        if c1 > c2:
            return "🟦", "clara"
        if c2 > c1:
            return "⬛", "oscura"
        # fallback
        for c in cams:
            if c in ("clara", "oscura"):
                return ("🟦", "clara") if c == "clara" else ("⬛", "oscura")
        return "🟦", "clara"

    def _eq_num(v):
        try:
            return int(v)
        except Exception:
            return None

    eq1 = [j for j in jugadores if _eq_num(j.get("equipo")) == 1]
    eq2 = [j for j in jugadores if _eq_num(j.get("equipo")) == 2]
    icon1, lab1 = _team_info(eq1)
    icon2, lab2 = _team_info(eq2)

    c1, c2 = st.columns(2)
    with c1:
        st.markdown(f"**{icon1} Equipo 1  (Camiseta {lab1})**")
        for j in eq1:
            st.write(f"{icon1}  {j['nombre']}")
    with c2:
        st.markdown(f"**{icon2} Equipo 2  (Camiseta {lab2})**")
        for j in eq2:
            st.write(f"{icon2}  {j['nombre']}")

    st.caption("⚠️ Si alguien se baja, los equipos se desarman automáticamente y el admin deberá regenerarlos.")


# -------------------------
# Matchmaking (balanceo) + UI bloques
# -------------------------
def ui_definir_bloques(partido_id: int, names):
    st.markdown("### 🔗 Duplas / tríos (opcional)")
    st.caption("Si querés obligar que jueguen juntos, asigná un mismo número de bloque.")

    jugadores = obtener_jugadores_partido_full(partido_id)
    # UI simple: un select por jugador (0 = sin bloque)
    # Guardamos en DB pj.bloque como texto (ej '1', '2', ...)
    bloque_map = {}
    for j in jugadores:
        bloque_map[j["nombre"]] = (j.get("bloque") or "")

    cols = st.columns(2)
    for i, n in enumerate(names):
        with cols[i % 2]:
            actual = str(bloque_map.get(n) or "").strip()
            # opciones 0..5
            opciones = [""] + [str(k) for k in range(1, 6)]
            idx = opciones.index(actual) if actual in opciones else 0
            sel = st.selectbox(
                f"Bloque de {n}",
                opciones,
                index=idx,
                key=f"bloque_{partido_id}_{n}"
            )
            bloque_map[n] = sel

    if st.button("💾 Guardar bloques", key=f"save_bloques_{partido_id}"):
        conn = get_connection()
        cur = conn.cursor()
        for n in names:
            b = (bloque_map.get(n) or "").strip()
            cur.execute(
                """
                UPDATE partido_jugadores
                   SET bloque = ?
                 WHERE partido_id = ?
                   AND jugador_id = (SELECT id FROM jugadores WHERE nombre = ? LIMIT 1)
                """,
                (b if b else None, partido_id, n),
            )
        conn.commit()
        conn.close()
        st.success("Bloques guardados.")
        st.rerun()


def generar_opciones_unicas(bloques, n_opciones=12, diff_max=350):
    """
    Genera combinaciones de 10 (2 equipos de 5) respetando bloques.
    Devuelve (opciones, diffs).
    - opciones: lista de combinaciones (lista de 10 nombres, primeros 5 eq1)
    - diffs: lista de ΔELO real por opción (abs(sum elo eq1 - sum elo eq2))
    """
    # Expand bloques a nombres y elos
    bloques2 = []
    for b in bloques:
        bloques2.append({
            "nombres": [j["nombre"] for j in b],
            "elo": sum(j["elo"] for j in b),
            "size": len(b),
        })

    # total size debe ser 10
    total = sum(b["size"] for b in bloques2)
    if total != 10:
        return [], []

    # buscamos subsets que sumen 5
    opciones = []
    diffs = []

    # para evitar duplicados (mismo set de equipo1)
    seen = set()

    # enumerar subsets de bloques
    for r in range(1, len(bloques2) + 1):
        for subset in itertools.combinations(range(len(bloques2)), r):
            size = sum(bloques2[i]["size"] for i in subset)
            if size != 5:
                continue
            team1_blocks = list(subset)
            team2_blocks = [i for i in range(len(bloques2)) if i not in team1_blocks]

            team1 = []
            team2 = []
            elo1 = 0.0
            elo2 = 0.0

            for i in team1_blocks:
                team1 += bloques2[i]["nombres"]
                elo1 += bloques2[i]["elo"]
            for i in team2_blocks:
                team2 += bloques2[i]["nombres"]
                elo2 += bloques2[i]["elo"]

            # normalizar orden para comparar
            key = tuple(sorted(team1))
            if key in seen:
                continue
            seen.add(key)

            diff = abs(elo1 - elo2)
            if diff <= diff_max:
                opciones.append(team1 + team2)
                diffs.append(diff)

    # ordenar por diff asc y recortar
    orden = sorted(range(len(opciones)), key=lambda i: diffs[i])
    opciones = [opciones[i] for i in orden][:n_opciones]
    diffs = [diffs[i] for i in orden][:n_opciones]
    return opciones, diffs


def panel_generacion():
    st.subheader("⚽ Generar equipos")

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

    # --- NUEVO: editor de roster acá mismo ---
    names, total_actual = _render_roster_editor(partido_id)

    if total_actual != CUPO_PARTIDO:
        st.warning(f"Se requieren exactamente {CUPO_PARTIDO} jugadores para generar equipos.")
        return

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
        if st.button("➕ Más alternativas", key="btn_mas_alternativas"):
            if st.session_state.get("_equipos_opciones"):
                opts = st.session_state._equipos_opciones
                pages = max(1, (len(opts) + 2) // 3)  # ceil(len/3)
                st.session_state._equipos_page = (st.session_state.get("_equipos_page", 0) + 1) % pages
                st.session_state._equipos_actual = None
                st.rerun()

    # =========================
    # Mostrar opciones (paginadas)
    # =========================
    if st.session_state.get("_equipos_opciones"):
        opts = st.session_state._equipos_opciones

        page = st.session_state.get("_equipos_page", 0)
        pages = max(1, (len(opts) + 2) // 3)
        page = page % pages

        start = page * 3
        end = start + 3
        opts_page = opts[start:end]

        st.caption(f"Página: **{page + 1}/{pages}** ({start + 1}–{min(end, len(opts))} de {len(opts)})")

        cols = st.columns(3)
        chosen_idx = None

        elo_map = {j["nombre"]: j["elo"] for j in jugadores}

        for ci, comb in enumerate(opts_page):
            with cols[ci]:
                team1 = comb[:5]
                team2 = comb[5:]
                elo1 = int(sum(elo_map.get(n, 1000) for n in team1))
                elo2 = int(sum(elo_map.get(n, 1000) for n in team2))
                diff = abs(elo1 - elo2)

                st.markdown(f"**Opción {start + ci + 1}**")
                st.caption(f"ΔELO: **{diff:.0f}**")
                st.write("**Equipo 1**")
                for n in team1:
                    st.write(f"- {n}")
                st.write("**Equipo 2**")
                for n in team2:
                    st.write(f"- {n}")

                if st.button("✅ Elegir esta opción", key=f"choose_{partido_id}_{start+ci}"):
                    chosen_idx = start + ci

        if chosen_idx is not None:
            st.session_state._equipos_actual = opts[chosen_idx]
            st.rerun()

    # =========================
    # Confirmar selección
    # =========================
    if st.session_state.get("_equipos_actual"):
        comb = st.session_state._equipos_actual
        st.divider()
        st.markdown("## ✅ Confirmar equipos")
        c1, c2 = st.columns(2)
        with c1:
            st.write("**Equipo 1**")
            for n in comb[:5]:
                st.write(f"- {n}")
        with c2:
            st.write("**Equipo 2**")
            for n in comb[5:]:
                st.write(f"- {n}")

        st.markdown("### 👕 Camisetas")
        colx, coly = st.columns(2)
        with colx:
            cam1 = st.selectbox("Equipo 1", list(JERSEYS), index=0, key=f"cam1_{partido_id}")
        with coly:
            cam2 = st.selectbox("Equipo 2", list(JERSEYS), index=1, key=f"cam2_{partido_id}")

        if cam1 == cam2:
            st.warning("Elegí camisetas distintas para cada equipo.")
        else:
            if st.button("💾 Confirmar equipos", key=f"confirm_{partido_id}"):
                guardar_equipos_confirmados(partido_id, comb)
                asignar_camiseta_equipo(partido_id, 1, cam1)
                asignar_camiseta_equipo(partido_id, 2, cam2)
                st.success("Equipos confirmados ✅")
                st.rerun()
