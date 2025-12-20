from db import get_connection
# jugador_stats.py
# Panel de estadísticas del jugador (por temporada):
# - Cabecera visual (opción A): 6 tarjetas + barra apilada 100% W/E/D.
#   * WinRate ahora es W/PJ (cuenta empates en el denominador).
# - Tendencia de desempeño (ELO difuso, sin mostrar valor exacto).
# - Comparativas: Rivales frecuentes (con "peor rival" por ventaja) y Compañeros frecuentes (mejor compañero ≥10).
# - Últimos 10 resultados con día e ícono.
# - Podios (por temporada específica): Más partidos, Mejor rendimiento 3/1/0 (min 15 PJ),
#   Mayor mejora ΔELO (min 15 PJ, se muestra +N pts), Dupla del año (≥10; medalla a ambos).
# - Colección de medallas: solo medallas obtenidas.
#   * Si hay season_awards finalizados: muestra medallas definitivas con año.
#   * Si no finalizada: muestra solo si estás en podio hoy (provisional, con año).
#
# Requisitos de DB existentes: partidos, partido_jugadores, jugadores, historial_elo.
# Tabla nueva (creación pasiva): season_awards.

from __future__ import annotations
import streamlit as st
import sqlite3
from datetime import date, datetime
import math
import json
import matplotlib.pyplot as plt

DB_NAME = "elo_futbol.db"

# ======================
# Conexión / helpers DB
# ======================
def _get_conn():
    from db import get_connection as _gc
    return _gc()

    return conn

