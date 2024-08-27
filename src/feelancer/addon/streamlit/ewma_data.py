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
        raise EnvironmentError(
            "env variable 'FEELANCER_CONFIG' is not set. A config file with "
            "the information about the database is necessary."
        )
    config_dict = read_config_file(config)

    return FeelancerDB.from_config_dict(config_dict["sqlalchemy"]["url"])


@st.cache_resource
def get_pid_dict_generator() -> PidDictGen:
    return PidDictGen(get_db())


@st.cache_data
def get_df_spread_controller() -> pl.DataFrame:
    generator = get_pid_dict_generator()
    return pl.DataFrame(generator.spread_controller())


@st.cache_data
def get_df_margin_controller() -> pl.DataFrame:
    generator = get_pid_dict_generator()
    return pl.DataFrame(generator.margin_controller())


def page():

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
    code1 = "select * from spread_controller"
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
            code1 = res1["text"]

            with pl.SQLContext(
                margin_controller=get_df_margin_controller(),
                spread_controller=get_df_spread_controller(),
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
            code2 = res2["text"]

            with pl.SQLContext(
                margin_controller=get_df_margin_controller(),
                spread_controller=get_df_spread_controller(),
                eager=True,
            ) as ctx:
                st.session_state.df_res2 = ctx.execute(code2)

        if "df_res2" in st.session_state:
            st.dataframe(st.session_state.df_res2)
