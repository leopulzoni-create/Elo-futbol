# historial.py (Calendario mejorado 2×N + Historial ELO)
from db import get_connection
from pathlib import Path
from typing import Optional
import calendar
from datetime import datetime, date
import pandas as pd
import streamlit as st

# =========================
# Config & DB helpers
# =========================
DB_PATH = Path(__file__).with_name("elo_futbol.db")

def get_conn():
    # Puente único hacia el adaptador central (SQLite local o Turso)
    from db import get_connection as _gc
    return _gc()

def read_sql_df(query: str, params: tuple = ()):
    """
    Lee filas con el cursor y construye un DataFrame, autocasteando columnas
    mayormente numéricas a float/int para evitar errores al operar con pandas.
    """
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(query, params)
        rows = cur.fetchall()

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)

    # --- Autocast numérico en columnas mayormente numéricas ---
    def _mostly_numeric(s: pd.Series, thresh: float = 0.7) -> bool:
        nn = s.dropna()
        if len(nn) == 0:
            return False
        ok = 0
        for v in nn:
            try:
                float(str(v).replace(",", "."))
                ok += 1
            except Exception:
                pass
        return ok / len(nn) >= thresh

    for c in df.columns:
        if df[c].dtype == object and _mostly_numeric(df[c]):
            df[c] = pd.to_numeric(df[c].astype(str).str.replace(",", ".", regex=False), errors="coerce")

    return df

# =========================
# SQL base
# =========================
SQL_JUGADORES_DE_PARTIDO = """
SELECT pj.partido_id, pj.equipo, pj.camiseta, j.id AS jugador_id, j.nombre AS jugador_nombre
FROM partido_jugadores pj
JOIN jugadores j ON j.id = pj.jugador_id
WHERE pj.partido_id = ?
ORDER BY pj.equipo ASC, j.nombre ASC;
"""

SQL_HISTORIAL_ELO_BASE = """
SELECT
  he.id               AS historial_id,
  he.fecha            AS fecha,
  he.jugador_id       AS jugador_id,
  j.nombre            AS jugador_nombre,
  he.partido_id       AS partido_id,
  he.elo_antes        AS elo_antes,
  he.elo_despues      AS elo_despues
FROM historial_elo he
JOIN jugadores j ON j.id = he.jugador_id
"""

# =========================
# UI utils (badges + helpers)
# =========================
def _badge(texto: str, background: str, color: str = "white"):
    st.markdown(
        """
        <span style="
            display:inline-block;
            padding:2px 8px;
            border-radius:999px;
            background:%s;
            color:%s;
            font-size:0.8rem;
            margin-right:6px;">
            %s
        </span>
        """ % (background, color, texto),
        unsafe_allow_html=True,
    )

def _camiseta_emoji(camiseta: Optional[str]) -> str:
    if not camiseta:
        return "👕"
    c = str(camiseta).strip().lower()
    if c.startswith("clara"): return "⚪"
    if c.startswith("osc"):   return "⬛"
    return "👕"

def _equipo_label(n: int) -> str:
    return "Equipo 1" if int(n) == 1 else "Equipo 2"

def _ganador_texto_simple(g):
    if g is None:
        return "—"
    try:
        gi = int(g)
    except Exception:
        return str(g)
    return {1: "Ganó Equipo 1", 2: "Ganó Equipo 2", 0: "Empate"}.get(gi, str(g))

def _oficial_texto(es_oficial):
    return "Oficial" if es_oficial else "Amistoso"

def _oficial_color(es_oficial):
    return "#2563eb" if es_oficial else "#64748b"

def _delta_str(antes, despues):
    try:
        d = float(despues) - float(antes)
    except Exception:
        return ""
    signo = "+" if d >= 0 else ""
    return "%s%.1f" % (signo, d)

# =========================
# Calendario helpers + estilos
# =========================
MESES_NOMBRES = ["Ene","Feb","Mar","Abr","May","Jun","Jul","Ago","Sep","Oct","Nov","Dic"]
DIAS_HDR = ["Lu","Ma","Mi","Ju","Vi","Sá","Do"]  # una sola línea

# Estilos: TODOS los días (y huecos) se renderizan con el MISMO alto/ancho
_CALENDAR_CSS = """
<style>
.cal-wrap .stButton > button{
  width:100%;
  height:40px;             /* alto fijo */
  padding:0;
  border-radius:10px;
  white-space:nowrap;      /* evita 2 líneas */
  display:flex;            /* centro perfecto */
  align-items:center;
  justify-content:center;
  gap:6px;                 /* espacio entre número y círculo */
  line-height:1;           /* evita desplazamientos verticales */
  font-variant-numeric: tabular-nums;  /* números de ancho fijo */
}
.cal-wrap .day-empty .stButton > button{ opacity:0.35; }
.cal-wrap .month-box{ border:1px solid rgba(255,255,255,0.12); border-radius:12px; padding:10px 12px; margin-bottom:12px; }
.cal-wrap .month-title{ margin:0 0 8px 0; }
.cal-wrap .weekday{ text-align:center; font-weight:600; margin-bottom:6px; }
</style>
"""

