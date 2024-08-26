import streamlit as st
from typing_extensions import Callable

from feelancer.addon.streamlit import ewma_data, ewma_simulator

st.set_page_config(layout="wide")


# Dictionary to store page names and functions
pages: dict[str, Callable] = {
    "Ewma Simulator": ewma_simulator.page,
    "Ewma Data Analysis": ewma_data.page,
}

# Sidebar for navigation
st.sidebar.title("Navigation")
selection = str(st.sidebar.selectbox("Select a page", list(pages.keys())))

# Call the selected page function
page = pages.get(selection)
if page:
    page()
