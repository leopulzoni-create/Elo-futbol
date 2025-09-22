from db import get_connection
# scheduler.py
# Disparo perezoso de programaciones y materialización de partidos.
# - Usa hora local (datetime.now()) para comparar publicar_desde/next_publicar_desde.
# - Soporta repetición semanal.
# - Inserta plantilla de jugadores al materializar (sin exceder cupo).

import sqlite3
from datetime import datetime, timedelta

DB_NAME = "elo_futbol.db"
CUPO_PARTIDO = 10

def get_connection():
    conn = get_connection()

    return conn

# -------------------------
# Helpers de número público
# -------------------------
def ensure_aux_tables(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS numeros_libres_partidos (
            n INTEGER PRIMARY KEY
        )
    """)

def next_numero_publico(cur):
    ensure_aux_tables(cur)
    cur.execute("SELECT MIN(n) AS n FROM numeros_libres_partidos")
    row = cur.fetchone()
    if row and row["n"] is not None:
        return int(row["n"]), "libre"
    cur.execute("SELECT COALESCE(MAX(numero_publico), 0) AS m FROM partidos")
    m = cur.fetchone()["m"] or 0
    return int(m) + 1, "nuevo"

def consumir_numero_publico(cur, numero_publico: int):
    cur.execute("DELETE FROM numeros_libres_partidos WHERE n = ?", (numero_publico,))

# -------------------------
# Migrations mínimas
# -------------------------
def _ensure_schema(cur):
    # programaciones
    cur.execute("""
        CREATE TABLE IF NOT EXISTS programaciones (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          partido_base_id INTEGER NOT NULL,
          repeat_semanal INTEGER NOT NULL DEFAULT 0,
          next_publicar_desde TEXT NOT NULL,
          hora_juego INTEGER,
          cancha_id INTEGER,
          enabled INTEGER NOT NULL DEFAULT 1,
          FOREIGN KEY (partido_base_id) REFERENCES partidos(id)
        )
    """)
    # columna publicar_desde (por compatibilidad; la instancia materializada no la usa)
    try:
        cur.execute("ALTER TABLE partidos ADD COLUMN publicar_desde TEXT")
    except Exception:
        pass
    # plantilla_jugadores: jugadores que deben arrancar confirmados al materializar
    cur.execute("""
        CREATE TABLE IF NOT EXISTS plantilla_jugadores (
            partido_base_id INTEGER NOT NULL,
            jugador_id INTEGER NOT NULL,
            orden INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (partido_base_id, jugador_id),
            FOREIGN KEY (partido_base_id) REFERENCES partidos(id),
            FOREIGN KEY (jugador_id) REFERENCES jugadores(id)
        )
    """)

# -------------------------
# Lazy trigger
# -------------------------
def run_programaciones_vencidas() -> int:
    """
    Materializa todas las programaciones con next_publicar_desde <= ahora y enabled=1.
    - Crea partido 'abierto' con numero_publico.
    - Copia grupos del base.
    - Inserta plantilla_jugadores al roster sin exceder cupo (confirmados, camiseta 'clara').
    - Si semanal: avanza base.fecha y next_publicar_desde +7d, deja enabled=1.
      Si no: set enabled=0.
    Devuelve cantidad de partidos creados.
    """
    now = datetime.now()
    created = 0

    with get_connection() as conn:
        cur = conn.cursor()
        _ensure_schema(cur)

        # Traer programaciones vencidas (enabled=1)
        cur.execute("""
            SELECT pr.id, pr.partido_base_id, pr.repeat_semanal, pr.next_publicar_desde,
                   pr.hora_juego, pr.cancha_id, pr.enabled,
                   pb.fecha AS base_fecha, pb.numero_publico AS base_np
            FROM programaciones pr
            JOIN partidos pb ON pb.id = pr.partido_base_id
            WHERE pr.enabled = 1
        """)
        progs = cur.fetchall()

        for pr in progs:
            try:
                pub_dt = datetime.strptime(pr["next_publicar_desde"], "%Y-%m-%d %H:%M:%S")
            except Exception:
                # formato alternativo por si llegó sin segundos
                try:
                    pub_dt = datetime.strptime(pr["next_publicar_desde"], "%Y-%m-%d %H:%M")
                except Exception:
                    continue

            if pub_dt > now:
                continue  # todavía no

            # #1 número público nuevo para la instancia
            numero_publico, _ = next_numero_publico(cur)

            # #2 fecha/hora/cancha
            # fecha del partido = fecha del base (plantilla)
            fecha_juego_str = pr["base_fecha"]  # 'YYYY-MM-DD'
            hora_juego = pr["hora_juego"] or 1900  # HHMM
            cancha_id = pr["cancha_id"]

            # #3 crear partido visible (abierto, sin ganador)
            cur.execute("""
                INSERT INTO partidos (fecha, cancha_id, es_oficial, tipo, hora, numero_publico, ganador, diferencia_gol, publicar_desde)
                VALUES (?, ?, 0, 'abierto', ?, ?, NULL, NULL, NULL)
            """, (fecha_juego_str, cancha_id, hora_juego, numero_publico))
            partido_id = cur.lastrowid
            consumir_numero_publico(cur, numero_publico)

            # #4 copiar grupos del base
            cur.execute("SELECT grupo_id FROM partido_grupos WHERE partido_id = ?", (pr["partido_base_id"],))
            base_groups = [r["grupo_id"] for r in cur.fetchall()]
            if base_groups:
                cur.executemany(
                    "INSERT INTO partido_grupos (partido_id, grupo_id) VALUES (?, ?)",
                    [(partido_id, gid) for gid in base_groups]
                )

            # #5 roster inicial a partir de plantilla (respetar cupo y jugadores activos)
            cur.execute("""
                SELECT pj.jugador_id, COALESCE(j.estado,'activo') AS estado
                FROM plantilla_jugadores pj
                JOIN jugadores j ON j.id = pj.jugador_id
                WHERE pj.partido_base_id = ?
                ORDER BY pj.orden ASC, pj.jugador_id ASC
            """, (pr["partido_base_id"],))
            plantilla = [r["jugador_id"] for r in cur.fetchall() if (r["estado"] == "activo")]

            if plantilla:
                # no exceder CUPO_PARTIDO
                plantilla = plantilla[:CUPO_PARTIDO]
                to_insert = [(partido_id, jid, 1, 'clara', 0) for jid in plantilla]
                cur.executemany("""
                    INSERT OR IGNORE INTO partido_jugadores (partido_id, jugador_id, confirmado_por_jugador, camiseta, ingreso_desde_espera)
                    VALUES (?, ?, ?, ?, ?)
                """, to_insert)

            created += 1

            # #6 actualizar programación
            if int(pr["repeat_semanal"] or 0) == 1:
                # avanzar base.fecha y next_publicar_desde + 7 días
                try:
                    base_dt = datetime.strptime(pr["base_fecha"], "%Y-%m-%d")
                    base_dt2 = base_dt + timedelta(days=7)
                    cur.execute("UPDATE partidos SET fecha = ? WHERE id = ?",
                                (base_dt2.strftime("%Y-%m-%d"), pr["partido_base_id"]))
                except Exception:
                    pass
                pub_dt2 = pub_dt + timedelta(days=7)
                cur.execute("UPDATE programaciones SET next_publicar_desde = ? WHERE id = ?",
                            (pub_dt2.strftime("%Y-%m-%d %H:%M:%S"), pr["id"]))
            else:
                # una sola vez
                cur.execute("UPDATE programaciones SET enabled = 0 WHERE id = ?", (pr["id"],))

        conn.commit()

    return created