def _ensure_awards_table():
    with _get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
        CREATE TABLE IF NOT EXISTS season_awards (
            season TEXT NOT NULL,
            category TEXT NOT NULL,     -- 'most_matches' | 'best_points' | 'most_improved' | 'best_duo'
            place INTEGER NOT NULL,     -- 1,2,3
            jugador_id INTEGER NOT NULL,
            value REAL,                 -- métrica interna (PJ, puntos%, ΔELO, etc.)
            meta TEXT,                  -- JSON opcional (p.ej. {"partner_id": 7})
            finalized INTEGER NOT NULL DEFAULT 0,
            awarded_at TEXT,
            PRIMARY KEY (season, category, place, jugador_id)
        )
        """)
        conn.commit()

# ======================
# Utilidades varias
# ======================
_DIAS_ES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]

def _weekday_es(yyyy_mm_dd: str) -> str:
    try:
        dt = datetime.strptime(yyyy_mm_dd, "%Y-%m-%d").date()
        return _DIAS_ES[dt.weekday()]
    except Exception:
        return ""

def _as_user_dict(user):
    try:
        if isinstance(user, dict):
            return user
        if hasattr(user, "keys"):
            return {k: user[k] for k in user.keys()}
        return dict(user)
    except Exception:
        return {"username": str(user), "rol": None, "jugador_id": None}

def _cancha_label(cancha_id: int | None) -> str:
    if cancha_id is None:
        return "Sin asignar"
    with _get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT nombre, direccion FROM canchas WHERE id = ?", (cancha_id,))
        row = cur.fetchone()
        if not row:
            return "Sin asignar"
        nombre = row["nombre"] or "Sin asignar"
        direccion = (row["direccion"] or "").strip()
        return f"{nombre} ({direccion})" if direccion else nombre

def _result_condition_sql(alias: str = "p") -> str:
    # Resultado válido: ganador no nulo, o empate (dif=0, ganador NULL)
    a = alias
    return f"(({a}.ganador IS NOT NULL) OR ({a}.ganador IS NULL AND IFNULL({a}.diferencia_gol,0)=0))"

def _get_season_range(label: str):
    try:
        with _get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT start_date, end_date, finalized FROM seasons WHERE label=? LIMIT 1", (label,))
            row = cur.fetchone()
            if not row:
                return None
            start = row["start_date"]
            end = row["end_date"] or date.today().strftime("%Y-%m-%d")
            return (start, end)
    except Exception:
        return None

def _season_clause_and_params(temporada: str | None, alias: str = "p"):
    # Si hay una temporada definida en 'seasons', usamos su rango;
    # si no, caemos al filtro por año calendario (compatibilidad).
    if temporada and temporada != "Todas":
        rng = _get_season_range(temporada)
        if rng:
            return f"AND date({alias}.fecha) BETWEEN date(?) AND date(?)", [rng[0], rng[1]]
        else:
            return f"AND strftime('%Y', {alias}.fecha) = ?", [temporada]
    return "", []


def _coarse_ticks(min_val: float, max_val: float, target_ticks: int = 3) -> list[int]:
    # Ticks gruesos para ocultar precisión del ELO (centenas, 3-4 marcas)
    if min_val == max_val:
        base = int(round(min_val / 100.0) * 100)
        return [base-100, base, base+100]
    lo = int(math.floor(min_val / 100.0) * 100)
    hi = int(math.ceil (max_val / 100.0) * 100)
    span = max(200, hi - lo)
    step = int(max(100, round(span / max(2, target_ticks-1) / 100.0) * 100))
    ticks = list(range(lo, hi + 1, step))
    if len(ticks) > 4:
        ticks = [ticks[0], ticks[len(ticks)//2], ticks[-1]]
    return ticks

# ======================
# Datos base por jugador
# ======================
def _years_for_player(jugador_id: int) -> list[str]:
    with _get_conn() as conn:
        cur = conn.cursor()
        cond = _result_condition_sql("p")
        cur.execute(
            f"""
            SELECT DISTINCT substr(p.fecha,1,4) AS y
            FROM partidos p
            JOIN partido_jugadores pj ON pj.partido_id = p.id
            WHERE pj.jugador_id = ? AND {cond}
            ORDER BY y DESC
            """, (jugador_id,)
        )
        ys = [r["y"] for r in cur.fetchall() if r["y"]]
        if ys:
            return ys
        cur.execute("SELECT DISTINCT substr(fecha,1,4) AS y FROM partidos ORDER BY y DESC")
        return [r["y"] for r in cur.fetchall() if r["y"]]

def _fetch_my_results(jugador_id: int, temporada: str | None):
    # Devuelve detalle (orden cronológico), secuencia G/E/P y totales W/D/L
    cond = _result_condition_sql("p")
    season_sql, season_params = _season_clause_and_params(temporada, "p")
    with _get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT p.id, p.fecha, p.cancha_id, p.ganador, p.diferencia_gol, mp.equipo AS mi_equipo
            FROM (SELECT pj.partido_id, pj.equipo
                  FROM partido_jugadores pj
                  JOIN partidos p2 ON p2.id = pj.partido_id
                  WHERE pj.jugador_id = ?) mp
            JOIN partidos p ON p.id = mp.partido_id
            WHERE {cond} {season_sql}
            ORDER BY datetime(p.fecha), p.id
            """, (jugador_id, *season_params)
        )
        rows = cur.fetchall()

    seq, detalle = [], []
    w = l = d = 0
    for r in rows:
        ganador, dif, mi = r["ganador"], r["diferencia_gol"], r["mi_equipo"]
        if ganador is None and (dif is None or int(dif) == 0):
            res = "E"; d += 1
        else:
            if ganador == mi:
                res = "G"; w += 1
            else:
                res = "P"; l += 1
        seq.append(res)
        detalle.append({
            "id": r["id"], "fecha": r["fecha"], "cancha_id": r["cancha_id"],
            "resultado": res, "dif": dif, "ganador": ganador
        })
    return detalle, seq, w, d, l

