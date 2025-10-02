import streamlit as st
from db import get_connection
from usuarios import hash_password

def ensure_admin_user():
    admin_conf = st.secrets.get("admin", {})
    username = admin_conf.get("username")
    password = admin_conf.get("password")
    if not username or not password:
        return  # no está configurado en secrets

    pwd_hash = hash_password(password)

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("CREATE TABLE IF NOT EXISTS usuarios (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE, password_hash TEXT, rol TEXT, jugador_id INTEGER, grupos INTEGER)")
        conn.commit()

        cur.execute("SELECT id FROM usuarios WHERE username = ?", (username,))
        row = cur.fetchone()
        if not row:
            cur.execute(
                "INSERT INTO usuarios (username, password_hash, rol, grupos) VALUES (?, ?, 'admin', -1)",
                (username, pwd_hash)
            )
            conn.commit()
            print(f"Admin '{username}' creado desde secrets ✅")
