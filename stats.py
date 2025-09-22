from database import get_connection

# ---------- Funciones de estadísticas ----------
def get_player_stats(jugador_id):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("SELECT equipo FROM partido_jugadores WHERE jugador_id = ?", (jugador_id,))
    partidos = cur.fetchall()
    conn.close()

    total = len(partidos)
    if total == 0:
        return {"jugados":0, "victorias":0, "derrotas":0, "empates":0, "winrate":0}

    victorias = sum(1 for p in partidos if p[0] == 1)  # simplificado
    derrotas = sum(1 for p in partidos if p[0] == 2)
    empates = sum(1 for p in partidos if p[0] == 0)
    winrate = (victorias / total) * 100
    return {
        "jugados": total,
        "victorias": victorias,
        "derrotas": derrotas,
        "empates": empates,
        "winrate": round(winrate, 2)
    }

def get_elo_history(jugador_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT partido_id, elo_despues FROM historial_elo WHERE jugador_id = ? ORDER BY partido_id", (jugador_id,))
    rows = cur.fetchall()
    conn.close()
    return [r[1] for r in rows], [r[0] for r in rows]
