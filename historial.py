# historial.py — Calendario (FullCalendar) + Historial ELO
from db import get_connection
from pathlib import Path
from typing import Optional
from datetime import datetime, date
import pandas as pd
import streamlit as st
from streamlit_calendar import calendar as fc_calendar

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
    Ejecuta una query y devuelve un DataFrame, autocasteando a numérico
    las columnas mayormente numéricas para evitar problemas con pandas.
    """
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(query, params)
        rows = cur.fetchall()

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)

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
            df[c] = pd.to_numeric(
                df[c].astype(str).str.replace(",", ".", regex=False),
                errors="coerce",
            )

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
        """
        % (background, color, texto),
        unsafe_allow_html=True,
    )


def _camiseta_emoji(camiseta: Optional[str]) -> str:
    if not camiseta:
        return "👕"
    c = str(camiseta).strip().lower()
    if c.startswith("clara"):
        return "⚪"
    if c.startswith("osc"):
        return "⬛"
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
# Helpers comunes (años, partidos por fecha)
# =========================
def _years_available():
    df = read_sql_df(
        """
        SELECT DISTINCT SUBSTR(fecha,1,4) AS anio
        FROM partidos
        WHERE fecha IS NOT NULL AND TRIM(fecha)!=''
        ORDER BY anio DESC
    """
    )
    if df.empty:
        return [str(datetime.now().year)]
    return df["anio"].astype(str).tolist()


def _partidos_by_date(date_iso: str):
    """
    Lista los partidos jugados de una fecha específica:
    - con resultado
    - con jugadores asignados (al menos uno)
    - fecha exacta = date_iso
    """
    return read_sql_df(
        """
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
    """,
        (date_iso,),
    )


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
                    cam = (
                        sub["camiseta"].mode().iloc[0]
                        if sub["camiseta"].notna().any()
                        else None
                    )
                    icon = _camiseta_emoji(cam)
                    lista = " · ".join(sub["jugador_nombre"].tolist())
                    st.write("**%s %s:** %s" % (_equipo_label(eq), icon, lista))


# =========================
# FullCalendar
# =========================
def _partidos_eventos_para_fullcalendar(year: int):
    """
    Devuelve una lista de eventos FullCalendar a partir de la BD,
    solo con partidos jugados (con resultado), con jugadores y no futuros.
    """
    today_iso = date.today().isoformat()
    df = read_sql_df(
        """
        SELECT
            p.id                           AS partido_id,
            p.fecha                        AS fecha_iso,
            COALESCE(c.nombre,'—')         AS cancha,
            p.ganador,
            p.diferencia_gol,
            p.es_oficial
        FROM partidos p
        LEFT JOIN canchas c ON c.id = p.cancha_id
        WHERE SUBSTR(p.fecha,1,4) = ?
          AND SUBSTR(p.fecha,1,10) <= ?
          AND (p.ganador IS NOT NULL OR p.diferencia_gol IS NOT NULL)
          AND EXISTS (
                SELECT 1 FROM partido_jugadores pj
                WHERE pj.partido_id = p.id
          )
        ORDER BY p.fecha ASC, p.id ASC
    """,
        (str(year), today_iso),
    )

    events = []
    if not df.empty:
        for _, r in df.iterrows():
            start_iso = str(r["fecha_iso"])[:19]
            ganador = r["ganador"]
            dif = r["diferencia_gol"]
            if ganador is None and (str(dif) == "0" or str(dif).strip() == "0.0"):
                res_txt = "Empate"
            else:
                try:
                    gi = int(ganador)
                    res_txt = {1: "Ganó Eq.1", 2: "Ganó Eq.2", 0: "Empate"}.get(
                        gi, "Resultado"
                    )
                except Exception:
                    res_txt = "Resultado"

            title = "Partido #%d · %s · %s" % (
                int(r["partido_id"]),
                res_txt,
                r["cancha"],
            )
            color = "#2563eb" if bool(r["es_oficial"]) else "#64748b"

            events.append(
                {
                    "id": str(int(r["partido_id"])),
                    "title": title,
                    "start": start_iso,
                    "allDay": True,
                    "backgroundColor": color,
                    "borderColor": color,
                }
            )

    return events


