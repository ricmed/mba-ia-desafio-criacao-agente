"""Execucao de um turno do assistente e leitura dos pedidos de confirmacao.

O ADK sinaliza um pedido de confirmacao emitindo uma chamada de funcao
reservada, `adk_request_confirmation`, cujos argumentos trazem a chamada de
tool original e o `ToolConfirmation` (com a dica e o payload que a tool
montou). Este modulo le esses eventos, guarda a pendencia no banco e, do outro
lado, constroi a resposta que retoma a invocacao parada.
"""

from __future__ import annotations

import logging
from typing import Any

from google.adk.events.event import Event
from google.adk.flows.llm_flows.functions import (
    REQUEST_CONFIRMATION_FUNCTION_CALL_NAME,
)
from google.adk.runners import Runner
from google.genai import types

from .. import confirmacoes

logger = logging.getLogger(__name__)


def resposta_de_confirmacao(
    confirmation_id: str, confirmado: bool
) -> types.Content:
    """Monta a mensagem que responde a um pedido de confirmacao do ADK.

    O `id` precisa ser o da chamada `adk_request_confirmation` e o `name`
    precisa ser exatamente esse nome reservado: o ADK ignora silenciosamente
    qualquer outra combinacao, inclusive uma forjada pelo modelo.
    """
    return types.Content(
        role="user",
        parts=[
            types.Part(
                function_response=types.FunctionResponse(
                    id=confirmation_id,
                    name=REQUEST_CONFIRMATION_FUNCTION_CALL_NAME,
                    response={"confirmed": confirmado},
                )
            )
        ],
    )


def _detalhes_do_pedido(args: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Extrai `acao` e `detalhes` dos argumentos do pedido de confirmacao."""
    original = args.get("originalFunctionCall") or {}
    confirmacao = args.get("toolConfirmation") or {}

    acao = original.get("name") or "acao_pendente"
    detalhes = dict(confirmacao.get("payload") or {})
    if not detalhes:
        # Sem payload, ainda assim expomos o que a acao vai executar.
        detalhes = dict(original.get("args") or {})
    if confirmacao.get("hint"):
        detalhes.setdefault("descricao", confirmacao["hint"])
    return acao, detalhes


async def _registrar_pendencias(session_id: str, evento: Event) -> None:
    for chamada in evento.get_function_calls():
        if chamada.name != REQUEST_CONFIRMATION_FUNCTION_CALL_NAME:
            continue
        acao, detalhes = _detalhes_do_pedido(chamada.args or {})
        await confirmacoes.registrar(
            session_id=session_id,
            confirmation_id=chamada.id,
            invocation_id=evento.invocation_id,
            acao=acao,
            detalhes=detalhes,
        )


def _textos(evento: Event) -> list[str]:
    if not evento.content or not evento.content.parts:
        return []
    return [
        parte.text.strip()
        for parte in evento.content.parts
        if parte.text and not parte.thought and parte.text.strip()
    ]


async def executar_turno(
    runner: Runner,
    user_id: str,
    session_id: str,
    new_message: types.Content | None = None,
    invocation_id: str | None = None,
) -> str:
    """Roda a invocacao ate ela terminar ou parar esperando confirmacao.

    Devolve o texto que o assistente produziu no turno, que pode ser vazio
    quando a execucao parou no pedido de confirmacao.
    """
    partes: list[str] = []
    async for evento in runner.run_async(
        user_id=user_id,
        session_id=session_id,
        invocation_id=invocation_id,
        new_message=new_message,
    ):
        await _registrar_pendencias(session_id, evento)
        for texto in _textos(evento):
            if texto not in partes:
                partes.append(texto)

    return "\n\n".join(partes)


def serializar_evento(evento: Event) -> dict[str, Any]:
    """Evento completo, como o ADK o gravou na sessao."""
    return evento.model_dump(mode="json", exclude_none=True)
