"""Montagem do App e do Runner do assistente."""

from __future__ import annotations

import logging
from typing import Any, Optional

from google.adk.agents.callback_context import CallbackContext
from google.adk.apps import App, ResumabilityConfig
from google.adk.runners import Runner
from google.adk.sessions.sqlite_session_service import SqliteSessionService
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext

from ..config import APP_NAME, caminho_banco_sessoes, modelo_padrao
from ..tools.contexto import CHAVE_APARTAMENTO
from .concierge import criar_agente

logger = logging.getLogger(__name__)

# Nomes de argumento pelos quais um modelo poderia tentar escolher em nome de
# quem uma acao acontece.
_ARGS_DE_IDENTIDADE = ("apartamento", "apto", "unidade", "numero_apartamento")


def bloquear_apartamento_do_modelo(
    tool: BaseTool, args: dict[str, Any], tool_context: ToolContext
) -> Optional[dict[str, Any]]:
    """Defesa em profundidade da Garantia 2.

    As tools de dominio simplesmente nao declaram um parametro de apartamento,
    entao o modelo nao tem por onde passar um. Este callback existe para o caso
    de alguem adicionar um no futuro: se um argumento de identidade aparecer e
    divergir do apartamento da sessao, a chamada e barrada antes de executar.
    """
    da_sessao = str(tool_context.state.get(CHAVE_APARTAMENTO, ""))
    for nome in _ARGS_DE_IDENTIDADE:
        if nome not in args:
            continue
        recebido = str(args[nome] or "").strip()
        if recebido and recebido != da_sessao:
            logger.warning(
                "Tool %s recusada: argumento %s=%r diverge do apartamento da sessao.",
                tool.name,
                nome,
                recebido,
            )
            return {
                "status": "recusado",
                "motivo": (
                    "Esta sessao so pode agir sobre o proprio apartamento."
                ),
            }
        args[nome] = da_sessao
    return None


def criar_app() -> App:
    agente = criar_agente()
    agente.before_tool_callback = bloquear_apartamento_do_modelo
    for especialista in agente.sub_agents:
        especialista.before_tool_callback = bloquear_apartamento_do_modelo

    return App(
        name=APP_NAME,
        root_agent=agente,
        # Garantia 1 + 3: sem retomada, a resposta da confirmacao nao consegue
        # voltar para a invocacao que ficou parada esperando por ela.
        resumability_config=ResumabilityConfig(is_resumable=True),
    )


def criar_session_service() -> SqliteSessionService:
    """Sessoes em SQLite: sobrevivem ao reinicio e suportam confirmacao."""
    return SqliteSessionService(str(caminho_banco_sessoes()))


def criar_runner(session_service: SqliteSessionService | None = None) -> Runner:
    return Runner(
        app=criar_app(),
        session_service=session_service or criar_session_service(),
    )


__all__ = [
    "APP_NAME",
    "bloquear_apartamento_do_modelo",
    "criar_app",
    "criar_runner",
    "criar_session_service",
    "modelo_padrao",
]
