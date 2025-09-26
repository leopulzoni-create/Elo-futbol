# pages/99_admin_rotate.py
import streamlit as st
from passlib.hash import pbkdf2_sha256  # sin backends nativos
from db import get_connection

st.set_page_config(page_title="Rotar contraseña admin", page_icon="🔐")

st.title("🔐 Rotar contraseña de admin")

# Paso 1: validar que exista la tabla 'usuarios'
with get_connection() as conn:
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='usuarios'")
    if cur.fetchone() is None:
        st.error("La tabla 'usuarios' no existe. Corré las migraciones o prepará la base antes de usar esta página.")
        st.stop()

# Paso 2: chequear que exista el usuario 'admin'
with get_connection() as conn:
    cur = conn.cursor()
    cur.execute("SELECT id, username FROM usuarios WHERE username = 'admin' LIMIT 1")
    row = cur.fetchone()

if not row:
    st.error("No existe el usuario 'admin' en la base actual.")
    st.stop()

admin_id = row["id"] if isinstance(row, dict) else row[0]

st.success("Base y usuario 'admin' encontrados.")

# Paso 3: UI para ingresar nueva contraseña
st.markdown("### Nueva contraseña")
new_pwd = st.text_input("Ingresá la nueva contraseña", type="password")
new_pwd2 = st.text_input("Repetí la nueva contraseña", type="password")

if st.button("Actualizar contraseña"):
    if not new_pwd:
        st.warning("La contraseña no puede estar vacía.")
        st.stop()
    if new_pwd != new_pwd2:
        st.warning("Las contraseñas no coinciden.")
        st.stop()
    if len(new_pwd) < 4:
        st.warning("Usá al menos 4 caracteres.")
        st.stop()

    # Generar hash con PBKDF2 (mismo esquema que usa la app)
    new_hash = pbkdf2_sha256.hash(new_pwd)

    with get_connection() as conn:
        cur = conn.cursor()
        # Detectar columna exacta (password_hash, password, pwd)
        cur.execute("PRAGMA table_info(usuarios)")
        cols = [ (r["name"] if isinstance(r, dict) else r[1]) for r in cur.fetchall() ]
        target_col = None
        for c in ("password_hash", "password", "pwd"):
            if c in cols:
                target_col = c
                break
        if not target_col:
            st.error("No encontré ninguna columna de contraseña en 'usuarios' (password_hash/password/pwd).")
            st.stop()

        cur.execute(f"UPDATE usuarios SET {target_col} = ? WHERE id = ?", (new_hash, admin_id))
        conn.commit()

    st.success("Contraseña de 'admin' actualizada correctamente ✅")
    st.info("Ya podés iniciar sesión con la nueva contraseña.")
