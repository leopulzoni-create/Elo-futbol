# partidos.py
import streamlit as st
import sqlite3
from typing import List, Dict, Optional, Tuple
from datetime import datetime, date, time as dtime, time as _time, date as _date

DB_NAME = "elo_futbol.db"

def get_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

# ---------- Helpers de fecha/hora y texto ----------
_DIAS_ES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]

def weekday_es(yyyy_mm_dd: str) -> str:
    try:
        dt = datetime.strptime(yyyy_mm_dd, "%Y-%m-%d").date()
        return _DIAS_ES[dt.weekday()]
    except Exception:
        return ""

def time_int_from_time(t: dtime) -> int:
    return t.hour * 100 + t.minute

def time_from_int_str(hhmm_int: int) -> dtime:
    if hhmm_int is None:
        return dtime(19, 0)
    hh = int(hhmm_int) // 100
    mm = int(hhmm_int) % 100
    try:
        return dtime(hh, mm)
    except Exception:
        return dtime(19, 0)

def time_label(hhmm_int: int) -> str:
    if hhmm_int is None:
        return "Sin hora"
    hh = int(hhmm_int) // 100
    mm = int(hhmm_int) % 100
    return f"{hh:02d}:{mm:02d}"

# ---------- Paleta de colores ----------
COLORES = [
    "#1e293b", "#3b0764", "#164e63", "#4a044e",
    "#0b3a3d", "#2b2c58", "#3c1c4f", "#052e2e",
]
def color_por_partido(pid: int) -> str:
    return COLORES[pid % len(COLORES)]

# ---------- numero_publico ----------
def ensure_aux_tables(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS numeros_libres_partidos (
            n INTEGER PRIMARY KEY
        )
    """)

def next_numero_publico(cur):
    ensure_aux_tables(cur)
    cur.execute("SELECT MIN(n) AS n FROM numeros_libres_partidos")
    row = cur.fetchone()
    if row and row["n"] is not None:
        return int(row["n"]), "libre"
    cur.execute("SELECT COALESCE(MAX(numero_publico), 0) AS m FROM partidos")
    m = cur.fetchone()["m"] or 0
    return int(m) + 1, "nuevo"

def consumir_numero_publico(cur, numero_publico: int):
    cur.execute("DELETE FROM numeros_libres_partidos WHERE n = ?", (numero_publico,))

def liberar_numero_publico(cur, numero_publico: int):
    ensure_aux_tables(cur)
    cur.execute("INSERT OR IGNORE INTO numeros_libres_partidos(n) VALUES (?)", (numero_publico,))

# ---------- GRUPOS (partido_grupos) ----------
def get_all_groups(cur):
    cur.execute("SELECT id, nombre FROM grupos ORDER BY nombre ASC")
    return cur.fetchall()

def get_groups_for_partido(cur, partido_id: int):
    cur.execute("""
        SELECT g.id, g.nombre
        FROM partido_grupos pg
        JOIN grupos g ON g.id = pg.grupo_id
        WHERE pg.partido_id = ?
        ORDER BY g.nombre ASC
    """, (partido_id,))
    return cur.fetchall()

def set_groups_for_partido(cur, partido_id: int, group_ids):
    cur.execute("DELETE FROM partido_grupos WHERE partido_id = ?", (partido_id,))
    if group_ids:
        cur.executemany(
            "INSERT INTO partido_grupos (partido_id, grupo_id) VALUES (?, ?)",
            [(partido_id, gid) for gid in group_ids]
        )

# Sugerencia de grupos por día
_MAP_DIA_TO_TOKEN = {1: "martes", 3: "jueves", 6: "domingo"}
def suggested_group_ids_for_date(fecha_obj: date, grupos_rows):
    if not fecha_obj:
        return []
    token = _MAP_DIA_TO_TOKEN.get(fecha_obj.weekday())
    if not token:
        return []
    token = token.lower()
    sug = []
    for g in grupos_rows:
        nombre = (g["nombre"] or "").lower()
        if token in nombre:
            sug.append(g["id"])
    return sug

# ---------- Lista de espera: migraciones mínimas ----------
def ensure_waitlist_schema(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS lista_espera (
            partido_id INTEGER NOT NULL,
            jugador_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (partido_id, jugador_id),
            FOREIGN KEY (partido_id) REFERENCES partidos(id),
            FOREIGN KEY (jugador_id) REFERENCES jugadores(id)
        )
    """)
    cur.execute("PRAGMA table_info(partido_jugadores)")
    cols = [row["name"] for row in cur.fetchall()]
    if "ingreso_desde_espera" not in cols:
        cur.execute("ALTER TABLE partido_jugadores ADD COLUMN ingreso_desde_espera INTEGER DEFAULT 0")

