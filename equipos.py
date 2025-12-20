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
    grupos = defaultdict(list)
    singles = []
    for j in jugadores:
        b = j["bloque"]
        if b is None or b == "":
            singles.append(j)
        else:
            grupos[str(b)].append(j)

    bloques = list(grupos.values())
    bloques.extend([[s] for s in singles])
    bloques.sort(key=lambda bl: (-len(bl), -sum(x["elo"] for x in bl)))
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
        current[str(b)].append(nombre)
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
# Heurística de asignación y generación
# -------------------------
def evaluar_asignacion(bloques, orden_indices):
    e1, e2 = [], []
    s1, s2 = 0.0, 0.0
    n1, n2 = 0, 0
    for idx in orden_indices:
        b = bloques[idx]
        size = len(b)
        elo_b = sum(p["elo"] for p in b)
        if (n1 + size) <= 5 and ((s1 <= s2) or ((n2 + size) > 5)):
            e1.extend(b)
            s1 += elo_b
            n1 += size
        else:
            e2.extend(b)
            s2 += elo_b
            n2 += size
    return e1, e2, s1, s2


def lista_nombres_10(e1, e2):
    n1 = [p["nombre"] for p in e1][:5]
    n2 = [p["nombre"] for p in e2][:5]
    n1 += [""] * (5 - len(n1))
    n2 += [""] * (5 - len(n2))
    return n1 + n2


