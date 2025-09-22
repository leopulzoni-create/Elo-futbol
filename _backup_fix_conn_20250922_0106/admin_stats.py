from db import get_connection
# admin_stats.py  (Py3.8-friendly)
# Panel de estadísticas globales para admin — versión visual

import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta
from typing import Optional, Tuple, List, Dict, Any
import math

import pandas as pd
import numpy as np
import streamlit as st

DB_NAME = "elo_futbol.db"

# -----------------------
# Conexión y utilidades
# -----------------------
def _conn():
    from db import get_connection as _gc
    return _gc()

    return c

def _read_df(sql: str, params: tuple = ()):
    with closing(_conn()) as conn:
        return pd.read_sql_query(sql, conn, params=params)

def _season_labels() -> List[str]:
    try:
        with closing(_conn()) as conn:
            cur = conn.cursor()
            cur.execute("SELECT label FROM seasons ORDER BY date(start_date) DESC")
            return [r[0] for r in cur.fetchall()]
    except Exception:
        return []

def _season_range(label: str) -> Optional[Tuple[str, str]]:
    try:
        with closing(_conn()) as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT start_date, COALESCE(end_date, ?) FROM seasons WHERE label=? LIMIT 1",
                (date.today().strftime("%Y-%m-%d"), label),
            )
            row = cur.fetchone()
            if not row:
                return None
            return (row[0], row[1])
    except Exception:
        return None

