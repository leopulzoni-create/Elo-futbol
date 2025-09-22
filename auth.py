import sqlite3
import hashlib

DB_NAME = "elo_futbol.db"

def get_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def verify_user(username, password):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM usuarios WHERE username = ?", (username,))
    user = cur.fetchone()
    conn.close()

    if user:
        password_hash = hashlib.sha256(password.encode()).hexdigest()
        if password_hash == user["password_hash"]:
            return user  # Devuelve fila completa del usuario
    return None