def _years_available():
    df = read_sql_df("""
        SELECT DISTINCT SUBSTR(fecha,1,4) AS anio
        FROM partidos
        WHERE fecha IS NOT NULL AND TRIM(fecha)!=''
        ORDER BY anio DESC
    """)
    if df.empty:
        return [str(datetime.now().year)]
    return df["anio"].astype(str).tolist()

def _days_with_match(year: int, month: int):
    """
    Devuelve set de días (int) con al menos un partido:
    - con resultado (ganador o diferencia_gol no nulos)
    - con jugadores asignados
    - con fecha <= hoy
    """
    today_iso = date.today().isoformat()  # 'YYYY-MM-DD'
    df = read_sql_df("""
        SELECT DISTINCT CAST(SUBSTR(p.fecha, 9, 2) AS INT) AS dia
          FROM partidos p
         WHERE SUBSTR(p.fecha,1,4) = ?
           AND CAST(SUBSTR(p.fecha,6,2) AS INT) = ?
           AND SUBSTR(p.fecha,1,10) <= ?
           AND (p.ganador IS NOT NULL OR p.diferencia_gol IS NOT NULL)
           AND EXISTS (
                 SELECT 1 FROM partido_jugadores pj
                  WHERE pj.partido_id = p.id
           )
    """, (str(year), month, today_iso))

    if df.empty or "dia" not in df.columns:
        return set()

    dias = pd.to_numeric(df["dia"], errors="coerce")
    return set(dias.dropna().astype(int).tolist())

def _partidos_by_date(date_iso: str):
    """
    Lista los partidos jugados de una fecha específica:
    - con resultado
    - con jugadores asignados (al menos uno)
    - fecha exacta = date_iso
    """
    return read_sql_df("""
        SELECT p.id AS partido_id,
               p.fecha,
               COALESCE(c.nombre,'—') AS cancha,
               p.ganador,
               p.diferencia_gol,
               p.es_oficial
          FROM partidos p
     LEFT JOIN canchas c ON c.id = p.cancha_id
         WHERE SUBSTR(p.fecha,1,10) = ?
           AND (p.ganador IS NOT NULL OR p.diferencia_gol IS NOT NULL)
           AND EXISTS (
                 SELECT 1 FROM partido_jugadores pj
                  WHERE pj.partido_id = p.id
           )
      ORDER BY p.id ASC
    """, (date_iso,))

def _render_partidos_detail_for_day(date_iso: str):
    df = _partidos_by_date(date_iso)
    if df.empty:
        st.info("No se encontraron partidos para esta fecha.")
        return
    for _, row in df.iterrows():
        pid = int(row["partido_id"])
        fecha = str(row["fecha"])
        cancha = row["cancha"]
        es_ofi = bool(row["es_oficial"])
        dif = row["diferencia_gol"]
        ganador = row["ganador"]
        with st.expander("Partido #%d — %s — %s" % (pid, fecha, cancha), expanded=False):
            _badge(_oficial_texto(es_ofi), _oficial_color(es_ofi))
            if pd.notna(dif):
                try:
                    st_diff = int(float(dif))
                except Exception:
                    st_diff = None
                if st_diff is not None:
                    _badge("Diff: %d" % st_diff, "#334155")
            if ganador is None and (str(dif) == "0" or str(dif).strip() == "0.0"):
                resultado_txt = "Empate"
            else:
                resultado_txt = _ganador_texto_simple(ganador)
            st.markdown("**Resultado:** %s" % resultado_txt)

            df_j = read_sql_df(SQL_JUGADORES_DE_PARTIDO, (pid,))
            if df_j.empty:
                st.caption("Sin jugadores asignados.")
            else:
                for eq in (1, 2):
                    sub = df_j[df_j["equipo"] == eq]
                    if sub.empty:
                        st.write("**%s:** (sin datos)" % _equipo_label(eq))
                        continue
                    cam = sub["camiseta"].mode().iloc[0] if sub["camiseta"].notna().any() else None
                    icon = _camiseta_emoji(cam)
                    lista = " · ".join(sub["jugador_nombre"].tolist())
                    st.write("**%s %s:** %s" % (_equipo_label(eq), icon, lista))