def _render_tab_calendario_fullcalendar():
    st.subheader("🗓️ Calendario de partidos")

    years = _years_available()
    anio_sel = st.selectbox("Temporada (año)", years, index=0, key="hist_fc_anio")
    try:
        year = int(anio_sel)
    except Exception:
        year = datetime.now().year

    events = _partidos_eventos_para_fullcalendar(year)

    options = {
        "locale": "es",
        "initialView": "dayGridMonth",
        "height": "auto",
        "firstDay": 1,  # lunes
        "headerToolbar": {
            "left": "prev,next today",
            "center": "title",
            "right": "dayGridMonth,dayGridWeek,listWeek",
        },
        "eventDisplay": "block",
        "dayMaxEventRows": 4,
    }

    st.markdown(
        "<div style='border:1px solid rgba(255,255,255,0.1);"
        "border-radius:12px;padding:8px'>",
        unsafe_allow_html=True,
    )
    ret = fc_calendar(events=events, options=options, key="hist_fc_%d" % year)
    st.markdown("</div>", unsafe_allow_html=True)

    # Click en evento => mostramos detalle
    if isinstance(ret, dict):
        ev_click = ret.get("eventClick")
        if isinstance(ev_click, dict):
            ev = ev_click.get("event")
            if isinstance(ev, dict):
                pid_str = ev.get("id")
                if pid_str:
                    try:
                        pid = int(pid_str)
                    except Exception:
                        pid = None
                    if pid is not None:
                        df = read_sql_df(
                            """
                            SELECT p.id AS partido_id,
                                   p.fecha,
                                   COALESCE(c.nombre,'—') AS cancha,
                                   p.ganador,
                                   p.diferencia_gol,
                                   p.es_oficial
                            FROM partidos p
                            LEFT JOIN canchas c ON c.id = p.cancha_id
                            WHERE p.id = ?
                        """,
                            (pid,),
                        )
                        if not df.empty:
                            fecha_sel = str(df.iloc[0]["fecha"])[:10]
                            st.markdown("### Partidos del **%s**" % fecha_sel)
                            _render_partidos_detail_for_day(fecha_sel)


# =========================
# Tab Historial ELO
# =========================
def _render_tab_historial_elo():
    st.subheader("📈 Historial de ELO")

    df = read_sql_df(
        SQL_HISTORIAL_ELO_BASE
        + " ORDER BY datetime(fecha) DESC, historial_id DESC"
    )
    if df.empty:
        st.info("Aún no hay cambios de ELO registrados.")
        return

    with st.container():
        col1, col2, col3 = st.columns([1, 1, 1])
        with col1:
            jugadores_unicos = ["(Todos)"] + sorted(
                df["jugador_nombre"].dropna().unique().tolist()
            )
            jug_sel = st.selectbox(
                "Filtrar por jugador",
                jugadores_unicos,
                index=0,
                key="hist_elo_sel_jugador",
            )
        with col2:
            id_part = st.text_input(
                "Filtrar por ID de partido",
                value="",
                key="hist_elo_filtro_partido",
            )
        with col3:
            ordenar_desc = st.toggle(
                "Ordenar por fecha descendente",
                value=True,
                key="hist_elo_toggle_order",
            )

    if jug_sel != "(Todos)":
        df = df[df["jugador_nombre"] == jug_sel]
    if id_part.strip():
        df = df[df["partido_id"].astype(str).str.contains(id_part.strip())]

    if df.empty:
        st.warning("No hay resultados con esos filtros.")
        return

    df = df.copy()
    df["ΔELO"] = df.apply(
        lambda r: _delta_str(r["elo_antes"], r["elo_despues"]), axis=1
    )
    cols_orden = [
        "fecha",
        "jugador_nombre",
        "partido_id",
        "elo_antes",
        "elo_despues",
        "ΔELO",
        "historial_id",
    ]
    df = df[cols_orden]

    if ordenar_desc:
        df = df.sort_values(
            by=["fecha", "historial_id"], ascending=[False, False]
        ).reset_index(drop=True)
    else:
        df = df.sort_values(
            by=["fecha", "historial_id"], ascending=[True, True]
        ).reset_index(drop=True)

    st.dataframe(df, use_container_width=True, hide_index=True)
    st.caption(
        "Tip: usa el buscador de la esquina superior derecha de la tabla para filtrar por texto."
    )


# =========================
# Public panel
# =========================
def panel_historial():
    st.title("6️⃣ Historial")

    tabs = st.tabs(["Calendario", "Historial ELO"])
    with tabs[0]:
        _render_tab_calendario_fullcalendar()
    with tabs[1]:
        _render_tab_historial_elo()

    st.divider()
    if st.button("⬅️ Volver al menú principal", key="hist_btn_volver"):
        st.session_state.admin_page = None
        st.rerun()
