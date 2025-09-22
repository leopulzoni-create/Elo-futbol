import streamlit as st
import sqlite3
import pandas as pd

DB_NAME = "elo_futbol.db"


def get_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

# =====================
# Helpers de mensajes (flash persistente)
# =====================

def _flash(msg: str, kind: str = "success"):
    """Guarda un mensaje para mostrarse luego del rerun."""
    st.session_state["_flash_msg"] = {"kind": kind, "msg": msg}


def _render_flash():
    data = st.session_state.pop("_flash_msg", None)
    if not data:
        return
    kind = data.get("kind", "success")
    msg = data.get("msg", "")
    if kind == "success":
        st.success(msg)
    elif kind == "info":
        st.info(msg)
    elif kind == "warning":
        st.warning(msg)
    else:
        st.error(msg)

# =====================
# NÚMERO PÚBLICO para canchas
# =====================

def _ensure_schema():
    """Asegura columna numero_publico y la completa si está vacía.
    También crea un índice único para evitar duplicados.
    """
    with get_connection() as conn:
        cur = conn.cursor()
        # ¿Existe la columna numero_publico?
        cur.execute("PRAGMA table_info('canchas')")
        cols = {r[1] for r in cur.fetchall()}  # r[1] = name
        if "numero_publico" not in cols:
            cur.execute("ALTER TABLE canchas ADD COLUMN numero_publico INTEGER")
            conn.commit()
        # Índice único para el número público
        cur.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_canchas_numero_publico ON canchas(numero_publico)"
        )
        conn.commit()
        # Completar números faltantes (sin renumerar existentes)
        cur.execute("SELECT numero_publico FROM canchas WHERE numero_publico IS NOT NULL")
        used = {r[0] for r in cur.fetchall()}
        cur.execute("SELECT id FROM canchas WHERE numero_publico IS NULL ORDER BY id ASC")
        to_assign = [r[0] for r in cur.fetchall()]
        for cid in to_assign:
            n = _next_public_number_from_set(used)
            cur.execute(
                "UPDATE canchas SET numero_publico = ? WHERE id = ?",
                (n, cid),
            )
            used.add(n)
        conn.commit()


def _next_public_number_from_set(used: set) -> int:
    n = 1
    while n in used:
        n += 1
    return n


def _get_next_public_number() -> int:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT numero_publico FROM canchas WHERE numero_publico IS NOT NULL")
        used = {r[0] for r in cur.fetchall()}
    return _next_public_number_from_set(used)

# =====================
# Consultas
# =====================

def _listar_canchas():
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, numero_publico, nombre, direccion, foto FROM canchas ORDER BY numero_publico ASC, nombre ASC"
        )
        return cur.fetchall()

# =====================
# Panel principal
# =====================

