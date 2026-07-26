"""Anwendungszustand und -steuerung fuer die Desktop-UIs: AppState
(desktop_state.py), Controller (desktop_controller.py) und die
Sitzungs-Persistierung (session_store.py). Framework-unabhaengig - wird von
der aktuellen Qt-Oberflaeche und der archivierten CustomTkinter-Oberflaeche
in legacy/ genutzt (nicht von der archivierten Streamlit-Oberflaeche, die
ihren Zustand ueber st.session_state selbst verwaltet, session_store.py
aber direkt fuer die Sitzungs-Persistierung nutzt)."""
