import sqlite3
import hashlib
from db import get_connection

DB_NAME = "elo_futbol.db"

# Datos del administrador
username = "admin"
password = "topo123"
password_hash = hashlib.sha256(password.encode()).hexdigest()
rol = "admin"

# Conexión a la base
conn = get_connection()
cur = conn.cursor()

# Insertar un jugador para vincular al admin (opcional, si no hay jugador aún)
cur.execute("INSERT INTO jugadores (nombre, elo_actual, estado) VALUES (?, ?, ?)",
            ("Administrador", 1000, "activo"))
jugador_id = cur.lastrowid

# Insertar usuario admin
cur.execute("INSERT INTO usuarios (jugador_id, username, password_hash, rol) VALUES (?, ?, ?, ?)",
            (jugador_id, username, password_hash, rol))

conn.commit()
conn.close()

print("Usuario administrador creado con éxito.")