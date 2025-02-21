from __future__ import annotations

import os

import polars as pl
import streamlit as st
from code_editor import code_editor

from feelancer.data.db import FeelancerDB
from feelancer.pid.data import PidDictGen
from feelancer.utils import read_config_file


@st.cache_resource
def get_db() -> FeelancerDB:
    config = os.getenv("FEELANCER_CONFIG")

    if not config:
        raise OSError(
            "env variable 'FEELANCER_CONFIG' is not set. A config file with "
            "the information about the database is necessary."
        )
    config_dict = read_config_file(config)

    return FeelancerDB.from_config_dict(config_dict["sqlalchemy"]["url"])


@st.cache_resource
def get_pid_dict_generator() -> PidDictGen:
    return PidDictGen(get_db())


def load_dataframes() -> None:
    g = get_pid_dict_generator()
    # When running the first time, session state is set.
    if "spread_controller" not in st.session_state:
        st.session_state.spread_controller = None
    if "margin_controller" not in st.session_state:
        st.session_state.margin_controller = None

    if st.session_state.spread_controller is None:
        st.session_state.spread_controller = pl.DataFrame(g.spread_controller())

    if st.session_state.margin_controller is None:
        st.session_state.margin_controller = pl.DataFrame(g.margin_controller())


def reload_dataframes() -> None:
    st.session_state.spread_controller = None
    st.session_state.margin_controller = None
    load_dataframes()


def page():
    load_dataframes()
    with st.sidebar:
        st.button("Reload Dataframes", on_click=reload_dataframes)

    btns = [
        {
            "name": "copy",
            "feather": "Copy",
            "hasText": True,
            "alwaysOn": True,
            "commands": ["copyAll"],
            "style": {"top": "0rem", "right": "0.4rem"},
        },
        {
            "name": "update",
            "feather": "RefreshCw",
            "primary": True,
            "hasText": True,
            "showWithIcon": True,
            "commands": ["submit"],
            "style": {"bottom": "0rem", "right": "0.4rem"},
        },
    ]

    height = 200
    language = "sql"
    theme = "default"
    shortcuts = "vscode"
    focus = False
    wrap = True
    ace_props = {"style": {"borderRadius": "0px 0px 8px 8px"}}

    code1 = st.session_state.get("code1")
    code2 = st.session_state.get("code2")

    if not code1:
        code1 = "select * from spread_controller"

    if not code2:
        code2 = "select * from margin_controller"

    with st.expander("", expanded=True):
        col1, _ = st.columns(2)
        with col1:
            res1 = code_editor(
                code=code1,
                height=height,
                lang=language,
                theme=theme,
                shortcuts=shortcuts,
                focus=focus,
                buttons=btns,
                info={},
                props=ace_props,
                response_mode="debounce",
                options={"wrap": wrap},
            )

        if res1["type"] == "submit":
            st.session_state.code1 = code1 = res1["text"]

            with pl.SQLContext(
                margin_controller=st.session_state.margin_controller,
                spread_controller=st.session_state.spread_controller,
                eager=True,
            ) as ctx:
                st.session_state.df_res1 = ctx.execute(code1)

        if "df_res1" in st.session_state:
            st.dataframe(st.session_state.df_res1)

    with st.expander("", expanded=True):
        col1, _ = st.columns(2)
        with col1:
            res2 = code_editor(
                code=code2,
                height=height,
                lang=language,
                theme=theme,
                shortcuts=shortcuts,
                focus=focus,
                buttons=btns,
                info={},
                props=ace_props,
                response_mode="debounce",
                options={"wrap": wrap},
            )

        if res2["type"] == "submit":
            st.session_state.code2 = code2 = res2["text"]

            with pl.SQLContext(
                margin_controller=st.session_state.margin_controller,
                spread_controller=st.session_state.spread_controller,
                eager=True,
            ) as ctx:
                st.session_state.df_res2 = ctx.execute(code2)

        if "df_res2" in st.session_state:
            st.dataframe(st.session_state.df_res2)
