from db import get_connection
# usuarios.py
import streamlit as st
import sqlite3
import hashlib

DB_NAME = "elo_futbol.db"

# =========================
# Helpers base de datos
# =========================
def get_connection():
    from db import get_connection as _gc
    return _gc()

    return conn

# Usa hash de auth si existe; si no, SHA-256 (MVP local)
_HASH_VIA_AUTH = False
try:
    from auth import hash_password as _auth_hash_password
    _HASH_VIA_AUTH = True
except Exception:
    pass

def _sha256_hash(pwd: str) -> str:
    return hashlib.sha256(pwd.encode("utf-8")).hexdigest()

def hash_password(pwd: str) -> str:
    return _auth_hash_password(pwd) if _HASH_VIA_AUTH else _sha256_hash(pwd)

def _set_flash(msg: str, typ: str = "success"):
    st.session_state["_flash_msg"] = msg
    st.session_state["_flash_type"] = typ

def _render_and_clear_flash_at_bottom():
    """Muestra el flash (si existe) al final del panel y luego lo limpia."""
    msg = st.session_state.get("_flash_msg")
    typ = st.session_state.get("_flash_type", "info")
    if msg:
        if   typ == "success": st.success(msg)
        elif typ == "warning": st.warning(msg)
        elif typ == "error":   st.error(msg)
        else:                  st.info(msg)
        st.session_state.pop("_flash_msg", None)
        st.session_state.pop("_flash_type", None)

# =========================
# Helpers de grupos (bitmask)
# =========================
# Convención:
# - Si usuarios.grupos == -1  => acceso a TODOS los grupos.
# - Si >= 0 => bitmask de grupos seleccionados (bit de grupo i es 1 << (id-1)).

def load_groups():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS grupos (id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT NOT NULL UNIQUE)")
    conn.commit()
    cur.execute("SELECT id, nombre FROM grupos ORDER BY nombre ASC")
    rows = cur.fetchall()
    conn.close()
    return rows

def bit_for_group_id(group_id: int) -> int:
    return 1 << (group_id - 1)

def encode_groups_to_bitmask(selected_group_ids, todos_selected: bool) -> int:
    if todos_selected:
        return -1
    mask = 0
    for gid in selected_group_ids:
        mask |= bit_for_group_id(gid)
    return mask

def decode_bitmask_to_group_ids(mask: int, all_groups_ids) -> list:
    if mask is None or mask < 0:
        return []
    active = []
    for gid in all_groups_ids:
        if mask & bit_for_group_id(gid):
            active.append(gid)
    return active

def is_todos(mask: int) -> bool:
    return mask is not None and mask < 0

def display_groups_from_mask(mask: int, groups_rows) -> str:
    if is_todos(mask):
        return "Todos"
    ids = [g["id"] for g in groups_rows]
    active_ids = decode_bitmask_to_group_ids(mask or 0, ids)
    names = [g["nombre"] for g in groups_rows if g["id"] in active_ids]
    return ", ".join(names) if names else "(sin grupos)"