def generar_mejor(bloques, intentos=5000, seed=1):
    """
    Busca una asignación que minimice la diferencia de ELO total.
    Devuelve (lista10, best_diff_aprox).
    """
    random.seed(seed * 97 + 3)

    n = len(bloques)
    indices = list(range(n))

    best_diff = float("inf")
    best_list = None

    for _ in range(intentos):
        random.shuffle(indices)
        e1, e2, s1, s2 = evaluar_asignacion(bloques, indices)
        diff = abs(s1 - s2)

        if diff < best_diff:
            best_diff = diff
            best_list = lista_nombres_10(e1, e2)

            # corte temprano suave
            if best_diff <= 20:
                break

    return best_list, best_diff


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
    - Si SÍ hay duplas/tríos: usa búsqueda por seeds (respeta bloques) + dedupe por matchup_key.

    diff_max se usa como preferencia de corte temprano en el modo con bloques.
    """
    if not bloques:
        return [], []

    name2elo = _name2elo_from_bloques(bloques)

    # ============
    # Caso 10 singles: enumeración exacta (126)
    # ============
    if len(bloques) == 10 and all(len(b) == 1 for b in bloques):
        names = [b[0]["nombre"] for b in bloques]
        # ancla para evitar contar swap equipo1/equipo2 dos veces
        anchor = names[0]
        others = names[1:]

        candidatos = []  # (diff, elo1, elo2, lista10)
        for comb in itertools.combinations(others, 4):
            team1 = [anchor] + list(comb)
            team2 = [n for n in names if n not in team1]

            elo1 = int(sum(name2elo.get(n, 0) for n in team1))
            elo2 = int(sum(name2elo.get(n, 0) for n in team2))
            diff = abs(elo1 - elo2)

            lista10 = team1 + team2
            candidatos.append((diff, elo1, elo2, lista10))

        candidatos.sort(key=lambda x: x[0])

        # preferimos <= diff_max si hay suficientes, si no, devolvemos igual lo mejor existente
        dentro = [c for c in candidatos if c[0] <= diff_max]
        base = dentro if len(dentro) >= n_opciones else candidatos

        base = base[:min(n_opciones, len(base))]
        opciones = [c[3] for c in base]
        diffs = [c[0] for c in base]
        return opciones, diffs

    # ============
    # Caso con bloques (duplas/tríos): búsqueda + dedupe
    # ============
    mejores_por_key = {}  # key -> (diff, lista10)

    seed_base = 11
    for pruebas in range(1, max_busquedas + 1):
        lista, _ = generar_mejor(
            bloques,
            intentos=intentos_por_busqueda,
            seed=seed_base + pruebas * 13
        )
        if not lista:
            continue

        key = matchup_key(lista)
        diff, _, _ = _diff_real(lista, name2elo)

        prev = mejores_por_key.get(key)
        if (prev is None) or (diff < prev[0]):
            mejores_por_key[key] = (diff, lista)

        # corte temprano si ya juntamos suficientes dentro del umbral
        if len([1 for d, _l in mejores_por_key.values() if d <= diff_max]) >= n_opciones:
            break

    ordenadas = sorted(mejores_por_key.values(), key=lambda x: x[0])
    opciones = [it[1] for it in ordenadas[:n_opciones]]
    diffs = [it[0] for it in ordenadas[:n_opciones]]
    return opciones, diffs


# -------------------------
# Guardar / borrar equipos elegidos
# -------------------------
def guardar_opcion(partido_id: int, combinacion):
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
# Selección de partido y panel
# -------------------------
def panel_generacion():
    st.subheader("⚽ Generar equipos (hasta 12 opciones, por tandas de 3)")

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

    st.markdown("**Jugadores inscriptos (10):**")
    names = [j["nombre"] for j in jugadores]
    if len(names) != 10:
        st.warning(f"Se requieren exactamente 10 jugadores para generar equipos. Actualmente: {len(names)}.")
        return

    col_a, col_b = st.columns(2)
    with col_a:
        for n in names[:5]:
            st.write(f"- {n}")
    with col_b:
        for n in names[5:10]:
            st.write(f"- {n}")

    ui_definir_bloques(partido_id, names)

    jugadores = obtener_jugadores_partido_full(partido_id)
    bloques = construir_bloques(jugadores)

    # =========================
    # Generar opciones (paginadas 3 en 3)
    # =========================
    cgen, calt = st.columns([1, 1])

    with cgen:
        if st.button("🎲 Generar 3 opciones balanceadas", key="btn_generar_opciones"):
            with st.spinner("Buscando hasta 12 alternativas (ordenadas por ΔELO real)..."):
                opts, diffs = generar_opciones_unicas(
                    bloques,
                    n_opciones=12,
                    diff_max=350,
                    max_busquedas=1200,
                    intentos_por_busqueda=3500
                )
                if not opts:
                    st.error("No se pudieron generar opciones. Revisá duplas/tríos o que haya 10 jugadores.")
                    return

                st.session_state._equipos_opciones = opts
                st.session_state._equipos_diffs = diffs
                st.session_state._equipos_actual = None
                st.session_state._equipos_page = 0  # siempre vuelve a las más parejas
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

        for local_i, col in enumerate(cols[:len(opts_page)]):
            global_i = start + local_i
            lista10 = opts_page[local_i]

            t1 = [n for n in lista10[:5] if n]
            t2 = [n for n in lista10[5:] if n]
            elo1 = int(sum(elo_map.get(n, 0) for n in t1))
            elo2 = int(sum(elo_map.get(n, 0) for n in t2))
            delta = abs(elo1 - elo2)

            col.markdown(f"### Opción {global_i + 1}")
            col.write(f"ΔELO = {delta}")
            col.caption(f"Equipo 1: {elo1} · Equipo 2: {elo2}")

            col.markdown("**Equipo 1**")
            for n in t1:
                col.write(f"- {n}")

            col.markdown("**Equipo 2**")
            for n in t2:
                col.write(f"- {n}")

            if col.button(f"Seleccionar Opción {global_i + 1}", key=f"btn_sel_opt_{global_i + 1}"):
                chosen_idx = global_i

        if chosen_idx is not None:
            st.session_state._equipos_actual = opts[chosen_idx][:]
            st.success(f"Opción {chosen_idx + 1} cargada. Podés intercambiar jugadores antes de confirmar.")

    # =========================
    # Ajuste manual + confirmar
    # =========================
    if st.session_state.get("_equipos_actual"):
        st.markdown("### ✍️ Ajuste manual")

        equipo_actual = st.session_state._equipos_actual
        team1 = equipo_actual[:5]
        team2 = equipo_actual[5:]

        elo_map = {j["nombre"]: j["elo"] for j in jugadores}
        elo1 = int(sum(elo_map.get(n, 0) for n in team1 if n))
        elo2 = int(sum(elo_map.get(n, 0) for n in team2 if n))

        c1, c2 = st.columns(2)
        with c1:
            st.markdown(f"**Equipo 1 ({elo1} ELO)**")
            st.write(", ".join([n for n in team1 if n]))
            a = st.selectbox("Jugador de Equipo 1", ["(ninguno)"] + [n for n in team1 if n], key="swap_a")
        with c2:
            st.markdown(f"**Equipo 2 ({elo2} ELO)**")
            st.write(", ".join([n for n in team2 if n]))
            b = st.selectbox("Jugador de Equipo 2", ["(ninguno)"] + [n for n in team2 if n], key="swap_b")

        if st.button("↔️ Intercambiar", key="btn_swap"):
            if a != "(ninguno)" and b != "(ninguno)":
                i1 = team1.index(a)
                i2 = team2.index(b)
                team1[i1], team2[i2] = team2[i2], team1[i1]
                st.session_state._equipos_actual = team1 + team2
                st.rerun()

        equipo_actual = st.session_state._equipos_actual
        team1 = equipo_actual[:5]
        team2 = equipo_actual[5:]
        elo1 = int(sum(elo_map.get(n, 0) for n in team1 if n))
        elo2 = int(sum(elo_map.get(n, 0) for n in team2 if n))
        st.markdown(f"**Equipo 1 ({elo1} ELO)**: " + ", ".join([n for n in team1 if n]))
        st.markdown(f"**Equipo 2 ({elo2} ELO)**: " + ", ".join([n for n in team2 if n]))

        if st.button("✅ Confirmar equipos", key="btn_confirmar_equipos"):
            if len([n for n in team1 if n]) == 5 and len([n for n in team2 if n]) == 5:
                guardar_opcion(partido_id, equipo_actual)

                if obtener_camiseta_equipo(partido_id, 1) is None:
                    asignar_camiseta_equipo(partido_id, 1, "clara")
                if obtener_camiseta_equipo(partido_id, 2) is None:
                    asignar_camiseta_equipo(partido_id, 2, "oscura")

                st.success("Equipos confirmados y guardados en la base de datos.")
                st.session_state._equipos_opciones = None
                st.session_state._equipos_diffs = None
                st.session_state._equipos_actual = None
                st.session_state._equipos_page = 0
                st.rerun()
            else:
                st.error("Cada equipo debe tener exactamente 5 jugadores.")

    if st.button("⬅️ Volver al menú principal", key="btn_back_bottom"):
        st.session_state.admin_page = None
        st.rerun()
