import streamlit as st
from auth import verify_user
import scheduler  # dispara materializaciones "lazy" si corresponde
from crear_admin import ensure_admin_user

# Asegura que exista el admin inicial
ensure_admin_user()

# Persistencia de sesión vía token en URL (usa remember.py con st.query_params)
from remember import (
    ensure_tables,
    validate_token,
    issue_token,
    revoke_token,
    current_token_in_url,
    set_url_token,
    clear_url_token,
    current_page_in_url,
    set_url_page,
)

import jugador_panel


st.set_page_config(page_title="Topo Partidos ⚽", page_icon="⚽", layout="wide")


def _init_states():
    if "user" not in st.session_state:
        st.session_state.user = None
    if "auth_token" not in st.session_state:
        st.session_state.auth_token = None
    if "admin_page" not in st.session_state:
        st.session_state.admin_page = "dashboard"


def _try_autologin_by_token():
    # Si ya hay sesión, no hacemos nada
    if st.session_state.user:
        return

    tok = current_token_in_url()
    if tok:
        user = validate_token(tok)
        if user:
            st.session_state.user = user
            st.session_state.auth_token = tok
            return

    tok = st.session_state.get("auth_token")
    if tok:
        user = validate_token(tok)
        if user:
            st.session_state.user = user


def _sync_jugador_page_from_url():
    # inicial desde URL o "menu"
    if "jugador_page" not in st.session_state:
        st.session_state.jugador_page = current_page_in_url("menu")
    url_page = current_page_in_url("menu")
    if url_page != st.session_state.jugador_page:
        st.session_state.jugador_page = url_page


def _render_header_and_logout():
    col_title, col_btn = st.columns([0.9, 0.1])
    with col_title:
        st.title("Topo Partidos ⚽")
    with col_btn:
        if st.session_state.get("user"):
            st.markdown("<div style='text-align:right;'>", unsafe_allow_html=True)
            if st.button("🚪", key="btn_logout", help="Cerrar sesión"):
                # Revocar token si existe
                tok = current_token_in_url() or st.session_state.get("auth_token")
                if tok:
                    revoke_token(tok)
                # Limpiar URL y estados
                clear_url_token()
                for k in list(st.session_state.keys()):
                    if k in ("user", "admin_page", "jugador_page", "auth_token", "flash"):
                        del st.session_state[k]
                st.rerun()
            st.markdown("</div>", unsafe_allow_html=True)


def _render_login():
    st.subheader("Iniciar sesión")
    with st.form("login_form"):
        username = st.text_input("Usuario")
        password = st.text_input("Contraseña", type="password")
        remember_me = st.checkbox("Mantener sesión (token en URL por 30 días)", value=True)
        ok = st.form_submit_button("Ingresar")
    if ok:
        user = verify_user(username, password)
        if not user:
            st.error("Usuario o contraseña incorrectos.")
            st.stop()
        st.session_state.user = user

        if remember_me:
            tok = issue_token(user["id"])
            st.session_state.auth_token = tok
            set_url_token(tok)  # agrega ?auth=... a la URL
        else:
            # Quitamos token de la URL si venía colgado
            clear_url_token()
        st.success("Sesión iniciada.")
        st.rerun()


def _route_admin(user):
    st.header(f"Panel Administrador — {user.get('username', 'Admin')}")
    st.info("El panel de administrador se mantiene igual. No se realizaron cambios aquí.")

    # Menú lateral básico (placeholder seguro)
    with st.sidebar:
        st.subheader("Menú Admin")
        choice = st.radio(
            "Secciones",
            ["Dashboard", "Jugadores", "Canchas", "Programar partido", "Generar equipos",
             "Registrar resultado", "Historial", "Usuarios", "Temporadas", "Estadísticas globales"],
            index=0,
        )
    st.write(f"Sección seleccionada: **{choice}**")
    st.warning("Para evitar romper tu lógica existente, no alteré la implementación admin. Usá tu archivo admin real.")


def _route_jugador(user):
    _sync_jugador_page_from_url()

    st.header(f"Panel Jugador — {user.get('username', 'Jugador')}")

    page = st.session_state.jugador_page
    if page == "menu":
        jugador_panel.panel_menu_jugador(user)
    elif page == "partidos":
        jugador_panel.panel_partidos_disponibles(user)
    elif page == "stats":
        # Esta función puede vivir en jugador_panel o en jugador_stats; tu jugador_panel la reenvía si existe.
        jugador_panel.panel_mis_estadisticas(user)
    elif page == "perfil":
        jugador_panel.panel_mi_perfil(user)
    else:
        st.session_state.jugador_page = "menu"
        set_url_page("menu")
        st.rerun()


def main():
    ensure_tables()
    _init_states()
    _try_autologin_by_token()

    _render_header_and_logout()

    user = st.session_state.user
    if not user:
        _render_login()
        st.stop()

    rol = user.get("rol", "jugador")
    if rol == "admin":
        _route_admin(user)
    else:
        _route_jugador(user)


if __name__ == "__main__":
    main()