def _elo_series(jugador_id: int, temporada: str | None):
    season_sql, season_params = _season_clause_and_params(temporada, "p")
    with _get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT p.fecha AS fecha, COALESCE(h.elo_despues, h.elo_antes) AS elo
            FROM historial_elo h
            JOIN partidos p ON p.id = h.partido_id
            WHERE h.jugador_id = ? {season_sql}
            ORDER BY datetime(p.fecha), p.id
            """, (jugador_id, *season_params)
        )
        rows = cur.fetchall()
    xs = [r["fecha"] for r in rows if r["elo"] is not None]
    ys = [float(r["elo"]) for r in rows if r["elo"] is not None]
    return xs, ys

# ======================
# Comparativas (stats cruzadas)
# ======================
def _rivales_stats(jugador_id: int, temporada: str | None, limit: int = 5):
    """
    Top rivales y peor rival por VENTAJA que te lleva (yo_perdi - yo_gane), con mínimo 10 vs.
    Fallback: si nadie te supera, el rival que más veces te ganó.
    """
    cond_result = _result_condition_sql("p")
    season_sql, season_params = _season_clause_and_params(temporada, "p")
    with _get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            f"""
            WITH mis AS (
              SELECT p.id AS partido_id, p.ganador, mp.equipo AS mi_equipo
              FROM partidos p
              JOIN (
                SELECT pj.partido_id, pj.equipo
                FROM partido_jugadores pj
                JOIN partidos p2 ON p2.id = pj.partido_id
                WHERE pj.jugador_id = ?
              ) mp ON mp.partido_id = p.id
              WHERE {cond_result} {season_sql}
            ),
            riv AS (
              SELECT pj2.jugador_id AS rival_id,
                     COUNT(*) AS jugados_vs,
                     SUM(CASE WHEN m.ganador = m.mi_equipo THEN 1 ELSE 0 END) AS yo_gane,
                     SUM(CASE WHEN m.ganador IS NULL AND IFNULL(p.diferencia_gol,0)=0 THEN 1 ELSE 0 END) AS empates,
                     SUM(CASE WHEN m.ganador IS NOT NULL AND m.ganador <> m.mi_equipo THEN 1 ELSE 0 END) AS yo_perdi
              FROM mis m
              JOIN partido_jugadores pj2 ON pj2.partido_id = m.partido_id
              JOIN partidos p ON p.id = m.partido_id
              WHERE pj2.jugador_id <> ?
                AND pj2.equipo IS NOT NULL
                AND pj2.equipo <> m.mi_equipo
              GROUP BY pj2.jugador_id
            )
            SELECT r.*, j.nombre
            FROM riv r
            JOIN jugadores j ON j.id = r.rival_id
            ORDER BY jugados_vs DESC, yo_perdi DESC, j.nombre ASC
            """,
            (jugador_id, *season_params, jugador_id),
        )
        rows = [dict(r) for r in cur.fetchall()]

    top = rows[:limit]
    cand_pos = [
        r for r in rows
        if (r.get("jugados_vs") or 0) >= 10 and (r.get("yo_perdi") or 0) > (r.get("yo_gane") or 0)
    ]
    peor = None
    if cand_pos:
        peor = max(
            cand_pos,
            key=lambda r: ((r["yo_perdi"] - r["yo_gane"]), r["yo_perdi"], r["jugados_vs"])
        )
    alternativo = max(rows, key=lambda r: (r["yo_perdi"], r["jugados_vs"])) if rows else None
    return top, peor, alternativo

def _companeros_stats(jugador_id: int, temporada: str | None, limit: int = 5):
    cond_result = _result_condition_sql("p")
    season_sql, season_params = _season_clause_and_params(temporada, "p")
    with _get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            f"""
            WITH mis AS (
              SELECT p.id AS partido_id, p.ganador, mp.equipo AS mi_equipo
              FROM partidos p
              JOIN (
                SELECT pj.partido_id, pj.equipo
                FROM partido_jugadores pj
                JOIN partidos p2 ON p2.id = pj.partido_id
                WHERE pj.jugador_id = ?
              ) mp ON mp.partido_id = p.id
              WHERE {cond_result} {season_sql}
            ),
            comp AS (
              SELECT pj2.jugador_id AS comp_id,
                     COUNT(*) AS jugados_juntos,
                     SUM(CASE WHEN p.ganador = m.mi_equipo THEN 1 ELSE 0 END) AS ganados_juntos
              FROM mis m
              JOIN partido_jugadores pj2 ON pj2.partido_id = m.partido_id
              JOIN partidos p ON p.id = m.partido_id
              WHERE pj2.jugador_id <> ?
                AND pj2.equipo = m.mi_equipo
              GROUP BY pj2.jugador_id
            )
            SELECT c.*, j.nombre,
                   (1.0 * ganados_juntos) / NULLIF(jugados_juntos, 0) AS wr_juntos
            FROM comp c
            JOIN jugadores j ON j.id = c.comp_id
            ORDER BY jugados_juntos DESC, wr_juntos DESC, j.nombre ASC
            """,
            (jugador_id, *season_params, jugador_id),
        )
        rows = [dict(r) for r in cur.fetchall()]

    top = rows[:limit]
    # mejor compañero: mínimo 10 juntos
    candidatos = [r for r in rows if r["jugados_juntos"] >= 10]
    mejor = None
    if candidatos:
        mejor = max(
            candidatos,
            key=lambda r: (r["wr_juntos"] or 0.0, r["ganados_juntos"])
        )
    return top, mejor

# ======================
# Duplas (para podio y colección)
# ======================
def _rank_best_duo(temporada: str | None, min_juntos: int = 10, top: int = 3):
    # Duplas del mismo equipo por partido; rendimiento 3/1/0.
    cond = _result_condition_sql("p")
    season_sql, season_params = _season_clause_and_params(temporada, "p")
    with _get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            f"""
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
              WHERE {cond} {season_sql}
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
            """, (*season_params, min_juntos)
        )
        return [dict(r) for r in cur.fetchall()]

def _best_duo_for_player(jugador_id: int, temporada: str | None, min_juntos: int = 2):
    # Mejor dupla (por rendimiento 3/1/0) que incluya al jugador.
    cond = _result_condition_sql("p")
    season_sql, season_params = _season_clause_and_params(temporada, "p")
    with _get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            f"""
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
              WHERE {cond} {season_sql}
            ),
            mine AS (
              SELECT * FROM pairs WHERE j1 = ? OR j2 = ?
            ),
            agg AS (
              SELECT j1, j2,
                     COUNT(*) AS pj,
                     SUM(CASE WHEN ganador = equipo THEN 1 ELSE 0 END) AS w,
                     SUM(CASE WHEN ganador IS NULL AND IFNULL(diferencia_gol,0)=0 THEN 1 ELSE 0 END) AS e,
                     SUM(CASE WHEN ganador IS NOT NULL AND ganador <> equipo THEN 1 ELSE 0 END) AS l
              FROM mine
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
            LIMIT 1
            """, (*season_params, jugador_id, jugador_id, min_juntos)
        )
        row = cur.fetchone()
        return dict(row) if row else None

# ======================
# Podios
# ======================
def _rank_most_matches(temporada: str | None, top: int = 3):
    cond = _result_condition_sql("p")
    season_sql, season_params = _season_clause_and_params(temporada, "p")
    with _get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT pj.jugador_id, j.nombre, COUNT(*) AS pj
            FROM partido_jugadores pj
            JOIN partidos p ON p.id = pj.partido_id
            JOIN jugadores j ON j.id = pj.jugador_id
            WHERE {cond} {season_sql}
            GROUP BY pj.jugador_id
            ORDER BY pj DESC, j.nombre ASC
            LIMIT {top}
            """, (*season_params,)
        )
        return [dict(r) for r in cur.fetchall()]