# =========================
# UI principal
# =========================
def panel_gestion():
    st.subheader("Administrar usuarios 👤")

    accion = st.radio(
        "Selecciona acción:",
        ["Crear usuario", "Editar usuario", "Eliminar usuario", "Ver usuarios"],
        key="usuarios_accion_radio"
    )

    # Utilidades de datos
    def cargar_jugadores():
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT id, nombre FROM jugadores ORDER BY estado DESC, nombre ASC")
        data = cur.fetchall()
        conn.close()
        return data

    def cargar_usuarios():
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("CREATE TABLE IF NOT EXISTS grupos (id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT NOT NULL UNIQUE)")
        conn.commit()
        cur.execute("""
            SELECT u.id, u.username, u.rol, u.jugador_id, u.grupos, j.nombre AS jugador_nombre
            FROM usuarios u
            LEFT JOIN jugadores j ON j.id = u.jugador_id
            ORDER BY u.rol ASC, u.username ASC
        """)
        data = cur.fetchall()
        conn.close()
        return data

    # -------------------------
    # CREAR USUARIO
    # -------------------------
    if accion == "Crear usuario":
        st.write("### Crear nuevo usuario")

        username = st.text_input("Nombre de usuario", key="usuarios_create_username")
        password = st.text_input("Contraseña", type="password", key="usuarios_create_password")
        rol = st.selectbox("Rol", ["jugador", "admin"], key="usuarios_create_rol")

        # Jugador vinculado (opcional)
        jugadores = cargar_jugadores()
        opciones_j = ["(sin vincular)"] + [f"{j['id']} - {j['nombre']}" for j in jugadores]
        vinculo_sel = st.selectbox("Vincular a jugador (opcional)", opciones_j, key="usuarios_create_vinculo")

        # Grupos
        groups = load_groups()
        group_names = [g["nombre"] for g in groups]
        group_map_name_to_id = {g["nombre"]: g["id"] for g in groups}
        st.write("**Grupos de acceso**")
        todos_flag = st.checkbox("Todos (acceso a todos los grupos)", key="usuarios_create_todos")
        group_selection = []
        if not todos_flag:
            group_selection = st.multiselect(
                "Seleccioná uno o más grupos",
                options=group_names,
                key="usuarios_create_group_multiselect"
            )

        if st.button("Crear usuario", key="usuarios_create_btn"):
            if not username.strip():
                st.error("El nombre de usuario no puede estar vacío.")
            elif not password:
                st.error("La contraseña no puede estar vacía.")
            else:
                conn = get_connection()
                cur = conn.cursor()

                # Username único
                cur.execute("SELECT COUNT(*) AS c FROM usuarios WHERE username = ?", (username,))
                if cur.fetchone()["c"]:
                    conn.close()
                    st.error(f"Ya existe un usuario con username '{username}'.")
                else:
                    # Vinculación opcional y única
                    jugador_id = None
                    if vinculo_sel != "(sin vincular)":
                        jugador_id = int(vinculo_sel.split(" - ")[0])
                        cur.execute("SELECT COUNT(*) AS c FROM usuarios WHERE jugador_id = ?", (jugador_id,))
                        if cur.fetchone()["c"]:
                            conn.close()
                            st.error("Ese jugador ya está vinculado a otro usuario.")
                            return

                    # Calcular bitmask de grupos
                    selected_ids = [group_map_name_to_id[n] for n in group_selection] if group_selection else []
                    grupos_mask = encode_groups_to_bitmask(selected_ids, todos_flag)

                    pwd_hash = hash_password(password)
                    cur.execute(
                        "INSERT INTO usuarios (jugador_id, username, password_hash, rol, grupos) VALUES (?, ?, ?, ?, ?)",
                        (jugador_id, username, pwd_hash, rol, grupos_mask)
                    )
                    conn.commit()
                    conn.close()

                    _set_flash(f"Usuario '{username}' creado con éxito ✅", "success")
                    st.rerun()

    # -------------------------
    # EDITAR USUARIO
    # -------------------------
    elif accion == "Editar usuario":
        st.write("### Editar usuario existente")

        usuarios = cargar_usuarios()
        if not usuarios:
            st.info("No hay usuarios cargados.")
        else:
            opciones_u = [f"{u['id']} - {u['username']} ({u['rol']})" for u in usuarios]
            usuario_sel = st.selectbox("Selecciona usuario", opciones_u, key="usuarios_edit_sel")
            usuario_id = int(usuario_sel.split(" - ")[0])

            u_row = next(u for u in usuarios if u["id"] == usuario_id)
            nuevo_username = st.text_input("Nuevo username", value=u_row["username"], key=f"usuarios_edit_username_{usuario_id}")
            nuevo_rol = st.selectbox("Rol", ["jugador", "admin"], index=(0 if u_row["rol"]=="jugador" else 1), key=f"usuarios_edit_rol_{usuario_id}")

            # Vinculación
            jugadores = cargar_jugadores()
            mapa_j = {j["id"]: j["nombre"] for j in jugadores}
            opciones_j = ["(sin vincular)"] + [f"{j['id']} - {j['nombre']}" for j in jugadores]
            if u_row["jugador_id"] and u_row["jugador_id"] in mapa_j:
                actual_label = f"{u_row['jugador_id']} - {mapa_j[u_row['jugador_id']]}"
                default_index = opciones_j.index(actual_label) if actual_label in opciones_j else 0
            else:
                default_index = 0
            vinculo_sel = st.selectbox("Vincular a jugador (opcional)", opciones_j, index=default_index, key=f"usuarios_edit_vinc_{usuario_id}")

            # Grupos (decode bitmask)
            groups = load_groups()
            group_names = [g["nombre"] for g in groups]
            ids_all = [g["id"] for g in groups]
            group_map_name_to_id = {g["nombre"]: g["id"] for g in groups}
            grupos_mask = u_row["grupos"] if u_row["grupos"] is not None else 0
            todos_flag_default = is_todos(grupos_mask)
            active_ids = decode_bitmask_to_group_ids(grupos_mask, ids_all)
            default_selected_names = [g["nombre"] for g in groups if g["id"] in active_ids]

            st.write("**Grupos de acceso**")
            todos_flag = st.checkbox("Todos (acceso a todos los grupos)", value=todos_flag_default, key=f"usuarios_edit_todos_{usuario_id}")
            group_selection = default_selected_names
            if not todos_flag:
                group_selection = st.multiselect(
                    "Seleccioná uno o más grupos",
                    options=group_names,
                    default=default_selected_names,
                    key=f"usuarios_edit_group_multiselect_{usuario_id}"
                )

            # Resetear contraseña (opcional)
            reset_pwd = st.checkbox("Resetear contraseña", key=f"usuarios_edit_resetpwd_{usuario_id}")
            nueva_pwd = st.text_input("Nueva contraseña", type="password", key=f"usuarios_edit_newpwd_{usuario_id}") if reset_pwd else None

            if st.button("Guardar cambios", key=f"usuarios_edit_guardar_{usuario_id}"):
                if not nuevo_username.strip():
                    st.error("El username no puede estar vacío.")
                else:
                    conn = get_connection()
                    cur = conn.cursor()

                    # Username único entre otros
                    cur.execute("SELECT COUNT(*) AS c FROM usuarios WHERE username = ? AND id != ?", (nuevo_username, usuario_id))
                    if cur.fetchone()["c"]:
                        conn.close()
                        st.error(f"Ya existe otro usuario con username '{nuevo_username}'.")
                        return

                    # Determinar jugador_id y verificar que no esté vinculado a otro
                    jugador_id = None
                    if vinculo_sel != "(sin vincular)":
                        jugador_id = int(vinculo_sel.split(" - ")[0])
                        cur.execute("SELECT COUNT(*) AS c FROM usuarios WHERE jugador_id = ? AND id != ?", (jugador_id, usuario_id))
                        if cur.fetchone()["c"]:
                            conn.close()
                            st.error("Ese jugador ya está vinculado a otro usuario.")
                            return

                    # Calcular bitmask de grupos
                    selected_ids = [group_map_name_to_id[n] for n in (group_selection or [])] if not todos_flag else []
                    new_mask = encode_groups_to_bitmask(selected_ids, todos_flag)

                    # Update (con o sin reset de contraseña)
                    if reset_pwd:
                        if not (nueva_pwd and nueva_pwd.strip()):
                            conn.close()
                            st.error("Debe ingresar la nueva contraseña.")
                            return
                        pwd_hash = hash_password(nueva_pwd)
                        cur.execute(
                            "UPDATE usuarios SET jugador_id=?, username=?, password_hash=?, rol=?, grupos=? WHERE id=?",
                            (jugador_id, nuevo_username, pwd_hash, nuevo_rol, new_mask, usuario_id)
                        )
                    else:
                        cur.execute(
                            "UPDATE usuarios SET jugador_id=?, username=?, rol=?, grupos=? WHERE id=?",
                            (jugador_id, nuevo_username, nuevo_rol, new_mask, usuario_id)
                        )

                    conn.commit()
                    conn.close()
                    _set_flash("Usuario actualizado ✏️", "success")
                    st.rerun()

    # -------------------------
    # ELIMINAR USUARIO
    # -------------------------
    elif accion == "Eliminar usuario":
        st.write("### Eliminar usuario")

        usuarios = cargar_usuarios()
        if not usuarios:
            st.info("No hay usuarios cargados.")
        else:
            opciones_u = [f"{u['id']} - {u['username']} ({u['rol']})" for u in usuarios]
            usuario_sel = st.selectbox("Selecciona usuario a eliminar", opciones_u, key="usuarios_del_sel")
            usuario_id = int(usuario_sel.split(" - ")[0])

            if st.button("Eliminar usuario", key="usuarios_del_btn"):
                conn = get_connection()
                cur = conn.cursor()
                cur.execute("DELETE FROM usuarios WHERE id = ?", (usuario_id,))
                conn.commit()
                conn.close()
                _set_flash("Usuario eliminado ❌", "success")
                st.rerun()

    # -------------------------
    # VER USUARIOS
    # -------------------------
    elif accion == "Ver usuarios":
        st.write("### Usuarios registrados")
        usuarios = cargar_usuarios()
        groups = load_groups()
        if not usuarios:
            st.info("No hay usuarios cargados.")
        else:
            for u in usuarios:
                vinc = u["jugador_nombre"] if u["jugador_nombre"] else "(sin vincular)"
                grupos_txt = display_groups_from_mask(u["grupos"] if u["grupos"] is not None else 0, groups)
                st.write(f"ID: {u['id']} | Usuario: {u['username']} | Rol: {u['rol']} | Jugador: {vinc} | Grupos: {grupos_txt}")

    # -------------------------
    # GESTIÓN DE GRUPOS (crear / renombrar / eliminar)
    # -------------------------
    st.write("---")
    st.write("### Gestión de grupos")

    # Crear grupo
    nuevo_grupo = st.text_input("Nombre del grupo (ej. Grupo Martes)", key="grupo_nuevo_nombre")
    if st.button("Crear grupo", key="grupo_nuevo_btn"):
        nombre = (nuevo_grupo or "").strip()
        if not nombre:
            st.error("Debes ingresar un nombre de grupo.")
        else:
            conn = get_connection()
            cur = conn.cursor()
            cur.execute("CREATE TABLE IF NOT EXISTS grupos (id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT NOT NULL UNIQUE)")
            conn.commit()
            try:
                cur.execute("INSERT INTO grupos (nombre) VALUES (?)", (nombre,))
                conn.commit()
                _set_flash(f"Grupo '{nombre}' creado ✅", "success")
            except sqlite3.IntegrityError:
                _set_flash(f"Ya existe un grupo llamado '{nombre}'.", "warning")
            finally:
                conn.close()
            st.rerun()

    # Listado con editar / eliminar (alineado en la misma fila)
    grupos = load_groups()
    if grupos:
        st.write("#### Grupos existentes")
        for g in grupos:
            with st.container():
                col1, col2, col3 = st.columns([4, 1.2, 1.2])
                with col1:
                    # Label obligatorio (usamos uno discreto) + ID como caption
                    nuevo_nombre = st.text_input(
                        " ",  # label mínimo para evitar el TypeError y no ocupar altura
                        value=g["nombre"],
                        key=f"grp_name_{g['id']}",
                    )
                    st.caption(f"ID {g['id']}")
                with col2:
                    st.write("")  # espaciador para alinear verticalmente
                    if st.button("Renombrar", key=f"grp_ren_{g['id']}", width='stretch'):
                        name = (nuevo_nombre or "").strip()
                        if not name:
                            st.error("El nombre no puede quedar vacío.")
                        else:
                            conn = get_connection()
                            cur = conn.cursor()
                            try:
                                cur.execute("UPDATE grupos SET nombre=? WHERE id=?", (name, g["id"]))
                                conn.commit()
                                _set_flash(f"Grupo ID {g['id']} renombrado a '{name}' ✅", "success")
                            except sqlite3.IntegrityError:
                                _set_flash(f"Ya existe un grupo con el nombre '{name}'.", "warning")
                            finally:
                                conn.close()
                            st.rerun()
                with col3:
                    st.write("")  # espaciador para alinear verticalmente
                    if st.button("Eliminar", key=f"grp_del_{g['id']}", width='stretch'):
                        # Eliminar directo (sin checkbox)
                        conn = get_connection()
                        cur = conn.cursor()
                        try:
                            bit = bit_for_group_id(g["id"])
                            # limpiar bit (grupos >= 0); mantener -1 (Todos)
                            cur.execute("""
                                UPDATE usuarios
                                   SET grupos = CASE
                                                    WHEN grupos IS NULL THEN 0
                                                    WHEN grupos < 0 THEN grupos
                                                    ELSE (grupos & ?)
                                                END
                            """, (~bit,))
                            conn.commit()
                            # borrar vínculos con partidos si la tabla existe
                            try:
                                cur.execute("DELETE FROM partido_grupos WHERE grupo_id = ?", (g["id"],))
                                conn.commit()
                            except Exception:
                                pass
                            # borrar el grupo
                            cur.execute("DELETE FROM grupos WHERE id = ?", (g["id"],))
                            conn.commit()
                            _set_flash(f"Grupo '{g['nombre']}' eliminado ❌", "success")
                        finally:
                            conn.close()
                        st.rerun()
    else:
        st.info("No hay grupos creados todavía.")

    # -------------------------
    # FLASH (debajo) + VOLVER
    # -------------------------
    _render_and_clear_flash_at_bottom()

    if st.button("⬅️ Volver al menú principal", key="usuarios_back"):
        st.session_state.admin_page = None
        st.rerun()