"""Ponto de entrada para o `adk web`, usado so durante o desenvolvimento.

A entrega e a API; este modulo existe para inspecionar transferencias,
chamadas de tool e pedidos de confirmacao na interface do ADK.
"""

from __future__ import annotations

from . import criar_app

app = criar_app()
root_agent = app.root_agent
