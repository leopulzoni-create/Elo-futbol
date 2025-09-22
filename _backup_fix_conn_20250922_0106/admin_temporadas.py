from db import get_connection
# admin_temporadas.py
# Panel admin para definir y finalizar temporadas con rangos arbitrarios (no atadas al año calendario).
# - Tabla seasons: label, start_date, end_date, finalized
# - Botón Finalizar: calcula podios en el rango y persiste en season_awards (finalized=1).

import streamlit as st
import sqlite3
from datetime import date, datetime

DB_NAME = "elo_futbol.db"

def _conn():
    from db import get_connection as _gc
    return _gc()

    return c

def _ensure_tables():
    with _conn() as conn:
        cur = conn.cursor()
        # seasons
        cur.execute("""
        CREATE TABLE IF NOT EXISTS seasons (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          label TEXT NOT NULL UNIQUE,     -- ej: '2025', '2026'
          start_date TEXT NOT NULL,       -- 'YYYY-MM-DD'
          end_date   TEXT,                -- NULL hasta finalizar
          finalized  INTEGER NOT NULL DEFAULT 0
        )
        """)
        # season_awards (por si aún no existe)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS season_awards (
          season TEXT NOT NULL,
          category TEXT NOT NULL,     -- 'most_matches' | 'best_points' | 'most_improved' | 'best_duo'
          place INTEGER NOT NULL,     -- 1,2,3
          jugador_id INTEGER NOT NULL,
          value REAL,
          meta TEXT,
          finalized INTEGER NOT NULL DEFAULT 0,
          awarded_at TEXT,
          PRIMARY KEY (season, category, place, jugador_id)
        )
        """)
        conn.commit()

def _result_condition_sql(alias="p"):
    a = alias
    return f"(({a}.ganador IS NOT NULL) OR ({a}.ganador IS NULL AND IFNULL({a}.diferencia_gol,0)=0))"

# ---------- Cómputo de podios en rango ----------
def _rank_most_matches_range(cur, start, end, top=3):
    cond = _result_condition_sql("p")
    cur.execute(f"""
      SELECT pj.jugador_id, j.nombre, COUNT(*) AS pj
      FROM partido_jugadores pj
      JOIN partidos p ON p.id = pj.partido_id
      JOIN jugadores j ON j.id = pj.jugador_id
      WHERE {cond} AND date(p.fecha) BETWEEN date(?) AND date(?)
      GROUP BY pj.jugador_id
      ORDER BY pj DESC, j.nombre ASC
      LIMIT {top}
    """, (start, end))
    return [dict(r) for r in cur.fetchall()]

def _rank_best_points_range(cur, start, end, min_pj=15, top=3):
    cond = _result_condition_sql("p")
    cur.execute(f"""
      WITH base AS (
        SELECT pj.jugador_id,
               SUM(CASE WHEN p.ganador = pj.equipo THEN 1 ELSE 0 END) AS w,
               SUM(CASE WHEN p.ganador IS NULL AND IFNULL(p.diferencia_gol,0)=0 THEN 1 ELSE 0 END) AS e,
               SUM(CASE WHEN p.ganador IS NOT NULL AND p.ganador <> pj.equipo THEN 1 ELSE 0 END) AS l
        FROM partido_jugadores pj
        JOIN partidos p ON p.id = pj.partido_id
        WHERE {cond} AND date(p.fecha) BETWEEN date(?) AND date(?)
        GROUP BY pj.jugador_id
      ), elig AS (
        SELECT jugador_id, w, e, l, (w+e+l) AS pj,
               (3.0*w + 1.0*e) / NULLIF(3.0*(w+e+l),0) AS puntos_pct
        FROM base
        WHERE (w+e+l) >= ?
      )
      SELECT e.jugador_id, j.nombre, e.pj, e.puntos_pct, e.w, e.e, e.l
      FROM elig e JOIN jugadores j ON j.id = e.jugador_id
      ORDER BY e.puntos_pct DESC, e.pj DESC, e.w DESC, j.nombre ASC
      LIMIT {top}
    """, (start, end, min_pj))
    return [dict(r) for r in cur.fetchall()]

def _rank_most_improved_range(cur, start, end, min_pj=15, top=3):
    cond = _result_condition_sql("p")
    cur.execute(f"""
      SELECT pj.jugador_id, COUNT(*) AS pj
      FROM partido_jugadores pj
      JOIN partidos p ON p.id = pj.partido_id
      WHERE {cond} AND date(p.fecha) BETWEEN date(?) AND date(?)
      GROUP BY pj.jugador_id
      HAVING COUNT(*) >= ?
    """, (start, end, min_pj))
    candidatos = [r["jugador_id"] for r in cur.fetchall()]

    results = []
    for jid in candidatos:
        cur.execute(f"""
          SELECT p.fecha, h.elo_antes, h.elo_despues
          FROM historial_elo h
          JOIN partidos p ON p.id = h.partido_id
          WHERE h.jugador_id = ? AND date(p.fecha) BETWEEN date(?) AND date(?)
          ORDER BY date(p.fecha), p.id
        """, (jid, start, end))
        rows = cur.fetchall()
        if not rows:
            continue
        start_elo = rows[0]["elo_antes"] if rows[0]["elo_antes"] is not None else rows[0]["elo_despues"]
        end_elo   = rows[-1]["elo_despues"] if rows[-1]["elo_despues"] is not None else rows[-1]["elo_antes"]
        if start_elo is None or end_elo is None:
            continue
        delta = float(end_elo) - float(start_elo)
        cur.execute("SELECT nombre FROM jugadores WHERE id=?", (jid,))
        nombre = (cur.fetchone() or {"nombre":"?"})["nombre"]
        results.append({"jugador_id": jid, "nombre": nombre, "delta": delta})

    results.sort(key=lambda x: (x["delta"], x["nombre"]), reverse=True)
    return results[:top]

def _rank_best_duo_range(cur, start, end, min_juntos=10, top=3):
    cond = _result_condition_sql("p")
    cur.execute(f"""
      WITH pairs AS (
        SELECT
          CASE WHEN a.jugador_id < b.jugador_id THEN a.jugador_id ELSE b.jugador_id END AS j1,
          CASE WHEN a.jugador_id < b.jugador_id THEN b.jugador_id ELSE a.jugador_id END AS j2,
          a.partido_id, a.equipo, p.ganador, p.diferencia_gol
        FROM partido_jugadores a
        JOIN partido_jugadores b
          ON b.partido_id = a.partido_id
         AND b.equipo = a.equipo
         AND b.jugador_id > a.jugador_id
        JOIN partidos p ON p.id = a.partido_id
        WHERE {cond} AND date(p.fecha) BETWEEN date(?) AND date(?)
      ),
      agg AS (
        SELECT j1, j2,
               COUNT(*) AS pj,
               SUM(CASE WHEN ganador = equipo THEN 1 ELSE 0 END) AS w,
               SUM(CASE WHEN ganador IS NULL AND IFNULL(diferencia_gol,0)=0 THEN 1 ELSE 0 END) AS e,
               SUM(CASE WHEN ganador IS NOT NULL AND ganador <> equipo THEN 1 ELSE 0 END) AS l
        FROM pairs
        GROUP BY j1, j2
        HAVING COUNT(*) >= ?
      )
      SELECT a.*,
             (3.0*w + 1.0*e) / NULLIF(3.0*pj,0) AS puntos_pct,
             jA.nombre AS nombre1, jB.nombre AS nombre2
      FROM agg a
      JOIN jugadores jA ON jA.id = a.j1
      JOIN jugadores jB ON jB.id = a.j2
      ORDER BY puntos_pct DESC, pj DESC, w DESC, nombre1 ASC, nombre2 ASC
      LIMIT {top}
    """, (start, end, min_juntos))
    return [dict(r) for r in cur.fetchall()]

def _persist_awards(cur, label, category, rows, now_iso):
    cur.execute("DELETE FROM season_awards WHERE season=? AND category=?", (label, category))
    if category == "best_duo":
        for i, r in enumerate(rows[:3], start=1):
            j1, j2 = r["j1"], r["j2"]
            val = float(r.get("puntos_pct") or 0.0)
            meta1 = f'{{"partner_id": {j2}}}'
            meta2 = f'{{"partner_id": {j1}}}'
            cur.execute("""INSERT INTO season_awards(season, category, place, jugador_id, value, meta, finalized, awarded_at)
                           VALUES(?,?,?,?,?,?,1,?)""",
                        (label, "best_duo", i, j1, val, meta1, now_iso))
            cur.execute("""INSERT INTO season_awards(season, category, place, jugador_id, value, meta, finalized, awarded_at)
                           VALUES(?,?,?,?,?,?,1,?)""",
                        (label, "best_duo", i, j2, val, meta2, now_iso))
    elif category == "most_improved":
        for i, r in enumerate(rows[:3], start=1):
            cur.execute("""INSERT INTO season_awards(season, category, place, jugador_id, value, meta, finalized, awarded_at)
                           VALUES(?,?,?,?,?,?,1,?)""",
                        (label, "most_improved", i, r["jugador_id"], float(r["delta"]), None, now_iso))
    elif category == "most_matches":
        for i, r in enumerate(rows[:3], start=1):
            cur.execute("""INSERT INTO season_awards(season, category, place, jugador_id, value, meta, finalized, awarded_at)
                           VALUES(?,?,?,?,?,?,1,?)""",
                        (label, "most_matches", i, r["jugador_id"], int(r["pj"]), None, now_iso))
    elif category == "best_points":
        for i, r in enumerate(rows[:3], start=1):
            val = float(r.get("puntos_pct") or 0.0)
            cur.execute("""INSERT INTO season_awards(season, category, place, jugador_id, value, meta, finalized, awarded_at)
                           VALUES(?,?,?,?,?,?,1,?)""",
                        (label, "best_points", i, r["jugador_id"], val, None, now_iso))

def _finalize(label, start_date, end_date):
    with _conn() as conn:
        cur = conn.cursor()
        now_iso = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

        mm = _rank_most_matches_range(cur, start_date, end_date, top=3)
        bp = _rank_best_points_range(cur, start_date, end_date, min_pj=15, top=3)
        mi = _rank_most_improved_range(cur, start_date, end_date, min_pj=15, top=3)
        bd = _rank_best_duo_range(cur, start_date, end_date, min_juntos=10, top=3)

        _persist_awards(cur, label, "most_matches", mm, now_iso)
        _persist_awards(cur, label, "best_points", bp, now_iso)
        _persist_awards(cur, label, "most_improved", mi, now_iso)
        _persist_awards(cur, label, "best_duo", bd, now_iso)

        cur.execute("""
          UPDATE seasons
             SET end_date = ?, finalized = 1
           WHERE label = ?
        """, (end_date, label))
        conn.commit()
    return {"most_matches": mm, "best_points": bp, "most_improved": mi, "best_duo": bd}

# ---------- UI ----------
def panel_temporadas():
    _ensure_tables()
    st.subheader("Temporadas (Admin)")

    # Crear / editar temporada
    st.markdown("### Crear / editar temporada activa")
    col1, col2, col3 = st.columns(3)
    with col1:
        label = st.text_input("Etiqueta de temporada", value="2026", help="Ej.: 2025, 2026, etc.")
    with col2:
        start_date = st.date_input("Inicio", value=date.today())
    with col3:
        st.text_input("Fin (se define al finalizar)", value="", disabled=True)

    c1, _ = st.columns(2)
    with c1:
        if st.button("Guardar/actualizar temporada"):
            with _conn() as conn:
                cur = conn.cursor()
                cur.execute("SELECT 1 FROM seasons WHERE label=?", (label,))
                exists = cur.fetchone() is not None
                if exists:
                    cur.execute("UPDATE seasons SET start_date=?, finalized=0 WHERE label=?",
                                (start_date.strftime("%Y-%m-%d"), label))
                else:
                    cur.execute("INSERT INTO seasons(label, start_date, finalized) VALUES(?, ?, 0)",
                                (label, start_date.strftime("%Y-%m-%d")))
                conn.commit()
            st.success(f"Temporada '{label}' guardada/actualizada.")

    st.markdown("---")

    # Listado
    st.markdown("### Temporadas")
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT label, start_date, IFNULL(end_date,'—') AS end_date, finalized FROM seasons ORDER BY date(start_date) DESC")
        rows = [dict(r) for r in cur.fetchall()]
    if rows:
        for r in rows:
            estado = "✅ Finalizada" if r["finalized"] else "🟡 Activa"
            st.write(f"- **{r['label']}** — {r['start_date']} → {r['end_date']} {estado}")
    else:
        st.info("No hay temporadas definidas aún.")

    st.markdown("---")

    # Finalizar temporada
    st.markdown("### Finalizar temporada")
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT label, start_date FROM seasons WHERE finalized=0 ORDER BY date(start_date) DESC")
        abiertas = [dict(r) for r in cur.fetchall()]

    if not abiertas:
        st.caption("No hay temporadas activas para finalizar.")
        st.divider()
        if st.button("⬅️ Volver al menú admin", key="admin_seasons_back_empty"):
            st.session_state.admin_page = None
            st.rerun()
        return

    sel = st.selectbox("Elegí temporada activa", [f"{r['label']} (desde {r['start_date']})" for r in abiertas])
    sel_label = sel.split(" (")[0]
    end_sel = st.date_input("Fecha de cierre (inclusive)", value=date.today())

    if st.button("Finalizar ahora (calcular podios y congelar medallas)"):
        with _conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT start_date FROM seasons WHERE label=?", (sel_label,))
            row = cur.fetchone()
            if not row:
                st.error("Temporada no encontrada.")
                return
            start = row["start_date"]

        pods = _finalize(sel_label, start, end_sel.strftime("%Y-%m-%d"))
        st.success(f"Temporada '{sel_label}' finalizada. Medallas congeladas.")
        with st.expander("Ver resumen de podios"):
            st.write("**Más partidos:**", pods["most_matches"])
            st.write("**Mejor rendimiento:**", pods["best_points"])
            st.write("**Mayor mejora ΔELO:**", pods["most_improved"])
            st.write("**Dupla del año:**", pods["best_duo"])

    st.divider()
    if st.button("⬅️ Volver al menú admin", key="admin_seasons_back"):
        st.session_state.admin_page = None
        st.rerun()