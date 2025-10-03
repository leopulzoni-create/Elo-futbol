# crear_admin.py
import streamlit as st
from db import get_connection
from usuarios import hash_password

def ensure_admin_user():
    admin_conf = st.secrets.get("admin", {})
    username = (admin_conf.get("username") or "").strip()
    password = admin_conf.get("password")
    if not username or not password:
        return  # falta en secrets

    pwd_hash = hash_password(password)

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS usuarios (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              username TEXT UNIQUE,
              password_hash TEXT,
              rol TEXT,
              jugador_id INTEGER,
              grupos INTEGER
            )
        """)
        conn.commit()

        cur.execute("SELECT id, password_hash, rol, grupos FROM usuarios WHERE username = ? LIMIT 1", (username,))
        row = cur.fetchone()

        if not row:
            # no existe -> crear
            cur.execute(
                "INSERT INTO usuarios (username, password_hash, rol, grupos) VALUES (?, ?, 'admin', -1)",
                (username, pwd_hash)
            )
            conn.commit()
            print(f"Admin '{username}' creado desde secrets ✅")
        else:
            # existe -> asegurar hash, rol y grupos
            needs_update = False
            updates = []
            params = []

            if str(row["password_hash"] or "") != pwd_hash:
                updates.append("password_hash = ?")
                params.append(pwd_hash)
                needs_update = True

            if (row["rol"] or "").lower() != "admin":
                updates.append("rol = 'admin'")
                needs_update = True

            if row["grupos"] is None or int(row["grupos"]) != -1:
                updates.append("grupos = -1")
                needs_update = True

            if needs_update:
                params.append(row["id"])
                cur.execute(f"UPDATE usuarios SET {', '.join(updates)} WHERE id = ?", params)
                conn.commit()
                print(f"Admin '{username}' actualizado desde secrets 🔄")
