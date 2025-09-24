# tools/sync_user_groups_to_m2m.py
# Sincroniza usuarios.grupos_mask -> jugador_grupos (M2M)
# Compatible con Python < 3.9 (usa typing.List) y con filas tuple/Row.

from pathlib import Path
import sys, os
from typing import List

# === HACK de ruta para que se vea db.py (que está en el directorio padre) ===
REPO_ROOT = Path(__file__).resolve().parent.parent  # sube de /tools a raíz del repo
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from db import get_connection  # ahora sí se puede importar

# --- Si Mu no “ve” tus variables de entorno, podés ponerlas acá TEMPORALMENTE ---
# ¡NO COMITEES tu token a GitHub!
# os.environ.setdefault("LIBSQL_URL", "libsql://<TU-URL-DE-TURSO>")
# os.environ.setdefault("LIBSQL_AUTH_TOKEN", "<TU-TOKEN>")

# Cambiá a True para una corrida de prueba (no escribe nada).
DRY_RUN = True

# ----------------- helpers para filas dict-safe -----------------
def _rows_to_dicts(cur, rows):
    """Convierte filas (Row o tuple) en dicts usando cur.description."""
    if not rows:
        return []
    try:
        # Si ya son dict o sqlite3.Row
        return [dict(r) for r in rows]
    except Exception:
        cols = [d[0] for d in cur.description] if cur.description else []
        out = []
        for r in rows:
            out.append({cols[i]: r[i] for i in range(min(len(cols), len(r)))})
        return out

# ----------------- bitmask utils -----------------
def _bit_for_gid(gid: int) -> int:
    return 1 << (int(gid) - 1)

def _decode_mask(mask: int, all_gids: List[int]) -> List[int]:
    """Devuelve los grupo_id cuyo bit está prendido en mask."""
    return [int(gid) for gid in all_gids if (int(mask) & _bit_for_gid(int(gid))) != 0]

# ----------------- main -----------------
def main():
    with get_connection() as conn:
        cur = conn.cursor()

        # 1) Todos los grupos
        cur.execute("SELECT id FROM grupos ORDER BY id ASC")
        all_gids = [int(r.get("id") if hasattr(r, "get") else dict(zip([d[0] for d in cur.description],[*r]))["id"])
                    for r in _rows_to_dicts(cur, cur.fetchall())]

        # 2) Usuarios con jugador vinculado
        cur.execute("""
            SELECT id, jugador_id, grupos_mask
            FROM usuarios
            WHERE jugador_id IS NOT NULL
        """)
        users = _rows_to_dicts(cur, cur.fetchall())

        total_deleted = 0
        total_inserted = 0

        for u in users:
            jid = u.get("jugador_id")
            mask_val = u.get("grupos_mask")
            try:
                mask = int(mask_val) if mask_val is not None else None
            except Exception:
                mask = None

            if not jid or mask is None:
                continue

            gids = _decode_mask(mask, all_gids)

            # Semántica “Todos”: si la máscara cubre todos los grupos actuales
            if gids and set(map(int, gids)) == set(map(int, all_gids)):
                gids = all_gids[:]  # explícito

            if DRY_RUN:
                print(f"[DRY] jugador_id={jid} -> grupos {gids}")
                continue

            # Limpiar y reinsertar
            cur.execute("DELETE FROM jugador_grupos WHERE jugador_id = ?", (jid,))
            if cur.rowcount and cur.rowcount > 0:
                total_deleted += cur.rowcount

            for gid in gids:
                cur.execute(
                    "INSERT OR IGNORE INTO jugador_grupos (jugador_id, grupo_id) VALUES (?, ?)",
                    (jid, int(gid))
                )
                total_inserted += 1

        if not DRY_RUN:
            conn.commit()
            print(f"OK. Borradas ~{total_deleted} filas y creadas {total_inserted}.")
        else:
            print("DRY_RUN activo: no se escribió nada.")

if __name__ == "__main__":
    main()
