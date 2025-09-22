from db import get_connection
# init_db.py
import sqlite3, hashlib

DB_NAME = "elo_futbol.db"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS grupos (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  nombre TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS jugadores (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  nombre TEXT NOT NULL,
  elo_actual INTEGER NOT NULL,
  grupo_id INTEGER,
  estado TEXT CHECK(estado IN ('activo','inactivo')),
  foto TEXT,
  FOREIGN KEY (grupo_id) REFERENCES grupos(id)
);
CREATE TABLE IF NOT EXISTS usuarios (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  jugador_id INTEGER,
  username TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  rol TEXT CHECK(rol IN ('admin','jugador')) NOT NULL,
  FOREIGN KEY (jugador_id) REFERENCES jugadores(id)
);
CREATE TABLE IF NOT EXISTS canchas (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  nombre TEXT NOT NULL,
  direccion TEXT,
  foto TEXT
);
CREATE TABLE IF NOT EXISTS partidos (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  fecha DATETIME NOT NULL,
  cancha_id INTEGER,
  ganador INTEGER CHECK(ganador IN (0,1,2)),
  diferencia_gol INTEGER,
  es_oficial INTEGER NOT NULL CHECK(es_oficial IN (0,1)) DEFAULT 0,
  tipo TEXT CHECK(tipo IN ('abierto','cerrado')) NOT NULL DEFAULT 'abierto',
  FOREIGN KEY (cancha_id) REFERENCES canchas(id)
);
CREATE TABLE IF NOT EXISTS partido_grupos (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  partido_id INTEGER NOT NULL,
  grupo_id INTEGER NOT NULL,
  FOREIGN KEY (partido_id) REFERENCES partidos(id),
  FOREIGN KEY (grupo_id) REFERENCES grupos(id)
);
CREATE TABLE IF NOT EXISTS confirmaciones (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  partido_id INTEGER NOT NULL,
  jugador_id INTEGER NOT NULL,
  confirmado INTEGER CHECK(confirmado IN (0,1)),
  fecha_confirmacion DATETIME,
  FOREIGN KEY (partido_id) REFERENCES partidos(id),
  FOREIGN KEY (jugador_id) REFERENCES jugadores(id)
);
CREATE TABLE IF NOT EXISTS partido_jugadores (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  partido_id INTEGER NOT NULL,
  jugador_id INTEGER NOT NULL,
  equipo INTEGER CHECK(equipo IN (1,2)),
  camiseta TEXT CHECK(camiseta IN ('clara','oscura')),
  bloque INTEGER,
  confirmado_por_jugador INTEGER,
  FOREIGN KEY (partido_id) REFERENCES partidos(id),
  FOREIGN KEY (jugador_id) REFERENCES jugadores(id)
);
CREATE TABLE IF NOT EXISTS historial_elo (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  jugador_id INTEGER NOT NULL,
  partido_id INTEGER NOT NULL,
  elo_antes INTEGER NOT NULL,
  elo_despues INTEGER NOT NULL,
  fecha DATETIME DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (jugador_id) REFERENCES jugadores(id),
  FOREIGN KEY (partido_id) REFERENCES partidos(id)
);
"""

def ensure_schema_and_admin():
    conn = get_connection()
    cur = conn.cursor()
    cur.executescript(SCHEMA_SQL)
    conn.commit()

    # crear admin por única vez si no hay usuarios
    cur.execute("SELECT COUNT(*) FROM usuarios")
    if cur.fetchone()[0] == 0:
        cur.execute("INSERT INTO jugadores (nombre, elo_actual, estado) VALUES (?, ?, ?)",
                    ("Administrador", 1000, "activo"))
        jugador_id = cur.lastrowid
        pwd_hash = hashlib.sha256("topo123".encode()).hexdigest()
        cur.execute("INSERT INTO usuarios (jugador_id, username, password_hash, rol) VALUES (?, ?, ?, 'admin')",
                    (jugador_id, "admin", pwd_hash))
        conn.commit()
    conn.close()