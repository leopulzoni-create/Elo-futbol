# pages/99_admin_rotate.py
import os, sqlite3
import streamlit as st
from passlib.hash import bcrypt  # requerirá passlib[bcrypt] en requirements.txt

DB_NAME = "elo_futbol.db"

st.title("Admin tools — Rotar password de admin")

maint = st.text_input("Maintenance token", type="password")
if not maint:
    st.stop()

# Verificación ultra simple vía secret
if maint != st.secrets.get("ROOT_MAINT_TOKEN", ""):
    st.error("Token inválido.")
    st.stop()

new_pwd = st.text_input("Nueva contraseña para 'admin'", type="password")
do = st.button("Rotar contraseña")

if do:
    if not new_pwd or len(new_pwd) < 10:
        st.error("Usá una contraseña larga (10+ caracteres).")
        st.stop()
    # Generar hash bcrypt
    new_hash = bcrypt.hash(new_pwd)
    with sqlite3.connect(DB_NAME) as conn:
        cur = conn.cursor()
        cur.execute("SELECT id FROM usuarios WHERE username='admin'")
        row = cur.fetchone()
        if not row:
            st.error("No existe el usuario 'admin'.")
        else:
            cur.execute("UPDATE usuarios SET password_hash=? WHERE username='admin'", (new_hash,))
            conn.commit()
            st.success("Password de 'admin' rotado correctamente.")
            st.info("Recordá borrar esta página del repo cuando termines.")
