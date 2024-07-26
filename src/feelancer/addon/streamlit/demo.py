from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import streamlit as st

from feelancer.addon.ewma_simulate import EwmaSimulator

if TYPE_CHECKING:
    pass

st.set_page_config(layout="wide")


def get_input(var_name: str, value: float | None = None) -> float:
    step = 5.0
    max_value = 1000.0
    format = "%0.3f"
    if var_name[0:6] == "alpha_":
        step = 0.01
    if var_name == "k_d":
        step = 0.5
    if var_name == "upper_bound":
        step = 0.1

    if not value:
        value = 0.0

    input = st.number_input(
        var_name,
        min_value=-max_value,
        max_value=max_value,
        value=value,
        step=step,
        format=format,
    )
    return float(input)  # type: ignore


with st.sidebar:
    st.header("EwmaController Simulation")

    param_col, sim_col = st.columns(2)
    with param_col:
        k_t = get_input("k_t")
        k_p = get_input("k_p")
        st.markdown("---")

        alpha_i = get_input("alpha_i")
        k_i = get_input("k_i")
        st.markdown("---")

        alpha_d = get_input("alpha_d")
        k_d = get_input("k_d")

    with sim_col:
        error = get_input("error", -0.5)
        ewma = get_input("ewma", -0.5)
        delta_residual = get_input("delta_residual", 0)

        sim_days = st.number_input(
            "number of days",
            min_value=0,
            max_value=30,
            value=10,
            step=1,
        )
        upper_bound = get_input("upper_bound", 1.0)
        iterations = st.number_input(
            "iterations",
            min_value=0,
            max_value=10000,
            value=100,
            step=10,
        )


simulator = EwmaSimulator(
    k_t=k_t,
    k_p=k_p,
    k_i=k_i,
    k_d=k_d,
    alpha_d=alpha_d,
    alpha_i=alpha_i,
)
sim_res = simulator.simulate(
    number_days=int(sim_days),
    steps=600,
    error=error,
    ewma=ewma,
    error_delta_resudal=delta_residual,
    error_1=0.5,
)

mc_res = simulator.monte_carlo(
    int(sim_days), steps=600, upper_bound=upper_bound, iterations=iterations  # type: ignore
)

# opt_res = ewma_optimize(
#     upper_bound=3,
#     gradient=30,
#     error=-0.5,
#     ewma=-0.5,
#     error_delta_resudal=0,
#     error_1=0.5,
# )

col_standard, col_mc = st.columns(2)

with col_standard:
    st.markdown(
        """
We simulate the depletion of a channel. The channel was 100% local for a longer
time, i.e. `error = error_ewma = -0.5`. In the first second the channel depleted
and the balance is 100% remote. The simulation shows how quickly the fee rate
(in ppm) would change with the set parameters. 
"""
    )
    st.write(sim_res.smoothing(0, upper_bound))
    st.line_chart(sim_res.df[["day", "value"]].set_index("day"))
    st.write(sim_res.df)

with col_mc:
    results = np.array(mc_res)
    st.write("mean: ", np.mean(results))
    st.write("std: ", np.std(results))
    st.write(mc_res)