def panel_canchas():
    _ensure_schema()
    st.subheader("Gestión de canchas 🏟️")
    _render_flash()  # muestra mensajes persistentes si existen

    accion = st.radio(
        "Selecciona acción:",
        ["Crear cancha", "Editar / Eliminar cancha", "Ver canchas"],
    )

    # --- CREAR CANCHA ---
    if accion == "Crear cancha":
        nombre = st.text_input("Nombre de la cancha")
        direccion = st.text_input("Dirección")
        foto = st.text_input("URL o path de la foto")

        if st.button("Crear cancha", key="btn_crear_cancha"):
            if nombre.strip() == "":
                st.error("Debe ingresar un nombre válido.")
            else:
                with get_connection() as conn:
                    cur = conn.cursor()
                    # nombre único
                    cur.execute("SELECT COUNT(*) FROM canchas WHERE nombre = ?", (nombre,))
                    existe = cur.fetchone()[0]
                    if existe:
                        st.error(f"Ya existe una cancha con el nombre '{nombre}'.")
                    else:
                        numero_pub = _get_next_public_number()
                        cur.execute(
                            "INSERT INTO canchas (numero_publico, nombre, direccion, foto) VALUES (?, ?, ?, ?)",
                            (numero_pub, nombre, direccion, foto),
                        )
                        conn.commit()
                        _flash(f"Cancha N° {numero_pub} — '{nombre}' creada con éxito ✅.")
                        st.rerun()

    # --- EDITAR / ELIMINAR CANCHA ---
    elif accion == "Editar / Eliminar cancha":
        canchas = _listar_canchas()
        if not canchas:
            st.info("No hay canchas cargadas.")
        else:
            opciones = [
                f"N° {c['numero_publico'] or '?'} — {c['nombre']} (id {c['id']})" for c in canchas
            ]
            cancha_sel = st.selectbox("Selecciona una cancha", opciones, key="sb_cancha_edit")
            cancha_id = int(cancha_sel.split("id ")[-1].rstrip(")")) if "id " in cancha_sel else int(cancha_sel.split("(")[-1][3:-1])
            # Datos actuales
            with get_connection() as conn:
                cur = conn.cursor()
                cur.execute("SELECT * FROM canchas WHERE id = ?", (cancha_id,))
                cancha = cur.fetchone()

            st.caption(f"Número público: **{cancha['numero_publico']}** (asignado automáticamente)")
            nuevo_nombre = st.text_input("Nombre", value=cancha["nombre"], key=f"cancha_nombre_{cancha_id}")
            nueva_direccion = st.text_input("Dirección", value=cancha["direccion"] or "", key=f"cancha_dir_{cancha_id}")
            nueva_foto = st.text_input("Foto", value=cancha["foto"] or "", key=f"cancha_foto_{cancha_id}")

            col1, col2 = st.columns(2)
            with col1:
                if st.button("Guardar cambios", key=f"btn_guardar_cancha_{cancha_id}"):
                    with get_connection() as conn:
                        cur = conn.cursor()
                        # nombre único (excluyendo la misma cancha)
                        cur.execute(
                            "SELECT COUNT(*) FROM canchas WHERE nombre = ? AND id != ?",
                            (nuevo_nombre, cancha_id),
                        )
                        existe = cur.fetchone()[0]
                        if existe:
                            st.error(f"Ya existe otra cancha con el nombre '{nuevo_nombre}'.")
                        else:
                            cur.execute(
                                "UPDATE canchas SET nombre = ?, direccion = ?, foto = ? WHERE id = ?",
                                (nuevo_nombre, nueva_direccion, nueva_foto, cancha_id),
                            )
                            conn.commit()
                            _flash("Cambios guardados ✅.")
                            st.rerun()

            with col2:
                if st.button("Eliminar cancha", key=f"btn_eliminar_cancha_{cancha_id}"):
                    with get_connection() as conn:
                        cur = conn.cursor()
                        # Desasociar partidos que usaban esta cancha
                        cur.execute("UPDATE partidos SET cancha_id = NULL WHERE cancha_id = ?", (cancha_id,))
                        cur.execute("DELETE FROM canchas WHERE id = ?", (cancha_id,))
                        conn.commit()
                    _flash("Cancha eliminada ❌. El número público queda libre para reutilizarse.")
                    st.rerun()

    # --- VER CANCHAS (tabla) ---
    elif accion == "Ver canchas":
        rows = _listar_canchas()
        if rows:
            df = pd.DataFrame([dict(r) for r in rows]).rename(
                columns={
                    "numero_publico": "N°",
                    "nombre": "Nombre",
                    "direccion": "Dirección",
                    "foto": "Foto",
                    "id": "ID (interno)",
                }
            )[["N°", "Nombre", "Dirección", "Foto", "ID (interno)"]]
            st.dataframe(df, use_container_width=True, hide_index=True)
            st.caption("El número público es consecutivo y reutiliza huecos al crear nuevas canchas.")
        else:
            st.info("No hay canchas cargadas todavía.")

    # --- VOLVER (único botón, al final) ---
    st.markdown("---")
    if st.button("⬅️ Volver al menú principal", key="btn_back_canchas"):
        st.session_state.admin_page = None
        st.rerun()