def _years_from_partidos() -> List[str]:
    try:
        with closing(_conn()) as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT DISTINCT substr(fecha,1,4) AS y
                FROM partidos
                WHERE fecha IS NOT NULL
                ORDER BY y DESC
            """)
            return [r[0] for r in cur.fetchall() if r and r[0]]
    except Exception:
        return []

def _season_clause(sel: Optional[str], alias: str = "p") -> Tuple[str, tuple]:
    # WHERE extra y params según temporada/año
    if not sel or sel == "Todas":
        return "", ()
    rng = _season_range(sel)
    if rng:
        return (f"AND date({alias}.fecha) BETWEEN date(?) AND date(?)", (rng[0], rng[1]))
    if len(sel) == 4 and sel.isdigit():
        return (f"AND strftime('%Y', {alias}.fecha) = ?", (sel,))
    return "", ()

def _result_cond(alias="p"):
    a = alias
    return f"(({a}.ganador IS NOT NULL) OR ({a}.diferencia_gol IS NOT NULL))"

# -----------------------
# Estilos reutilizables
# -----------------------
def _kpi_card(title: str, value_html: str, icon: str, tint_bg: str, tint_border: str):
    # Tarjeta grande tipo “jugador panel”
    st.markdown(
        f"""
        <div style="
          background: linear-gradient(180deg, #ffffff, #f8fafc);
          border: 1px solid {tint_border};
          border-radius: 18px;
          padding: 14px 16px;
          box-shadow: 0 1px 3px rgba(0,0,0,0.06);
          display:flex; align-items:center; gap:12px;
        ">
          <div style="
            width:36px; height:36px; border-radius:10px;
            background:{tint_bg}; display:flex; align-items:center; justify-content:center;
            font-size:20px;">
            {icon}
          </div>
          <div style="display:flex; flex-direction:column;">
            <div style="font-size:0.9rem; color:#64748b;">{title}</div>
            <div style="font-size:1.9rem; font-weight:800; color:#0f172a; line-height:1.1;">{value_html}</div>
          </div>
        </div>
        """, unsafe_allow_html=True
    )

def _mini_card(title: str, value_html: str, subtitle: str = ""):
    # Mini tarjeta para Paridad/Consistencia
    st.markdown(
        f"""
        <div style="
          background: linear-gradient(180deg, #ffffff, #f8fafc);
          border: 1px solid rgba(0,0,0,0.06);
          border-radius: 14px;
          padding: 12px 14px;
        ">
          <div style="font-size:0.9rem;color:#64748b;margin-bottom:6px;">{title}</div>
          <div style="font-size:1.6rem;font-weight:800;color:#0f172a;line-height:1;">{value_html}</div>
          <div style="font-size:0.8rem;color:#94a3b8;margin-top:6px;">{subtitle}</div>
        </div>
        """, unsafe_allow_html=True
    )

def _nowrap(text: str) -> str:
    # Garantiza que "5 partidos" no se corte en 2 líneas
    return f"<span style='white-space:nowrap;'>{text}</span>"

# -----------------------
# KPIs principales
# -----------------------
def _kpis_df(temporada_sel: Optional[str]) -> Dict[str, Any]:
    cond = _result_cond("p")
    where, params = _season_clause(temporada_sel, "p")
    sql = f"""
        SELECT p.id, p.fecha, p.ganador, p.diferencia_gol
        FROM partidos p
        WHERE {cond} {(' ' + where if where else '')}
    """
    df = _read_df(sql, params)
    jugados = int(len(df))

    # jugadores activos
    sql_j = f"""
        SELECT DISTINCT pj.jugador_id
        FROM partido_jugadores pj
        JOIN partidos p ON p.id = pj.partido_id
        WHERE {cond} {(' ' + where if where else '')}
    """
    dfj = _read_df(sql_j, params)
    activos = int(len(dfj))

    # % empates
    if df.empty:
        pct_emp = 0.0
    else:
        draws = df[(df["ganador"].isna()) & (df["diferencia_gol"].fillna(9999) == 0)]
        pct_emp = round(100.0 * len(draws) / jugados, 1)

    # ΔELO medio entre equipos
    sql_elo = f"""
        WITH sums AS (
          SELECT p.id AS partido_id,
                 pj.equipo,
                 SUM(he.elo_antes) AS elo_pre
          FROM partidos p
          JOIN partido_jugadores pj ON pj.partido_id = p.id
          JOIN historial_elo he ON he.partido_id = p.id AND he.jugador_id = pj.jugador_id
          WHERE {cond} {(' ' + where if where else '')}
          GROUP BY p.id, pj.equipo
        )
        SELECT partido_id,
               MAX(CASE WHEN equipo=1 THEN elo_pre END) AS e1,
               MAX(CASE WHEN equipo=2 THEN elo_pre END) AS e2
        FROM sums
        GROUP BY partido_id
    """
    dfe = _read_df(sql_elo, params)
    if not dfe.empty:
        dfe["abs_diff"] = (dfe["e1"] - dfe["e2"]).abs()
        delta_medio = float(dfe["abs_diff"].mean())
    else:
        delta_medio = float("nan")

    return {
        "jugados": jugados,
        "activos": activos,
        "pct_empates": pct_emp,
        "delta_elo_prom": None if math.isnan(delta_medio) else round(delta_medio, 1)
    }

# -----------------------
# Asistencia semanal
# -----------------------
_DIAS_ES = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]  # Python: 0..6

def _count_possible_weekdays(start_yyyy_mm_dd: str, end_yyyy_mm_dd: str) -> List[int]:
    d0 = datetime.strptime(start_yyyy_mm_dd, "%Y-%m-%d").date()
    d1 = datetime.strptime(end_yyyy_mm_dd, "%Y-%m-%d").date()
    if d1 < d0:
        d0, d1 = d1, d0
    cnt = [0]*7
    cur = d0
    step = timedelta(days=1)
    while cur <= d1:
        cnt[cur.weekday()] += 1
        cur += step
    return cnt

def _weekday_assistance(temporada_sel: Optional[str]) -> pd.DataFrame:
    # rango posibles
    rng = _season_range(temporada_sel) if (temporada_sel and temporada_sel != "Todas") else None
    if rng:
        start, end = rng
    else:
        if temporada_sel and len(temporada_sel) == 4 and temporada_sel.isdigit():
            start, end = temporada_sel + "-01-01", temporada_sel + "-12-31"
        else:
            with closing(_conn()) as conn:
                cur = conn.cursor()
                cur.execute("SELECT MIN(fecha), MAX(fecha) FROM partidos WHERE fecha IS NOT NULL")
                row = cur.fetchone()
                start = row[0] or date.today().strftime("%Y-%m-01")
                end = row[1] or date.today().strftime("%Y-%m-%d")

    posibles = _count_possible_weekdays(start, end)

    # jugados con resultado
    where_p, params_p = _season_clause(temporada_sel, "p")
    cond = _result_cond("p")
    sql_jug = f"""
        SELECT CAST(strftime('%w', p.fecha) AS INTEGER) AS wd_sql, COUNT(*) AS n
        FROM partidos p
        WHERE {cond} {(' ' + where_p if where_p else '')}
        GROUP BY wd_sql
    """
    df_jug = _read_df(sql_jug, params_p)
    jug_py = [0]*7
    for _, r in df_jug.iterrows():
        wd_sql = int(r["wd_sql"])  # 0=Dom .. 6=Sab
        wd_py = (wd_sql + 6) % 7   # -> 6=Dom .. 5=Sab (python 0=Lun)
        jug_py[wd_py] = int(r["n"])

    cobertura = [ (jug_py[i] / posibles[i] * 100.0 if posibles[i] > 0 else 0.0) for i in range(7) ]
    df = pd.DataFrame({
        "Día": _DIAS_ES,
        "Posibles": posibles,
        "Jugados": jug_py,
        "Cobertura %": [round(x,1) for x in cobertura],
    })
    return df

# -----------------------
# Rachas actuales (≥3)
# -----------------------
def _detalle_partidos_jugador(jid: int, temporada_sel: Optional[str]) -> pd.DataFrame:
    where, params = _season_clause(temporada_sel, "p")
    sql = f"""
      SELECT p.id AS partido_id, p.fecha, p.ganador, p.diferencia_gol, pj.equipo, pj.camiseta
      FROM partidos p
      JOIN partido_jugadores pj ON pj.partido_id = p.id
      WHERE pj.jugador_id = ? {(' ' + where if where else '')}
      ORDER BY date(p.fecha) ASC, p.id ASC
    """
    params2 = (jid,) + params
    return _read_df(sql, params2)

def _resultado_letra(row: pd.Series) -> Optional[str]:
    dif = row["diferencia_gol"]
    gan = row["ganador"]
    if pd.notna(dif) and int(dif) == 0:
        return "E"
    if pd.isna(gan) or pd.isna(row["equipo"]):
        return None
    return "G" if int(gan) == int(row["equipo"]) else "P"

def _racha_actual(vals: List[str], target: str) -> int:
    cnt = 0
    for v in reversed(vals):
        if v == target:
            cnt += 1
        else:
            break
    return cnt

def _streaks_current(temporada_sel: Optional[str], min_len: int = 3, months_for_shirt: int = 3):
    """
    Rachas actuales (≥min_len).
    - Victorias/Derrotas: consideran TODO el rango seleccionado (temporada/año/Todas).
    - Camisetas: solo últimos `months_for_shirt` meses (por defecto, 3).
    """
    with closing(_conn()) as conn:
        cur = conn.cursor()
        cur.execute("SELECT id, nombre FROM jugadores ORDER BY nombre ASC")
        jugadores = [dict(r) for r in cur.fetchall()]

    ganar, perder, camis = [], [], []

    # fecha de corte para camisetas (aprox 90 días)
    cutoff_dt = date.today() - timedelta(days=months_for_shirt * 30)
    cutoff_ts = pd.Timestamp(cutoff_dt.strftime("%Y-%m-%d"))

    for j in jugadores:
        df = _detalle_partidos_jugador(j["id"], temporada_sel)
        if df.empty:
            continue

        # --- rachas de W/L (consideran todo el rango de la selección) ---
        res = []
        for _, r in df.iterrows():
            res.append(_resultado_letra(r))
        res = [x for x in res if x in ("G", "P", "E")]  # ignora pendientes

        if res:
            rg = _racha_actual(res, "G")
            rp = _racha_actual(res, "P")
            if rg >= min_len:
                ganar.append({"jugador": j["nombre"], "racha": rg})
            if rp >= min_len:
                perder.append({"jugador": j["nombre"], "racha": rp})

        # --- rachas de camiseta (SOLO últimos 3 meses) ---
        # Parseamos fecha y filtramos a recent
        df = df.copy()
        df["_fecha_dt"] = pd.to_datetime(df["fecha"], errors="coerce")
        recent = df[df["_fecha_dt"] >= cutoff_ts]
        if recent.empty:
            continue

        cams = [str(x).strip().lower() if pd.notna(x) else None for x in recent["camiseta"].tolist()]
        cams = ["clara" if (c and c.startswith("clara")) else ("oscura" if (c and c.startswith("osc")) else None) for c in cams]

        if cams:
            last = None
            count = 0
            for c in reversed(cams):  # empezamos desde el partido más reciente
                if c and (last is None or c == last):
                    last = c
                    count += 1
                else:
                    break
            if last and count >= min_len:
                camis.append({"jugador": j["nombre"], "color": last, "racha": count})

    # ordenar por racha desc y luego nombre
    ganar.sort(key=lambda x: (-x["racha"], x["jugador"]))
    perder.sort(key=lambda x: (-x["racha"], x["jugador"]))
    camis.sort(key=lambda x: (-x["racha"], x["jugador"]))
    return ganar, perder, camis

def _render_streak_list(items: List[Dict[str, Any]], kind: str):
    if not items:
        st.caption("Sin rachas de 3+.")
        return
    for it in items:
        if kind == "win":
            st.markdown(f"- 🟢 **{it['jugador']}** — " + _nowrap(f"{it['racha']} seguidas"), unsafe_allow_html=True)
        elif kind == "loss":
            st.markdown(f"- 🔴 **{it['jugador']}** — " + _nowrap(f"{it['racha']} seguidas"), unsafe_allow_html=True)
        else:
            label = "Clara ⚪" if it["color"] == "clara" else "Oscura ⬛"
            st.markdown(
                f"- 👕 **{it['jugador']}** — {label} · " + _nowrap(f"{it['racha']} partidos"),
                unsafe_allow_html=True
            )

# -----------------------
# Paridad y checks ELO
# -----------------------
def _matches_with_team_elo(temporada_sel: Optional[str]) -> pd.DataFrame:
    cond = _result_cond("p")
    where, params = _season_clause(temporada_sel, "p")
    sql = f"""
      WITH t AS (
        SELECT p.id AS partido_id, p.fecha, p.ganador, p.diferencia_gol,
               pj.equipo, SUM(he.elo_antes) AS elo_pre
        FROM partidos p
        JOIN partido_jugadores pj ON pj.partido_id = p.id
        JOIN historial_elo he ON he.partido_id = p.id AND he.jugador_id = pj.jugador_id
        WHERE {cond} {(' ' + where if where else '')}
        GROUP BY p.id, pj.equipo
      )
      SELECT a.partido_id, a.fecha, a.elo_pre AS team1_elo, b.elo_pre AS team2_elo,
             p.ganador, p.diferencia_gol
      FROM t a
      JOIN t b ON b.partido_id = a.partido_id AND b.equipo <> 1
      JOIN partidos p ON p.id = a.partido_id
      WHERE a.equipo = 1
      ORDER BY date(p.fecha) ASC, p.id ASC
    """
    df = _read_df(sql, params)
    if df.empty:
        return df
    df["elo_diff"] = (df["team1_elo"] - df["team2_elo"]).astype(float)
    df["fav"] = np.where(df["elo_diff"] >= 0, 1, 2)            # favorito por ELO (suma pre)
    df["real"] = df["ganador"].fillna(0).astype(int)          # 0=empate
    mask_no_draw = df["real"].isin([1,2])
    df["fav_ok"] = np.where(mask_no_draw & ((df["fav"] == df["real"])), 1, 0)
    df["gd_abs"] = df["diferencia_gol"].fillna(0).abs()
    return df

def _elo_expected_metrics(df: pd.DataFrame) -> Dict[str, Any]:
    if df.empty:
        return {"accuracy": None, "avg_gd": None, "share_le2": None}
    mask_no_draw = df["real"].isin([1,2])
    acc = float(df.loc[mask_no_draw, "fav_ok"].mean()) if mask_no_draw.any() else float("nan")
    avg_gd = float(df["gd_abs"].mean())
    share_le2 = float((df["gd_abs"] <= 2).mean())  # partidos por 2 goles o menos
    res = {
        "accuracy": None if math.isnan(acc) else round(acc*100.0, 1),
        "avg_gd": round(avg_gd, 2),
        "share_le2": round(share_le2*100.0, 1),
    }
    return res

# -----------------------
# Sobre/Sub-rendimiento (S−E)
# -----------------------
def _player_overperf(temporada_sel: Optional[str], min_pj: int = 15) -> Tuple[pd.DataFrame, pd.DataFrame]:
    dfm = _matches_with_team_elo(temporada_sel)
    if dfm.empty:
        return pd.DataFrame(), pd.DataFrame()

    tmp_rows = []
    for _, r in dfm.iterrows():
        pid = int(r["partido_id"])
        d1 = float(r["team1_elo"] - r["team2_elo"])
        e1 = 1.0 / (1.0 + 10.0 ** (-d1/400.0))
        d2 = -d1
        e2 = 1.0 / (1.0 + 10.0 ** (-d2/400.0))
        real = int(r["ganador"]) if pd.notna(r["ganador"]) else 0
        is_draw = (pd.isna(r["ganador"]) and r["diferencia_gol"] == 0)
        s1 = 1.0 if real == 1 else (0.5 if is_draw else (0.0 if real == 2 else 0.0))
        s2 = 1.0 if real == 2 else (0.5 if is_draw else (0.0 if real == 1 else 0.0))
        tmp_rows.append((pid, 1, e1, s1))
        tmp_rows.append((pid, 2, e2, s2))
    exp_df = pd.DataFrame(tmp_rows, columns=["partido_id","equipo","E","S"])

    where, params = _season_clause(temporada_sel, "p")
    sql_pj = f"""
      SELECT pj.partido_id, pj.jugador_id, pj.equipo
      FROM partido_jugadores pj
      JOIN partidos p ON p.id = pj.partido_id
      WHERE 1=1 {(' ' + where if where else '')}
    """
    dfpj = _read_df(sql_pj, params)
    if dfpj.empty:
        return pd.DataFrame(), pd.DataFrame()

    merged = dfpj.merge(exp_df, on=["partido_id","equipo"], how="inner")
    ag = merged.groupby("jugador_id").agg(PJ=("partido_id","count"), E=("E","sum"), S=("S","sum")).reset_index()
    ag["S_minus_E"] = ag["S"] - ag["E"]

    dfn = _read_df("SELECT id AS jugador_id, nombre FROM jugadores", ())
    ag = ag.merge(dfn, on="jugador_id", how="left")

    elig = ag[ag["PJ"] >= int(min_pj)].copy()
    if elig.empty:
        return pd.DataFrame(), pd.DataFrame()

    top_pos = elig.sort_values(by=["S_minus_E","PJ","S"], ascending=[False, False, False]).head(10)
    top_neg = elig.sort_values(by=["S_minus_E","PJ","S"], ascending=[True, False, False]).head(10)
    for df_ in (top_pos, top_neg):
        df_["S_minus_E"] = df_["S_minus_E"].map(lambda x: f"{x:+.2f}")
        df_["E"] = df_["E"].map(lambda x: f"{x:.2f}")
        df_["S"] = df_["S"].map(lambda x: f"{x:.2f}")
    cols = ["nombre","PJ","S","E","S_minus_E"]
    return top_pos[cols].rename(columns={"nombre":"Jugador"}), top_neg[cols].rename(columns={"nombre":"Jugador"})

# -----------------------
# Resumen de jugadores
# -----------------------
def _resumen_jugadores(temporada_sel: Optional[str]) -> pd.DataFrame:
    cond = _result_cond("p")
    where, params = _season_clause(temporada_sel, "p")
    sql = f"""
      WITH base AS (
        SELECT pj.jugador_id,
               SUM(CASE WHEN p.ganador = pj.equipo THEN 1 ELSE 0 END) AS W,
               SUM(CASE WHEN p.ganador IS NULL AND IFNULL(p.diferencia_gol,0)=0 THEN 1 ELSE 0 END) AS E,
               SUM(CASE WHEN p.ganador IS NOT NULL AND p.ganador <> pj.equipo THEN 1 ELSE 0 END) AS L
        FROM partidos p
        JOIN partido_jugadores pj ON pj.partido_id = p.id
        WHERE {cond} {(' ' + where if where else '')}
        GROUP BY pj.jugador_id
      )
      SELECT j.nombre AS Jugador, (b.W+b.E+b.L) AS PJ, b.W, b.E, b.L,
             CASE WHEN (b.W+b.E+b.L)=0 THEN 0.0 ELSE ROUND(100.0 * b.W / (b.W+b.E+b.L), 1) END AS WR_pct,
             ROUND((3.0*b.W + 1.0*b.E), 1) AS Puntos_3_1_0
      FROM base b
      JOIN jugadores j ON j.id = b.jugador_id
      ORDER BY PJ DESC, j.nombre ASC
    """
    return _read_df(sql, params)

# -----------------------
# UI principal
# -----------------------
def panel_admin_stats():
    st.subheader("📊 Estadísticas globales")

    # selector de temporada/año
    labels = _season_labels()
    if labels:
        opts = labels + ["Todas"]
        default_idx = 0
    else:
        years = _years_from_partidos()
        if not years:
            years = [str(date.today().year)]
        opts = [years[0]] + ["Todas"]
        default_idx = 0

    temporada_sel = st.selectbox(
        "Temporada / Año",
        options=opts,
        index=default_idx,
        help="Si hay temporadas definidas se usan sus rangos; si no, por año. 'Todas' muestra todo."
    )

    # ============ KPIs (tarjetas grandes) ============
    k = _kpis_df(temporada_sel)
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        _kpi_card("Jugados", f"{k['jugados']}", "🥾", "#e0f2fe", "rgba(14,165,233,0.25)")
    with c2:
        _kpi_card("Jugadores activos", f"{k['activos']}", "✅", "#dcfce7", "rgba(16,185,129,0.25)")
    with c3:
        _kpi_card("Empates", f"{k['pct_empates']:.1f}%", "⚪", "#f1f5f9", "rgba(100,116,139,0.25)")
    with c4:
        _kpi_card("ΔELO equipos (prom.)", "—" if k["delta_elo_prom"] is None else str(int(round(k["delta_elo_prom"]))), "📈", "#ede9fe", "rgba(124,58,237,0.25)")

    st.markdown("---")

    # ============ Rachas (primero, visual) ============
    st.markdown("### 🔥 Rachas actuales (≥3)")
    r_g, r_p, r_c = _streaks_current(temporada_sel, min_len=3)
    colA, colB, colC = st.columns(3)
    with colA:
        st.write("**Victorias**")
        _render_streak_list(r_g, "win")
    with colB:
        st.write("**Derrotas**")
        _render_streak_list(r_p, "loss")
    with colC:
        st.write("**Camiseta**")
        _render_streak_list(r_c, "shirt")

    st.markdown("---")

    # ============ Asistencia semanal ============
    st.markdown("### 📆 Asistencia semanal")
    df_w = _weekday_assistance(temporada_sel)
    st.dataframe(df_w, use_container_width=True, hide_index=True)
    st.caption("Cobertura % = Jugados / Posibles (por día de la semana en el rango seleccionado).")

    st.markdown("---")

    # ============ Paridad y consistencia ELO ============
    st.markdown("### ⚖️ Paridad y consistencia ELO")
    dfm = _matches_with_team_elo(temporada_sel)
    if dfm.empty:
        st.info("No hay suficientes datos de ELO previo al partido para esta vista.")
    else:
        met = _elo_expected_metrics(dfm)
        ca, cb, cc = st.columns(3)
        with ca:
            _mini_card(
                "Efectividad del favorito",
                "—" if met["accuracy"] is None else f"{met['accuracy']}%",
                "Win rate del equipo con más ELO (pre)."
            )
        with cb:
            _mini_card(
                "Dif. gol promedio",
                f"{met['avg_gd']}",
                "Promedio de la diferencia absoluta de goles."
            )
        with cc:
            _mini_card(
                "Partidos por ≤2 goles",
                f"{met['share_le2']}%",
                "Proporción de partidos que terminaron por 1 o 2 goles (partidos parejos)."
            )

    st.markdown("---")

    # ============ Sobre/Sub-rendimiento ============
    st.markdown("### 📈 Sobre/Sub-rendimiento por jugador (S−E)")
    top_pos, top_neg = _player_overperf(temporada_sel, min_pj=15)
    col1, col2 = st.columns(2)
    with col1:
        st.write("**Top sobre-rendimiento** (≥15 PJ)")
        if not top_pos.empty:
            st.dataframe(top_pos, use_container_width=True, hide_index=True)
        else:
            st.caption("Sin datos suficientes.")
    with col2:
        st.write("**Top sub-rendimiento** (≥15 PJ)")
        if not top_neg.empty:
            st.dataframe(top_neg, use_container_width=True, hide_index=True)
        else:
            st.caption("Sin datos suficientes.")

    st.markdown("---")

    # ============ Resumen de jugadores ============
    st.markdown("### 👥 Resumen de jugadores")
    df_rj = _resumen_jugadores(temporada_sel)
    if df_rj.empty:
        st.caption("Sin datos.")
    else:
        st.dataframe(df_rj, use_container_width=True, hide_index=True)

    st.divider()
    if st.button("⬅️ Volver al menú admin", key="admin_stats_back"):
        st.session_state.admin_page = None
        st.rerun()

# Alias para compatibilidad con main.py
def panel_estadisticas_globales():
    return panel_admin_stats()