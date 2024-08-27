from __future__ import annotations

import os

import pandas as pd
import streamlit as st

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
def get_df_spread_controller() -> pd.DataFrame:
    generator = get_pid_dict_generator()
    return pd.DataFrame(generator.spread_controller())


@st.cache_data
def get_df_margin_controller() -> pd.DataFrame:
    generator = get_pid_dict_generator()
    return pd.DataFrame(generator.margin_controller())


def page():

    st.header("Spread Controller")
    st.dataframe(get_df_spread_controller())
    st.markdown("---")
    st.header("Margin Controller")
    st.dataframe(get_df_margin_controller())