def _rank_best_points(temporada: str | None, min_pj: int = 15, top: int = 3):
    cond = _result_condition_sql("p")
    season_sql, season_params = _season_clause_and_params(temporada, "p")
    with _get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            f"""
            WITH base AS (
              SELECT pj.jugador_id,
                     SUM(CASE WHEN p.ganador = pj.equipo THEN 1 ELSE 0 END) AS w,
                     SUM(CASE WHEN p.ganador IS NULL AND IFNULL(p.diferencia_gol,0)=0 THEN 1 ELSE 0 END) AS e,
                     SUM(CASE WHEN p.ganador IS NOT NULL AND p.ganador <> pj.equipo THEN 1 ELSE 0 END) AS l
              FROM partido_jugadores pj
              JOIN partidos p ON p.id = pj.partido_id
              WHERE {cond} {season_sql}
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
            """, (*season_params, min_pj)
        )
        return [dict(r) for r in cur.fetchall()]

def _rank_most_improved(temporada: str | None, min_pj: int = 15, top: int = 3):
    # ΔELO = ELO_último - ELO_primero en la temporada; requiere historial_elo.
    season_sql, season_params = _season_clause_and_params(temporada, "p")
    cond = _result_condition_sql("p")
    with _get_conn() as conn:
        cur = conn.cursor()
        # candidatos por PJ
        cur.execute(
            f"""
            SELECT pj.jugador_id, COUNT(*) AS pj
            FROM partido_jugadores pj
            JOIN partidos p ON p.id = pj.partido_id
            WHERE {cond} {season_sql}
            GROUP BY pj.jugador_id
            HAVING COUNT(*) >= ?
            """, (*season_params, min_pj)
        )
        candidatos = [r["jugador_id"] for r in cur.fetchall()]

        results = []
        for jid in candidatos:
            cur.execute(
                f"""
                SELECT p.fecha, h.elo_antes, h.elo_despues
                FROM historial_elo h
                JOIN partidos p ON p.id = h.partido_id
                WHERE h.jugador_id = ? {season_sql}
                ORDER BY datetime(p.fecha), p.id
                """, (jid, *season_params)
            )
            rows = cur.fetchall()
            if not rows:
                continue
            start = rows[0]["elo_antes"] if rows[0]["elo_antes"] is not None else rows[0]["elo_despues"]
            end   = rows[-1]["elo_despues"] if rows[-1]["elo_despues"] is not None else rows[-1]["elo_antes"]
            if start is None or end is None:
                continue
            delta = float(end) - float(start)
            cur.execute("SELECT nombre FROM jugadores WHERE id = ?", (jid,))
            nombre = (cur.fetchone() or {"nombre":"?"})["nombre"]
            results.append({"jugador_id": jid, "nombre": nombre, "delta": delta})

        results.sort(key=lambda x: (x["delta"], x["nombre"]), reverse=True)
        return results[:top]

