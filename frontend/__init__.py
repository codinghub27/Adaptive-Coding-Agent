"""Streamlit frontend for the Adaptive Coding Tutor.

This package talks to the backend over HTTP only (`frontend.api_client`). It
never imports `app.*`, never touches the database, and never runs or reaches
the code-execution sandbox -- see the Phase 08 frontend packet.
"""
