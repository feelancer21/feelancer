from __future__ import annotations

import os

import pandas as pd
import streamlit as st

from feelancer.data.db import FeelancerDB
from feelancer.pid.data import PidDictGen
from feelancer.utils import read_config_file


def page():
    config = os.getenv("FEELANCER_CONFIG")

    if not config:
        st.write(
            "env variable 'FEELANCER_CONFIG' is not set. A config file with "
            "the information about the database is necessary."
        )
        return None
    config_dict = read_config_file(config)

    db = FeelancerDB.from_config_dict(config_dict["sqlalchemy"]["url"])

    pidgen = PidDictGen(db)

    st.header("Spread Controller")
    st.dataframe(pd.DataFrame(pidgen.spread_controller()))
    st.markdown("---")
    st.header("Margin Controller")
    st.dataframe(pd.DataFrame(pidgen.margin_controller()))