def _render_month(year: int, month: int, key_prefix: str):
    cal = calendar.Calendar(firstweekday=0)  # 0 = lunes
    weeks = cal.monthdayscalendar(year, month)
    days_with = _days_with_match(year, month)

    # Caja del mes
    with st.container():
        st.markdown('<div class="month-box">', unsafe_allow_html=True)
        st.markdown('<h4 class="month-title">%s %d</h4>' % (MESES_NOMBRES[month-1], year), unsafe_allow_html=True)

        # Encabezado (misma grilla de 7 columnas)
        hdr_cols = st.columns(7, gap="small")
        for i, name in enumerate(DIAS_HDR):
            with hdr_cols[i]:
                st.markdown('<div class="weekday">%s</div>' % name, unsafe_allow_html=True)

        # Celdas (7 columnas por semana)
        for w_idx, week in enumerate(weeks):
            row_cols = st.columns(7, gap="small")
            for d_idx, day in enumerate(week):
                with row_cols[d_idx]:
                    if day == 0:
                        # Hueco con el mismo alto que un botón normal
                        st.markdown('<div class="day-empty">', unsafe_allow_html=True)
                        st.button(" ", key="%s_blank_%d_%02d_%d_%d" % (key_prefix, year, month, w_idx, d_idx),
                                  disabled=True, use_container_width=True)
                        st.markdown('</div>', unsafe_allow_html=True)
                        continue

                    has_match = day in days_with
                    label = ("%02d 🔵" % day) if has_match else ("%02d ⚪" % day)
                    btn_key = "%s_day_%d_%02d_%02d" % (key_prefix, year, month, day)
                    clicked = st.button(label, key=btn_key, use_container_width=True)
                    if clicked:
                        st.session_state["hist_cal_selected_date"] = "%d-%02d-%02d" % (year, month, day)

        st.markdown('</div>', unsafe_allow_html=True)  # /month-box

def _render_year_calendar_grid(year: int):
    # CSS global del calendario
    st.markdown(_CALENDAR_CSS, unsafe_allow_html=True)
    st.markdown('<div class="cal-wrap">', unsafe_allow_html=True)

    st.caption("Tocá un día 🔵 para ver sus partidos (⚪ = sin partido).")

    # 2 meses por fila, con columna separadora
    for fila in range(6):  # hasta 12 meses (6 filas × 2 columnas)
        month_left = fila * 2 + 1
        month_right = fila * 2 + 2
        if month_left > 12:
            break

        left_col, spacer, right_col = st.columns([1, 0.08, 1], gap="large")
        with left_col:
            _render_month(year, month_left, key_prefix="histcal")
        if month_right <= 12:
            with right_col:
                _render_month(year, month_right, key_prefix="histcal")

        # Separación entre filas de meses
        st.markdown("&nbsp;", unsafe_allow_html=True)

    st.markdown('<hr style="opacity:0.2;">', unsafe_allow_html=True)

    sel = st.session_state.get("hist_cal_selected_date")
    if sel:
        st.markdown("### Partidos del **%s**" % sel)
        _render_partidos_detail_for_day(sel)
    else:
        st.caption("Seleccioná una fecha para ver su detalle.")

    st.markdown('</div>', unsafe_allow_html=True)  # /cal-wrap

# =========================
# Tabs
# =========================
def _render_tab_calendario():
    st.subheader("🗓️ Calendario de partidos por año")

    years = _years_available()
    anio_sel = st.selectbox("Temporada (año)", years, index=0, key="hist_cal_sel_anio")
    try:
        year = int(anio_sel)
    except Exception:
        year = datetime.now().year

    _render_year_calendar_grid(year)

def _render_tab_historial_elo():
    st.subheader("📈 Historial de ELO")

    df = read_sql_df(SQL_HISTORIAL_ELO_BASE + " ORDER BY datetime(fecha) DESC, historial_id DESC")
    if df.empty:
        st.info("Aún no hay cambios de ELO registrados.")
        return

    with st.container():
        col1, col2, col3 = st.columns([1, 1, 1])
        with col1:
            jugadores_unicos = ["(Todos)"] + sorted(df["jugador_nombre"].dropna().unique().tolist())
            jug_sel = st.selectbox("Filtrar por jugador", jugadores_unicos, index=0, key="hist_elo_sel_jugador")
        with col2:
            id_part = st.text_input("Filtrar por ID de partido", value="", key="hist_elo_filtro_partido")
        with col3:
            ordenar_desc = st.toggle("Ordenar por fecha descendente", value=True, key="hist_elo_toggle_order")

    if jug_sel != "(Todos)":
        df = df[df["jugador_nombre"] == jug_sel]
    if id_part.strip():
        df = df[df["partido_id"].astype(str).str.contains(id_part.strip())]

    if df.empty:
        st.warning("No hay resultados con esos filtros.")
        return

    df = df.copy()
    df["ΔELO"] = df.apply(lambda r: _delta_str(r["elo_antes"], r["elo_despues"]), axis=1)
    cols_orden = ["fecha", "jugador_nombre", "partido_id", "elo_antes", "elo_despues", "ΔELO", "historial_id"]
    df = df[cols_orden]

    if ordenar_desc:
        df = df.sort_values(by=["fecha", "historial_id"], ascending=[False, False]).reset_index(drop=True)
    else:
        df = df.sort_values(by=["fecha", "historial_id"], ascending=[True, True]).reset_index(drop=True)

    st.dataframe(df, use_container_width=True, hide_index=True)
    st.caption("Tip: usa el buscador de la esquina superior derecha de la tabla para filtrar por texto.")

# =========================
# Public panel
# =========================
def panel_historial():
    st.title("6️⃣ Historial")

    tabs = st.tabs(["Calendario", "Historial ELO"])
    with tabs[0]:
        _render_tab_calendario()
    with tabs[1]:
        _render_tab_historial_elo()

    st.divider()
    if st.button("⬅️ Volver al menú principal", key="hist_btn_volver"):
        st.session_state.admin_page = None
        st.rerun()