# ======================
# Cabecera visual (opción A)
# ======================
def _render_header_cards_and_bar(w: int, e: int, l: int):
    pj = w + e + l
    wr = (w / pj * 100.0) if pj > 0 else 0.0              # WinRate (cuenta empates en denominador)
    puntos_pct = ((3*w + 1*e) / (3*pj) * 100.0) if pj > 0 else 0.0  # Rendimiento 3/1/0

    cards = [
        ("👟", "Jugados", f"{pj}", "#0ea5e9", "#f0f9ff"),
        ("✅", "Victorias", f"{w}", "#10b981", "#ecfdf5"),
        ("⚪", "Empates", f"{e}", "#9ca3af", "#f5f6f7"),
        ("❌", "Derrotas", f"{l}", "#ef4444", "#fef2f2"),
        ("🎯", "Rend. 3/1/0", f"{puntos_pct:.0f}%", "#7c3aed", "#f5f3ff"),
        ("🧮", "WinRate", f"{wr:.0f}%", "#0ea5e9", "#eff6ff"),
    ]
    c = st.columns(6)
    for i, (icon, title, value, fg, bg) in enumerate(cards):
        with c[i]:
            st.markdown(
                f"""
                <div style="background:{bg};border:1px solid rgba(0,0,0,0.06);border-radius:14px;padding:12px 14px">
                  <div style="font-size:20px">{icon}</div>
                  <div style="font-size:12px;color:#6b7280;margin-top:2px">{title}</div>
                  <div style="font-size:24px;font-weight:800;color:{fg}">{value}</div>
                </div>
                """, unsafe_allow_html=True
            )

    if pj > 0:
        w_pct = int(round(100 * w / pj))
        e_pct = int(round(100 * e / pj))
        l_pct = max(0, 100 - w_pct - e_pct)
        st.markdown(
            f"""
            <div style="margin-top:10px;height:16px;border-radius:999px;overflow:hidden;border:1px solid rgba(0,0,0,0.08)">
              <div style="width:{w_pct}%;height:100%;background:#10b981;float:left"></div>
              <div style="width:{e_pct}%;height:100%;background:#9ca3af;float:left"></div>
              <div style="width:{l_pct}%;height:100%;background:#ef4444;float:left"></div>
            </div>
            <div style="display:flex;gap:12px;margin-top:6px;color:#6b7280;font-size:12px">
              <span>✅ {w_pct}%</span><span>⚪ {e_pct}%</span><span>❌ {l_pct}%</span>
            </div>
            """, unsafe_allow_html=True
        )

