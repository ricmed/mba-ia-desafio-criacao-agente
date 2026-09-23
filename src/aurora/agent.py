"""Ponto de entrada para o `adk web`, usado so durante o desenvolvimento.

A entrega e a API (`uv run aurora`); este modulo existe para inspecionar
transferencias, chamadas de tool e pedidos de confirmacao na interface do ADK.

Fica na raiz do pacote de proposito: o carregador do `adk web` importa a pasta
do agente como modulo de topo, entao o modulo precisa ser o pacote `aurora`
inteiro para que os imports relativos continuem validos. Por isso o comando e
`adk web src`, e nao `adk web src/aurora`.

A sessao do `adk web` nasce sem apartamento. Crie uma ja vinculada antes de
conversar:

    curl -X POST http://localhost:8080/apps/aurora/users/apto-101/sessions \
      -H 'Content-Type: application/json' \
      -d '{"state": {"apartamento": "101", "morador": "Helena Prado"}}'
"""

from __future__ import annotations

from .agentes import criar_app

app = criar_app()
root_agent = app.root_agent
