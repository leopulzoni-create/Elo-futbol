# pages/99_admin_rotate.py
import streamlit as st
import sqlite3
import hashlib

DB_NAME = "elo_futbol.db"

def _table_exists(conn, table: str) -> bool:
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name = ?", (table,))
    return cur.fetchone() is not None

def _get_admin_id(conn):
    cur = conn.cursor()
    cur.execute("SELECT id FROM usuarios WHERE username='admin' LIMIT 1")
    row = cur.fetchone()
    return row[0] if row else None

def _set_admin_password_sha256(conn, user_id: int, plain_pwd: str):
    hash_hex = hashlib.sha256(plain_pwd.encode("utf-8")).hexdigest()
    cur = conn.cursor()
    cur.execute("UPDATE usuarios SET password_hash=? WHERE id=?", (hash_hex, user_id))
    conn.commit()

st.title("Rotar contraseña de admin")

with sqlite3.connect(DB_NAME) as conn:
    if not _table_exists(conn, "usuarios"):
        st.error("La tabla 'usuarios' no existe. Corré las migraciones o creá la base antes de usar esta página.")
    else:
        admin_id = _get_admin_id(conn)
        if not admin_id:
            st.error("No existe el usuario 'admin'. Crealo desde el panel de usuarios.")
        else:
            pwd1 = st.text_input("Nueva contraseña", type="password")
            pwd2 = st.text_input("Repetir contraseña", type="password")
            if st.button("Actualizar contraseña"):
                if not pwd1:
                    st.warning("Ingresá una contraseña.")
                elif pwd1 != pwd2:
                    st.warning("Las contraseñas no coinciden.")
                elif len(pwd1) < 4:
                    st.warning("Usá al menos 4 caracteres.")
                else:
                    try:
                        _set_admin_password_sha256(conn, admin_id, pwd1)
                        st.success("Contraseña de 'admin' actualizada con éxito (SHA-256).")
                    except sqlite3.Error as e:
                        st.error(f"Error de base de datos: {e}")