# ======================
# UI principal
# ======================
def panel_mis_estadisticas(user):
    _ensure_awards_table()

    user = _as_user_dict(user)
    jugador_id = user.get("jugador_id")
    if not jugador_id:
        st.subheader("Mis estadísticas")
        st.warning("Tu usuario no está vinculado a ningún jugador. Pedile al admin que te vincule para ver tus estadísticas.")
        if st.button("⬅️ Volver", key="stats_back_no_j"):
            st.session_state["jugador_page"] = "menu"
            st.rerun()
        return

    # Selector de temporada
    def _season_labels():
        try:
            with _get_conn() as conn:
                cur = conn.cursor()
                cur.execute("SELECT label FROM seasons ORDER BY date(start_date) DESC")
                return [r["label"] for r in cur.fetchall()]
        except Exception:
            return []

    # ...
    labels = _season_labels()
    if labels:
        opts = labels + ["Todas"]
        default_index = 0
    else:
        years = _years_for_player(jugador_id)
        default_year = years[0] if years else str(date.today().year)
        opts = ([default_year] if default_year else []) + ["Todas"]
        opts = list(dict.fromkeys(opts))
        default_index = 0

    st.markdown("### Temporada")
    temporada = st.selectbox(
        "Elegí el año (o Todas):",
        options=opts, index=default_index, key=f"stats_temporada_sel_{jugador_id}",
        help="Las estadísticas y medallas se calculan para la temporada seleccionada."
    )

    st.markdown("---")

    # Datos base
    detalle, seq, w, e, l = _fetch_my_results(jugador_id, temporada)

    # Cabecera visual
    _render_header_cards_and_bar(w, e, l)

    # Tendencia
    st.write("")
    st.write("### Tendencia de desempeño")
    xs, ys = _elo_series(jugador_id, temporada)
    if xs and ys:
        fig = plt.figure()
        plt.plot(xs, ys)
        ax = plt.gca()
        y_ticks = _coarse_ticks(min(ys), max(ys), target_ticks=3)
        ax.set_yticks(y_ticks)
        ax.set_ylabel("Escala de progreso en ELO")
        n = len(xs)
        if n > 10:
            step = max(1, n // 8)
            idx = list(range(0, n, step))
            if idx[-1] != n - 1:
                idx.append(n - 1)
            ax.set_xticks(idx)
            ax.set_xticklabels([xs[i] for i in idx], rotation=45, ha="right")
        else:
            plt.xticks(rotation=45, ha="right")
        ax.set_xlabel("Fecha")
        ax.set_title("Evolución en el tiempo")
        plt.tight_layout()
        st.pyplot(fig, width='stretch')
    else:
        st.info("Aún no hay historial suficiente para graficar la tendencia en esta temporada.")

    st.markdown("---")

    # ---------- Comparativas ----------
    st.write("### Comparativas")
    c1, c2 = st.columns(2)

    # Rivales
    top_riv, peor_rival, alt_rival = _rivales_stats(jugador_id, temporada, limit=5)
    with c1:
        st.markdown("#### Rivales frecuentes")
        if not top_riv:
            st.caption("Sin datos suficientes en esta temporada.")
        else:
            for rrow in top_riv:
                jug = rrow["jugados_vs"]; yo_w = rrow["yo_gane"]; yo_l = rrow["yo_perdi"]; emp = rrow["empates"]
                st.write(f"- **{rrow['nombre']}** — vs: {jug} • balance: {yo_w}-{emp}-{yo_l}")

        if peor_rival:
            diff = int(peor_rival["yo_perdi"] - peor_rival["yo_gane"])
            st.warning(
                f"Peor rival (≥10 vs): **{peor_rival['nombre']}** — te lleva **{diff}** partido(s) "
                f"(te ganó {peor_rival['yo_perdi']}, vos le ganaste {peor_rival['yo_gane']})."
            )
        elif alt_rival:
            st.info(
                f"Nadie te supera (≥10 vs). El rival que más veces te ganó es **{alt_rival['nombre']}** "
                f"({alt_rival['yo_perdi']})."
            )

    # Compañeros
    top_comp, mejor_comp = _companeros_stats(jugador_id, temporada, limit=5)
    with c2:
        st.markdown("#### Compañeros frecuentes")
        if not top_comp:
            st.caption("Sin datos suficientes en esta temporada.")
        else:
            for crow in top_comp:
                jug = crow["jugados_juntos"]; gw = crow["ganados_juntos"]; wrj = (crow["wr_juntos"] or 0.0) * 100.0
                st.write(f"- **{crow['nombre']}** — juntos: {jug} • ganados: {gw} • WR: {wrj:.0f}%")
        if mejor_comp:
            wr = (mejor_comp["wr_juntos"] or 0.0) * 100.0
            st.success(f"Mejor compañero (≥10 juntos): **{mejor_comp['nombre']}** — WR juntos {wr:.0f}% ({mejor_comp['ganados_juntos']} ganados).")

    st.markdown("---")

    # Últimos 10
    st.write("### Últimos 10 resultados")
    if detalle:
        ult = detalle[-10:]
        icon_map = {"G": "✅", "E": "⚪", "P": "❌"}
        st.write(" ".join(icon_map[x["resultado"]] for x in ult))
        with st.expander("Ver detalle"):
            for rrow in reversed(ult):
                fecha = rrow["fecha"]
                dia = _weekday_es(fecha)
                cancha = _cancha_label(rrow["cancha_id"])
                res = rrow["resultado"]; icon = icon_map[res]
                txt = {"G":"Ganado","E":"Empatado","P":"Perdido"}[res]
                suf = f" (dif: {rrow['dif']})" if res != "E" and rrow.get("dif") is not None else ""
                st.write(f"- {fecha} ({dia}) • {cancha} — **{txt}**{suf} {icon}")
    else:
        st.caption("No hay partidos con resultado en esta temporada.")

    st.markdown("---")

    # ======================
    # Podios de temporada (globales)
    # ======================
    if temporada == "Todas":
        st.write("### 🏆 Podios de temporada")
        st.caption("Seleccioná una temporada específica para ver los podios.")
    else:
        st.write("### 🏆 Podios de temporada")
        pods = {
            "most_matches": _rank_most_matches(temporada, top=3),
            "best_points": _rank_best_points(temporada, min_pj=15, top=3),
            "most_improved": _rank_most_improved(temporada, min_pj=15, top=3),
            "best_duo": _rank_best_duo(temporada, min_juntos=10, top=3),
        }

        def _render_podium(title, rows, formatter):
            st.markdown(f"#### {title}")
            if not rows:
                st.caption("Sin datos suficientes en esta temporada.")
                return
            medals = ["🥇","🥈","🥉"]
            for i, r in enumerate(rows[:3]):
                st.write(f"{medals[i]} {formatter(r)}")

        c1, c2 = st.columns(2)

        with c1:
            _render_podium(
                "Más partidos (PJ)",
                pods["most_matches"],
                lambda r: f"{r['nombre']} — {int(r['pj'])} PJ"
            )
            _render_podium(
                "Mayor mejora ΔELO",
                pods["most_improved"],
                lambda r: f"{r['nombre']} — +{int(round(r['delta']))} pts"
            )
        with c2:
            _render_podium(
                "Mejor rendimiento 3/1/0",
                pods["best_points"],
                lambda r: f"{r['nombre']} — {int(round((r['puntos_pct'] or 0)*100))}% ({int(r['pj'])} PJ)"
            )
            _render_podium(
                "Dupla del año",
                pods["best_duo"],
                lambda r: f"{r['nombre1']} + {r['nombre2']} — {int(round((r['puntos_pct'] or 0)*100))}% ({int(r['pj'])} juntos)"
            )

        st.markdown("---")

    # ======================
    # Mi colección de medallas (solo obtenidas)
    # ======================
    st.write("### 🥇 Mi colección de medallas")

    # Helper para ícono según puesto
    def _medal_icon(place: int) -> str:
        return "🥇" if place == 1 else ("🥈" if place == 2 else "🥉")

    # Map labels
    CAT_LABEL = {
        "most_matches": "Más partidos",
        "best_points": "Mejor rendimiento 3/1/0",
        "most_improved": "Mayor mejora ΔELO",
        "best_duo": "Dupla del año",
    }

    # Mostrar medallas finalizadas si existen:
    with _get_conn() as conn:
        cur = conn.cursor()
        if temporada == "Todas":
            # Todas las medallas finalizadas del jugador, en cualquier año
            cur.execute("""
                SELECT season, category, place, jugador_id, value, meta
                FROM season_awards
                WHERE finalized = 1 AND jugador_id = ?
                ORDER BY season DESC, category ASC, place ASC
            """, (jugador_id,))
            finals = [dict(r) for r in cur.fetchall()]
        else:
            # Solo esta temporada
            cur.execute("""
                SELECT season, category, place, jugador_id, value, meta
                FROM season_awards
                WHERE finalized = 1 AND season = ? AND jugador_id = ?
                ORDER BY category ASC, place ASC
            """, (temporada, jugador_id))
            finals = [dict(r) for r in cur.fetchall()]

    showed_any = False
    if finals:
        for r in finals:
            cat = r["category"]; season_val = r["season"]; place = int(r["place"])
            label = CAT_LABEL.get(cat, cat)
            extra = ""
            # Para dupla finalizada, intentar mostrar partner (si fue guardado en meta)
            if cat == "best_duo" and r.get("meta"):
                try:
                    meta = json.loads(r["meta"])
                    pid = meta.get("partner_id")
                    if pid:
                        with _get_conn() as conn:
                            c2 = conn.cursor()
                            c2.execute("SELECT nombre FROM jugadores WHERE id=?", (pid,))
                            nr = c2.fetchone()
                            if nr:
                                extra = f" — con {nr['nombre']}"
                except Exception:
                    pass
            st.success(f"{_medal_icon(place)} {label} ({season_val}){extra}")
            showed_any = True

    # Si no hay finalizadas (o si no hay ninguna para esta temporada), mostrar provisionales SOLO SI está en podio hoy
    if not showed_any:
        if temporada == "Todas":
            st.caption("No tenés medallas finalizadas aún (mostraría todas tus medallas históricas aquí).")
        else:
            # calcular standings en vivo y ver si está en top3 de algo
            pods_live = {
                "most_matches": _rank_most_matches(temporada, top=3),
                "best_points": _rank_best_points(temporada, min_pj=15, top=3),
                "most_improved": _rank_most_improved(temporada, min_pj=15, top=3),
                "best_duo": _rank_best_duo(temporada, min_juntos=10, top=3),
            }
            provisional_any = False
            # categorías individuales
            for cat in ["most_matches", "best_points", "most_improved"]:
                rows = pods_live[cat]
                pos = next((i+1 for i, r in enumerate(rows) if r["jugador_id"] == jugador_id), None)
                if pos:
                    st.info(f"{_medal_icon(pos)} {CAT_LABEL[cat]} (provisional, {temporada})")
                    provisional_any = True
            # dupla del año
            rows = pods_live["best_duo"]
            pos = None; partner_name = None
            for i, r in enumerate(rows):
                if jugador_id in (r["j1"], r["j2"]):
                    pos = i+1
                    partner_name = r["nombre2"] if r["j1"] == jugador_id else r["nombre1"]
                    break
            if pos:
                st.info(f"{_medal_icon(pos)} {CAT_LABEL['best_duo']} (provisional, {temporada}) — con {partner_name}")
                provisional_any = True

            if not provisional_any:
                st.caption("Aún no estás en podio en esta temporada.")

    st.divider()
    if st.button("⬅️ Volver", key=f"stats_back_{jugador_id}"):
        st.session_state["jugador_page"] = "menu"
        st.rerun()