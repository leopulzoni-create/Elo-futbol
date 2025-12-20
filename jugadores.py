from db import get_connection
# jugadores.py — versión con mejoras de UX + descripción en 'foto'
import streamlit as st
import sqlite3

DB_NAME = "elo_futbol.db"


def get_connection():
    from db import get_connection as _gc
    return _gc()

    return conn  # (inaccesible, se deja por compat)


# ==== Migración mínima: tabla puente jugador_grupos ====
def _ensure_jugador_grupos():
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS jugador_grupos (
                jugador_id INTEGER NOT NULL,
                grupo_id   INTEGER NOT NULL,
                PRIMARY KEY (jugador_id, grupo_id)
            )
            """
        )
        conn.commit()


def _ensure_descripcion_en_jugadores():
    """Garantiza que exista la columna 'foto' (TEXT), usada como 'descripcion'."""
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("PRAGMA table_info(jugadores)")
            # En la mayoría de los casos row_factory=sqlite3.Row => r["name"]
            cols = [r["name"] if isinstance(r, sqlite3.Row) else r[1] for r in cur.fetchall()]
            if "foto" not in cols:
                cur.execute("ALTER TABLE jugadores ADD COLUMN foto TEXT")
                conn.commit()
    except Exception:
        # Si falla (por ejemplo en Turso ya existe), seguimos sin interrumpir la UI.
        pass


def _cargar_grupos():
    """Devuelve lista de rows con (id, nombre) de la tabla grupos, ordenados por nombre."""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id, nombre FROM grupos ORDER BY nombre ASC")
        return cur.fetchall()


def _labels_grupos(grupos, ids):
    """Devuelve lista de labels 'id - nombre' a partir de IDs."""
    m = {g["id"]: f"{g['id']} - {g['nombre']}" for g in grupos}
    return [m[i] for i in ids if i in m]


def _ids_seleccionados(desde_labels):
    """Convierte labels 'id - nombre' -> lista de ints id."""
    out = []
    for s in desde_labels:
        try:
            out.append(int(s.split(" - ")[0]))
        except Exception:
            pass
    return out


def _get_memberships(jugador_id):
    """IDs de grupos (lista de int) actuales del jugador desde la tabla puente."""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT grupo_id FROM jugador_grupos WHERE jugador_id = ? ORDER BY grupo_id ASC",
            (jugador_id,),
        )
        return [r["grupo_id"] for r in cur.fetchall()]


def _set_memberships(jugador_id, group_ids):
    """Reemplaza membresías del jugador en la tabla puente."""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM jugador_grupos WHERE jugador_id = ?", (jugador_id,))
        if group_ids:
            cur.executemany(
                "INSERT OR IGNORE INTO jugador_grupos (jugador_id, grupo_id) VALUES (?, ?)",
                [(jugador_id, gid) for gid in group_ids],
            )
        conn.commit()


# =======================
# UI principal (admin)
# =======================
def panel_gestion():
    _ensure_jugador_grupos()
    _ensure_descripcion_en_jugadores()
    st.subheader("Gestión de jugadores ⚽")

    accion = st.radio(
        "Selecciona acción:",
        ["Crear jugador", "Editar / Eliminar jugador", "Ver jugadores"],
    )

    # --- CREAR JUGADOR ---
    if accion == "Crear jugador":
        nombre = st.text_input("Nombre del jugador")
        elo_inicial = st.number_input(
            "ELO inicial",
            min_value=0.0,
            value=1000.0,
            step=50.0,
            format="%.0f",
            key="elo_create_v3",
        )
        estado = st.selectbox("Estado", ["activo", "inactivo"])
        descripcion = st.text_area(
            "Descripción (opcional)",
            placeholder="Breve descripción de cómo juega, posición, estilo…",
        )

        # --- Selección de grupos (multiselect) ---
        grupos = _cargar_grupos()
        opciones_grupos = [f"{g['id']} - {g['nombre']}" for g in grupos]
        labels_sel = st.multiselect(
            "Grupos del jugador (podés elegir varios)",
            opciones_grupos,
            key="jug_grps_new",
        )
        grupos_ids = _ids_seleccionados(labels_sel)

        if st.button("Crear jugador", key="jug_create_btn"):
            if nombre.strip() == "":
                st.error("Debe ingresar un nombre válido.")
            else:
                conn = get_connection()
                cur = conn.cursor()
                # Verificar si el nombre ya existe
                cur.execute("SELECT COUNT(*) FROM jugadores WHERE nombre = ?", (nombre,))
                existe = cur.fetchone()[0]
                if existe:
                    st.error(f"Ya existe un jugador con el nombre '{nombre}'.")
                else:
                    # fuente de verdad de grupos: tabla puente (grupo_id queda NULL)
                    cur.execute(
                        "INSERT INTO jugadores (nombre, elo_actual, estado, grupo_id, foto) VALUES (?, ?, ?, NULL, ?)",
                        (nombre, int(elo_inicial), estado, (descripcion or None)),
                    )
                    jugador_id = cur.lastrowid
                    conn.commit()
                    # membresías M2M
                    _set_memberships(jugador_id, grupos_ids)
                    st.success(f"Jugador {nombre} creado con éxito ✅.")
                conn.close()

    # --- EDITAR / ELIMINAR JUGADOR ---
    elif accion == "Editar / Eliminar jugador":
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT id, nombre, elo_actual, estado, grupo_id FROM jugadores ORDER BY nombre ASC")
        jugadores = cur.fetchall()
        conn.close()

        if jugadores:
            opciones = [f"{j['id']} - {j['nombre']}" for j in jugadores]
            jugador_sel = st.selectbox("Selecciona jugador a editar", opciones)
            jugador_id = int(jugador_sel.split(" - ")[0])

            # Obtener datos actuales
            conn = get_connection()
            cur = conn.cursor()
            cur.execute("SELECT * FROM jugadores WHERE id = ?", (jugador_id,))
            jugador = cur.fetchone()
            conn.close()

            # Formulario de edición
            nuevo_nombre = st.text_input("Nombre", value=jugador["nombre"])
            valor_elo = float(round(jugador["elo_actual"] or 0))
            nuevo_elo = st.number_input(
                "ELO",
                min_value=0.0,
                step=50.0,
                value=valor_elo,
                format="%.0f",
                key=f"elo_edit_v3_{jugador_id}",
            )
            nuevo_estado = st.selectbox(
                "Estado", ["activo", "inactivo"],
                index=(0 if jugador["estado"] == "activo" else 1),
            )

            # Descripción (usa la col. 'foto')
            desc_actual = jugador["foto"] if "foto" in jugador.keys() else None
            nueva_desc = st.text_area(
                "Descripción (opcional)",
                value=(desc_actual or ""),
                key=f"desc_edit_{jugador_id}",
            )

            # --- Selección de grupos (multiselect) ---
            grupos = _cargar_grupos()
            opciones_grupos = [f"{g['id']} - {g['nombre']}" for g in grupos]

            # membresías actuales desde la tabla puente
            actuales_ids = set(_get_memberships(jugador_id))
            # compat: si hay grupo_id legacy en jugadores, incluirlo como preseleccionado
            if jugador["grupo_id"]:
                actuales_ids.add(jugador["grupo_id"])

            default_labels = _labels_grupos(grupos, sorted(list(actuales_ids)))
            labels_sel = st.multiselect(
                "Grupos del jugador (podés elegir varios)",
                opciones_grupos,
                default=default_labels,
                key=f"jug_grps_{jugador_id}",
            )
            grupos_ids = _ids_seleccionados(labels_sel)

            c1, c2 = st.columns(2)
            with c1:
                if st.button("💾 Guardar cambios", key=f"jug_save_{jugador_id}"):
                    conn = get_connection()
                    cur = conn.cursor()
                    # Verificar si el nuevo nombre ya existe en otro jugador
                    cur.execute(
                        "SELECT COUNT(*) FROM jugadores WHERE nombre = ? AND id != ?",
                        (nuevo_nombre, jugador_id),
                    )
                    existe = cur.fetchone()[0]
                    if existe:
                        st.error(f"Ya existe otro jugador con el nombre '{nuevo_nombre}'.")
                    else:
                        cur.execute(
                            """
                            UPDATE jugadores
                               SET nombre = ?,
                                   elo_actual = ?,
                                   estado = ?,
                                   grupo_id = NULL,   -- fuente de verdad: tabla puente
                                   foto = ?
                             WHERE id = ?
                            """,
                            (nuevo_nombre, int(nuevo_elo), nuevo_estado, (nueva_desc or None), jugador_id),
                        )
                        conn.commit()
                        # membresías M2M (reemplazo)
                        _set_memberships(jugador_id, grupos_ids)
                        st.success(f"Jugador {nuevo_nombre} actualizado ✏️.")
                    conn.close()

            with c2:
                with st.expander("🗑️ Eliminar jugador", expanded=False):
                    st.warning(
                        "Esta acción es **permanente** y no se puede deshacer. Se eliminará el jugador y sus membresías de grupo.",
                        icon="⚠️",
                    )
                    confirm_txt = st.text_input(
                        "Para confirmar, escribí **ELIMINAR**",
                        key=f"jug_del_confirm_{jugador_id}",
                    )
                    if st.button("Eliminar definitivamente", key=f"jug_del_{jugador_id}"):
                        if confirm_txt.strip().upper() != "ELIMINAR":
                            st.error("Confirmación inválida. Escribí ELIMINAR para continuar.")
                        else:
                            conn = get_connection()
                            cur = conn.cursor()
                            cur.execute(
                                "DELETE FROM jugador_grupos WHERE jugador_id = ?",
                                (jugador_id,),
                            )
                            cur.execute("DELETE FROM jugadores WHERE id = ?", (jugador_id,))
                            conn.commit()
                            conn.close()
                            st.success("Jugador eliminado ❌.")
                            st.rerun()
        else:
            st.info("No hay jugadores cargados.")

    # --- VER JUGADORES (en tabla) ---
    elif accion == "Ver jugadores":
        conn = get_connection()
        cur = conn.cursor()
        # mostramos todos los grupos (si hay), usando group_concat
        cur.execute(
            """
            SELECT j.id,
                   j.nombre,
                   j.elo_actual,
                   j.estado,
                   j.foto AS descripcion,
                   GROUP_CONCAT(g.nombre, ', ') AS grupos
            FROM jugadores j
            LEFT JOIN jugador_grupos jg ON jg.jugador_id = j.id
            LEFT JOIN grupos g ON g.id = jg.grupo_id
            GROUP BY j.id, j.nombre, j.elo_actual, j.estado, j.foto
            ORDER BY (j.estado='activo') DESC, j.nombre ASC
            """
        )
        jugadores = cur.fetchall()
        conn.close()

        if jugadores:
            st.markdown("### Jugadores")
            # Construimos registros para DataFrame/tabla
            data = []
            for j in jugadores:
                data.append(
                    {
                        "ID": j["id"],
                        "Nombre": j["nombre"],
                        "ELO": int(round(j["elo_actual"] or 0)),
                        "Estado": j["estado"],
                        "Grupos": j["grupos"] or "—",
                        "Descripción": (j["descripcion"] or "—"),
                    }
                )
            st.dataframe(data, width='stretch', hide_index=True)
            st.caption("Ordenado por estado y nombre. Podés usar el buscador de la esquina para filtrar.")
        else:
            st.info("No hay jugadores cargados todavía.")

    st.divider()
    if st.button("⬅️ Volver al menú principal", key="jug_back_bottom"):
        st.session_state.admin_page = None
        st.rerun()
