"""Aula-SAMA (demo): conversación con la guardiana virtual y Retos SAMA (trivias).

    uv run streamlit run src/aula_sama/chat/app.py --server.port 8502
"""

from pathlib import Path

import streamlit as st

HERE = Path(__file__).parent

st.set_page_config(page_title="Aula SAMA", page_icon="🌧️")

st.navigation(
    [
        st.Page(HERE / "conversar.py", title="Conversar", icon="💬", default=True),
        st.Page(HERE / "retos.py", title="Retos SAMA", icon="🏆", url_path="retos"),
    ]
).run()