# ---------- Plantilla de jugadores para programaciones ----------
def ensure_plantilla_schema(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS plantilla_jugadores (
            partido_base_id INTEGER NOT NULL,
            jugador_id INTEGER NOT NULL,
            orden INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (partido_base_id, jugador_id),
            FOREIGN KEY (partido_base_id) REFERENCES partidos(id),
            FOREIGN KEY (jugador_id) REFERENCES jugadores(id)
        )
    """)

def get_plantilla(cur, base_id: int) -> List[int]:
    cur.execute("""
        SELECT pj.jugador_id, j.nombre, pj.orden
        FROM plantilla_jugadores pj
        JOIN jugadores j ON j.id = pj.jugador_id
        WHERE pj.partido_base_id = ?
        ORDER BY pj.orden ASC, j.nombre ASC
    """, (base_id,))
    return cur.fetchall()

def set_plantilla(cur, base_id: int, jugador_ids: List[int]) -> None:
    cur.execute("DELETE FROM plantilla_jugadores WHERE partido_base_id = ?", (base_id,))
    if jugador_ids:
        rows = [(base_id, jid, i) for i, jid in enumerate(jugador_ids)]
        cur.executemany("""
            INSERT INTO plantilla_jugadores (partido_base_id, jugador_id, orden)
            VALUES (?, ?, ?)
        """, rows)

# ---------- UI principal ----------
def panel_creacion():
    st.subheader("Gestión de partidos ⚽")

    conn = get_connection()
    cur = conn.cursor()
    ensure_aux_tables(cur)
    ensure_waitlist_schema(cur)
    ensure_plantilla_schema(cur)
    # compat publicar_desde
    try:
        cur.execute("ALTER TABLE partidos ADD COLUMN publicar_desde TEXT")
    except Exception:
        pass
    conn.commit()

    # --- CREAR / PROGRAMAR PARTIDO ---
    st.write("### Crear nuevo partido")

    # Fecha y hora de juego
    fecha = st.date_input("Fecha del partido", value=_date.today(), key="crear_fecha")
    hora_juego = st.time_input("Hora del partido", value=_time(hour=19, minute=0), key="crear_hora")

    # Cancha (opcional)
    cur.execute("SELECT id, nombre FROM canchas ORDER BY nombre ASC")
    canchas = cur.fetchall()
    opciones_canchas = ["Sin asignar"] + [f"{c['id']} - {c['nombre']}" for c in canchas]
    cancha_sel = st.selectbox("Seleccionar cancha (opcional)", opciones_canchas, key="crear_cancha_sel")
    cancha_id = int(cancha_sel.split(" - ")[0]) if cancha_sel != "Sin asignar" else None

    # Grupos (multiselect) con preselección automática por día
    cur.execute("SELECT id, nombre FROM grupos ORDER BY nombre ASC")
    grupos_rows = cur.fetchall()
    grupos_dict = {g["nombre"]: g["id"] for g in grupos_rows}
    grupos_nombres = list(grupos_dict.keys())

    def _preseleccion_por_dia(dt):
        if not grupos_nombres:
            return []
        nombre_dia = ["lunes","martes","miércoles","jueves","viernes","sábado","domingo"][dt.weekday()]
        nombre_dia_sin_tilde = {"miércoles":"miercoles","sábado":"sabado"}.get(nombre_dia, nombre_dia)
        pre = []
        for n in grupos_nombres:
            n_low = n.lower()
            if "martes" in n_low and "martes" in nombre_dia_sin_tilde:
                pre.append(n)
            elif "jueves" in n_low and "jueves" in nombre_dia_sin_tilde:
                pre.append(n)
            elif "domingo" in n_low and "domingo" in nombre_dia_sin_tilde:
                pre.append(n)
        return pre

    predef = _preseleccion_por_dia(fecha)
    grupos_sel_names = st.multiselect(
        "Grupos que podrán ver este partido",
        options=grupos_nombres,
        default=predef,
        key="crear_grupos_ms"
    )
    grupos_sel_ids = [grupos_dict[n] for n in grupos_sel_names]
    st.caption("Sugerencia automática por día (Martes/Jueves/Domingo) — podés ajustar manualmente.")

    # --- Programación (opcional) ---
    st.markdown("#### Publicación (opcional)")
    programar = st.checkbox("Programar publicación (no visible hasta la fecha/hora elegidas)", key="prog_chk")
    fecha_pub = None
    hora_pub = None
    repetir_semanal = False

    # Plantilla inicial (solo si se programa)
    plantilla_sel_ids = []
    if programar:
        fecha_pub = st.date_input("Publicar desde (fecha)", value=_date.today(), key="prog_fecha")
        hora_pub = st.time_input("Publicar desde (hora)", value=_time(hour=9, minute=0), key="prog_hora")
        repetir_semanal = st.checkbox("Repetir semanalmente (crea un partido nuevo cada semana)", key="prog_repeat")

        # Elegir jugadores para arrancar confirmados al materializar
        cur.execute("SELECT id, nombre FROM jugadores WHERE estado = 'activo' ORDER BY nombre ASC")
        jug_rows = cur.fetchall()
        jug_map = {f"{r['nombre']} (ID {r['id']})": r["id"] for r in jug_rows}
        pre_plantilla = st.multiselect(
            "Plantilla inicial (opcional): jugadores que aparecerán confirmados al publicar",
            options=list(jug_map.keys()),
            key="prog_plantilla_ms"
        )
        plantilla_sel_ids = [jug_map[k] for k in pre_plantilla]

    col_create, col_prog = st.columns(2)

    # ===== Crear partido visible ya =====
    with col_create:
        if st.button("✅ Crear partido (visible ya)", key="btn_crear_inmediato"):
            try:
                numero_publico, _ = next_numero_publico(cur)
            except Exception:
                cur.execute("SELECT COALESCE(MAX(numero_publico), 0) AS mx FROM partidos")
                row = cur.fetchone()
                numero_publico = (row["mx"] or 0) + 1

            hhmm = hora_juego.hour * 100 + hora_juego.minute

            cur.execute(
                "INSERT INTO partidos (fecha, cancha_id, es_oficial, tipo, hora, numero_publico, publicar_desde) "
                "VALUES (?, ?, 0, 'abierto', ?, ?, NULL)",
                (fecha.strftime("%Y-%m-%d"), cancha_id, hhmm, numero_publico)
            )
            nuevo_id = cur.lastrowid
            consumir_numero_publico(cur, numero_publico)

            if grupos_sel_ids:
                cur.executemany(
                    "INSERT INTO partido_grupos (partido_id, grupo_id) VALUES (?, ?)",
                    [(nuevo_id, gid) for gid in grupos_sel_ids]
                )

            conn.commit()
            st.success(f"Partido N° {numero_publico} creado y visible ✅")

    # ===== Programar partido =====
    with col_prog:
        if st.button("📅 Programar partido", key="btn_programar"):
            if not programar or not (fecha_pub and hora_pub):
                st.warning("Elegí fecha y hora de publicación, o desmarcá la opción de programar.")
            else:
                # schema mínimos
                try:
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS programaciones (
                          id INTEGER PRIMARY KEY AUTOINCREMENT,
                          partido_base_id INTEGER NOT NULL,
                          repeat_semanal INTEGER NOT NULL DEFAULT 0,
                          next_publicar_desde TEXT NOT NULL,
                          hora_juego INTEGER,
                          cancha_id INTEGER,
                          enabled INTEGER NOT NULL DEFAULT 1,
                          FOREIGN KEY (partido_base_id) REFERENCES partidos(id)
                        )
                    """)
                except Exception:
                    pass
                ensure_plantilla_schema(cur)

                # 1) crear PARTIDO BASE tipo 'cerrado'
                try:
                    numero_publico_base, _origen = next_numero_publico(cur)
                except Exception:
                    cur.execute("SELECT COALESCE(MAX(numero_publico), 0) AS mx FROM partidos")
                    row = cur.fetchone()
                    numero_publico_base = (row["mx"] or 0) + 1

                hhmm = hora_juego.hour * 100 + hora_juego.minute
                cur.execute(
                    "INSERT INTO partidos (fecha, cancha_id, es_oficial, tipo, hora, numero_publico, publicar_desde) "
                    "VALUES (?, ?, 0, 'cerrado', ?, ?, NULL)",
                    (fecha.strftime("%Y-%m-%d"), cancha_id, hhmm, numero_publico_base)
                )
                base_id = cur.lastrowid
                consumir_numero_publico(cur, numero_publico_base)

                # 2) grupos del base
                if grupos_sel_ids:
                    cur.executemany(
                        "INSERT INTO partido_grupos (partido_id, grupo_id) VALUES (?, ?)",
                        [(base_id, gid) for gid in grupos_sel_ids]
                    )

                # 3) guardar plantilla inicial (orden según selección)
                if plantilla_sel_ids:
                    set_plantilla(cur, base_id, plantilla_sel_ids)

                # 4) crear programación
                publicar_dt_str = f"{fecha_pub.strftime('%Y-%m-%d')} {hora_pub.strftime('%H:%M')}:00"
                cur.execute("""
                    INSERT INTO programaciones (partido_base_id, repeat_semanal, next_publicar_desde, hora_juego, cancha_id, enabled)
                    VALUES (?, ?, ?, ?, ?, 1)
                """, (base_id, 1 if repetir_semanal else 0, publicar_dt_str, hhmm, cancha_id))

                conn.commit()
                if repetir_semanal:
                    st.success(f"Partido base N° {numero_publico_base} programado semanalmente desde {publicar_dt_str} ⏳")
                else:
                    st.success(f"Partido base N° {numero_publico_base} programado para {publicar_dt_str} ⏳")

                # corregir typo del string (para no romper nada si copiaste rápido 😉)


    # --- PARTIDOS EXISTENTES (pendientes) ---
    st.write("### Partidos existentes (pendientes)")
    cur.execute("""
        SELECT id, fecha, cancha_id, hora, numero_publico
        FROM partidos
        WHERE tipo = 'abierto'
          AND ganador IS NULL
          AND diferencia_gol IS NULL
        ORDER BY fecha DESC, id DESC
    """)
    partidos = cur.fetchall()

    for p in partidos:
        pid = p["id"]
        color = color_por_partido(pid)

        cancha = "Sin asignar"
        if p["cancha_id"]:
            cur.execute("SELECT nombre FROM canchas WHERE id = ?", (p["cancha_id"],))
            cancha_row = cur.fetchone()
            if cancha_row:
                cancha = cancha_row["nombre"]

        dia_es = weekday_es(p["fecha"])
        hora_lbl = time_label(p["hora"])

        st.markdown(
            f"""
            <div style="
                background:{color};
                padding:12px 14px;
                border-radius:10px;
                margin-top:12px;
                font-size:1.25rem;
                font-weight:700;
                color:#ffffff;
            ">
                N° {p['numero_publico']} | Fecha: {p['fecha']} ({dia_es}) | Cancha: {cancha} | Hora: {hora_lbl}
            </div>
            """,
            unsafe_allow_html=True
        )

        # --- GESTIONAR JUGADORES ---
        with st.expander(f"Gestionar jugadores Partido N° {p['numero_publico']}"):
            st.markdown(
                f"""<div style="background:{color};color:#ffffff;padding:12px;border-radius:10px;">""",
                unsafe_allow_html=True
            )

            # Jugadores disponibles (solo activos)
            cur.execute("SELECT id, nombre FROM jugadores WHERE estado = 'activo' ORDER BY nombre ASC")
            jugadores = cur.fetchall()

            # Ya asignados
            cur.execute(
                "SELECT pj.jugador_id, pj.confirmado_por_jugador, j.nombre "
                "FROM partido_jugadores pj "
                "JOIN jugadores j ON j.id = pj.jugador_id "
                "WHERE pj.partido_id = ?", (pid,)
            )
            jugadores_partido = cur.fetchall()
            ids_asignados = [j["jugador_id"] for j in jugadores_partido]

            total_actual = len(jugadores_partido)
            cupo_total = 10
            cupo_restante = max(0, cupo_total - total_actual)

            st.write("### Jugadores asignados")
            cols = st.columns(2)
            for i, jp in enumerate(jugadores_partido):
                icono = "🟢" if jp["confirmado_por_jugador"] else "🔵"
                col = cols[i % 2]
                with col:
                    st.write(f"{icono} {jp['nombre']}")
                    if st.button(
                        f"Quitar {jp['nombre']} del partido N° {p['numero_publico']}",
                        key=f"quitar_{pid}_{jp['jugador_id']}_{i}"
                    ):
                        # 1) Quitar
                        cur.execute(
                            "DELETE FROM partido_jugadores WHERE partido_id = ? AND jugador_id = ?",
                            (pid, jp["jugador_id"])
                        )
                        # 2) Desarmar equipos
                        cur.execute(
                            "UPDATE partido_jugadores SET equipo = NULL, camiseta = NULL WHERE partido_id = ?",
                            (pid,)
                        )
                        # 3) Promover lista de espera
                        cur.execute("""
                            SELECT le.jugador_id, j.nombre
                              FROM lista_espera le
                              JOIN jugadores j ON j.id = le.jugador_id
                             WHERE le.partido_id = ?
                             ORDER BY le.created_at ASC
                             LIMIT 1
                        """, (pid,))
                        prom = cur.fetchone()
                        if prom:
                            cur.execute("""
                                INSERT INTO partido_jugadores
                                    (partido_id, jugador_id, confirmado_por_jugador, camiseta, ingreso_desde_espera)
                                VALUES (?, ?, 1, 'clara', 1)
                            """, (pid, prom["jugador_id"]))
                            cur.execute(
                                "DELETE FROM lista_espera WHERE partido_id = ? AND jugador_id = ?",
                                (pid, prom["jugador_id"])
                            )
                            conn.commit()
                            st.success("Se promovió al primero de la lista de espera.")
                        else:
                            conn.commit()
                            st.info("No había lista de espera.")
                        st.rerun()

            # Agregar jugadores hasta cupo
            st.write(f"### Agregar jugadores al partido ({total_actual}/{cupo_total})")
            if cupo_restante <= 0:
                st.info("Cupo completo: ya hay 10/10 jugadores en este partido.")

            jugadores_dict = {j["nombre"]: j["id"] for j in jugadores if j["id"] not in ids_asignados}

            seleccionados = st.multiselect(
                "Seleccioná hasta completar el cupo",
                options=list(jugadores_dict.keys()),
                key=f"multiselect_{pid}"
            )

            if len(seleccionados) > cupo_restante:
                st.warning(f"Solo podés agregar {cupo_restante} jugador(es) más para no superar {cupo_total}/10.")
                seleccionados = seleccionados[:cupo_restante]

            if st.button(
                f"Agregar jugadores al partido N° {p['numero_publico']}",
                key=f"agregar_{pid}",
                disabled=(cupo_restante <= 0 or len(seleccionados) == 0)
            ):
                for nombre in seleccionados:
                    jugador_id = jugadores_dict[nombre]
                    cur.execute(
                        "INSERT INTO partido_jugadores (partido_id, jugador_id, confirmado_por_jugador) VALUES (?, ?, 0)",
                        (pid, jugador_id)
                    )
                conn.commit()
                st.rerun()

            # Lista de espera (x/4)
            st.write("### Lista de espera (x/4)")
            cur.execute("""
                SELECT le.jugador_id, j.nombre, le.created_at
                  FROM lista_espera le
                  JOIN jugadores j ON j.id = le.jugador_id
                 WHERE le.partido_id = ?
                 ORDER BY le.created_at ASC
            """, (pid,))
            espera = cur.fetchall()
            st.caption(f"({len(espera)}/4)")
            if espera:
                for idx, e in enumerate(espera, start=1):
                    c_wl = st.container()
                    with c_wl:
                        cols_wl = st.columns([8, 2])
                        with cols_wl[0]:
                            st.write(f"{idx}. {e['nombre']}")
                        with cols_wl[1]:
                            if st.button("Quitar", key=f"wl_quitar_{pid}_{e['jugador_id']}_{idx}"):
                                cur.execute(
                                    "DELETE FROM lista_espera WHERE partido_id = ? AND jugador_id = ?",
                                    (pid, e["jugador_id"])
                                )
                                conn.commit()
                                st.toast("Eliminado de la lista de espera.", icon="✅")
                                st.rerun()
            else:
                st.info("No hay jugadores en la lista de espera.")

            st.markdown("</div>", unsafe_allow_html=True)

        # --- EDITAR PARTIDO (fecha, cancha, hora y grupos) ---
        with st.expander(f"Editar partido N° {p['numero_publico']}"):
            st.markdown(
                f"""<div style="background:{color};color:#ffffff;padding:12px;border-radius:10px;">""",
                unsafe_allow_html=True
            )

            fecha_actual = datetime.strptime(p["fecha"], "%Y-%m-%d").date()
            hora_actual = time_from_int_str(p["hora"])

            opciones_canchas_edit = ["Sin asignar"] + [f"{c['id']} - {c['nombre']}" for c in canchas]
            cancha_actual_label = "Sin asignar"
            if p["cancha_id"]:
                for c in canchas:
                    if c["id"] == p["cancha_id"]:
                        cancha_actual_label = f"{c['id']} - {c['nombre']}"
                        break
            idx_pre = opciones_canchas_edit.index(cancha_actual_label) if cancha_actual_label in opciones_canchas_edit else 0

            nueva_fecha = st.date_input("Nueva fecha", value=fecha_actual, key=f"fecha_edit_{pid}")
            nueva_hora = st.time_input("Nueva hora", value=hora_actual, key=f"hora_edit_{pid}")
            nueva_cancha_sel = st.selectbox(
                "Nueva cancha (opcional)", opciones_canchas_edit, index=idx_pre, key=f"cancha_edit_{pid}"
            )
            nueva_cancha_id = int(nueva_cancha_sel.split(" - ")[0]) if nueva_cancha_sel != "Sin asignar" else None

            # Grupos del partido
            grupos_all = get_all_groups(cur)
            opciones_g_all = [f"{g['id']} - {g['nombre']}" for g in grupos_all]
            actuales = get_groups_for_partido(cur, pid)
            labels_actuales = {f"{g['id']} - {g['nombre']}" for g in actuales}

            grupos_edit_sel = st.multiselect(
                "Grupos vinculados a este partido",
                options=opciones_g_all,
                default=sorted(list(labels_actuales)),
                key=f"grupos_edit_{pid}"
            )
            grupos_edit_ids = [int(lbl.split(" - ")[0]) for lbl in grupos_edit_sel]

            sug_ids = suggested_group_ids_for_date(nueva_fecha, grupos_all)
            if st.button("Sugerir grupos por fecha", key=f"sugerir_grupos_{pid}"):
                grupos_edit_ids = sug_ids[:]
                st.session_state[f"grupos_edit_{pid}"] = [
                    f"{g['id']} - {g['nombre']}" for g in grupos_all if g["id"] in grupos_edit_ids
                ]
                st.info("Aplicada la sugerencia de grupos según el día de la fecha seleccionada.")

            c1, c2 = st.columns(2)
            with c1:
                if st.button("Guardar cambios", key=f"guardar_edit_{pid}"):
                    cur.execute(
                        "UPDATE partidos SET fecha = ?, cancha_id = ?, hora = ? WHERE id = ?",
                        (nueva_fecha.strftime("%Y-%m-%d"), nueva_cancha_id, time_int_from_time(nueva_hora), pid)
                    )
                    set_groups_for_partido(cur, pid, grupos_edit_ids)
                    conn.commit()
                    st.success(f"Partido N° {p['numero_publico']} actualizado ✅")
                    st.rerun()
            with c2:
                st.caption("Los cambios impactan de inmediato.")

            st.markdown("</div>", unsafe_allow_html=True)

        # --- ELIMINAR PARTIDO ---
        if st.button(f"Eliminar partido N° {p['numero_publico']}", key=f"eliminar_{pid}"):
            numero_publico = p["numero_publico"]
            liberar_numero_publico(cur, numero_publico)
            cur.execute("DELETE FROM partido_jugadores WHERE partido_id = ?", (pid,))
            cur.execute("DELETE FROM partido_grupos WHERE partido_id = ?", (pid,))
            cur.execute("DELETE FROM lista_espera WHERE partido_id = ?", (pid,))
            cur.execute("DELETE FROM partidos WHERE id = ?", (pid,))
            conn.commit()
            st.success(f"Partido N° {numero_publico} eliminado ❌ (número liberado)")
            st.rerun()

    # --- PROGRAMACIONES ACTIVAS ---
    st.write("### Programaciones activas")
    # schema seguro
    cur.execute("""
        CREATE TABLE IF NOT EXISTS programaciones (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          partido_base_id INTEGER NOT NULL,
          repeat_semanal INTEGER NOT NULL DEFAULT 0,
          next_publicar_desde TEXT NOT NULL,
          hora_juego INTEGER,
          cancha_id INTEGER,
          enabled INTEGER NOT NULL DEFAULT 1,
          FOREIGN KEY (partido_base_id) REFERENCES partidos(id)
        )
    """)
    ensure_plantilla_schema(cur)

    cur.execute("""
        SELECT pr.id, pr.partido_base_id, pr.repeat_semanal, pr.next_publicar_desde, pr.hora_juego, pr.cancha_id,
               pb.numero_publico AS np_base, pb.fecha AS fecha_base
        FROM programaciones pr
        JOIN partidos pb ON pb.id = pr.partido_base_id
        WHERE pr.enabled = 1
        ORDER BY pr.next_publicar_desde ASC
    """)
    progs = cur.fetchall()

    if not progs:
        st.caption("_No hay programaciones activas._")
    else:
        # jugadores activos para selector de plantilla
        cur.execute("SELECT id, nombre FROM jugadores WHERE estado = 'activo' ORDER BY nombre ASC")
        jugadores_activos = cur.fetchall()
        jug_map = {f"{r['nombre']} (ID {r['id']})": r["id"] for r in jugadores_activos}
        jug_rev = {v: k for k, v in jug_map.items()}

        for pr in progs:
            etiqueta = f"Plantilla N° {pr['np_base']}  •  Publicar: {pr['next_publicar_desde']}  •  Juego: {pr['fecha_base']}  •  Semanal: {'Sí' if pr['repeat_semanal'] else 'No'}"
            with st.expander(etiqueta):
                base_id = pr["partido_base_id"]

                # Gestión de plantilla
                st.markdown("**Plantilla de jugadores (se agregan confirmados al publicar)**")
                plantilla_rows = get_plantilla(cur, base_id)
                if plantilla_rows:
                    cols = st.columns(2)
                    for i, row in enumerate(plantilla_rows):
                        txt = jug_rev.get(row["jugador_id"], f"{row['nombre']} (ID {row['jugador_id']})")
                        with cols[i % 2]:
                            st.write(f"• {txt}")
                            if st.button("Quitar", key=f"pl_quitar_{base_id}_{row['jugador_id']}_{i}"):
                                cur.execute("DELETE FROM plantilla_jugadores WHERE partido_base_id=? AND jugador_id=?",
                                            (base_id, row["jugador_id"]))
                                conn.commit()
                                st.rerun()
                else:
                    st.info("No hay jugadores en plantilla.")

                # Agregar a plantilla
                nuevos = st.multiselect(
                    "Agregar jugadores a la plantilla",
                    options=list(jug_map.keys()),
                    key=f"pl_add_ms_{base_id}"
                )
                if st.button("Agregar a plantilla", key=f"pl_add_btn_{base_id}"):
                    # obtener orden actual
                    cur.execute("SELECT COALESCE(MAX(orden), -1) AS mx FROM plantilla_jugadores WHERE partido_base_id=?",
                                (base_id,))
                    mx = cur.fetchone()["mx"]
                    start = int(mx) + 1
                    rows = [(base_id, jug_map[k], start + i) for i, k in enumerate(nuevos)]
                    if rows:
                        cur.executemany("""
                            INSERT OR IGNORE INTO plantilla_jugadores (partido_base_id, jugador_id, orden)
                            VALUES (?, ?, ?)
                        """, rows)
                        conn.commit()
                        st.success("Plantilla actualizada.")
                        st.rerun()

                st.divider()
                c1, c2 = st.columns(2)
                with c1:
                    if st.button("❌ Cancelar programación", key=f"cancel_prog_{pr['id']}"):
                        cur.execute("UPDATE programaciones SET enabled = 0 WHERE id = ?", (pr["id"],))
                        conn.commit()
                        st.success("Programación cancelada.")
                        st.rerun()
                with c2:
                    if st.button("🗑️ Cancelar y eliminar plantilla base", key=f"cancel_y_del_{pr['id']}"):
                        cur.execute("UPDATE programaciones SET enabled = 0 WHERE id = ?", (pr["id"],))
                        cur.execute("DELETE FROM partido_grupos WHERE partido_id = ?", (base_id,))
                        cur.execute("DELETE FROM plantilla_jugadores WHERE partido_base_id = ?", (base_id,))
                        cur.execute("DELETE FROM partidos WHERE id = ?", (base_id,))
                        conn.commit()
                        st.success("Programación cancelada y plantilla eliminada.")
                        st.rerun()

    # --- VOLVER ---
    if st.button("⬅️ Volver al menú principal", key="volver_menu"):
        st.session_state.admin_page = None
        st.rerun()

    conn.close()
