"""API do assistente do Residencial Aurora."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException, Request
from google.genai import types

from .. import confirmacoes, dados, storage
from ..agentes import criar_runner
from ..config import APP_NAME
from ..tools.contexto import CHAVE_APARTAMENTO
from .runtime import executar_turno, resposta_de_confirmacao, serializar_evento
from .schemas import (
    ConfirmacaoPedido,
    CriarSessaoPedido,
    CriarSessaoResposta,
    MensagemPedido,
    ReservaResposta,
    TurnoResposta,
    VisitanteResposta,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await storage.inicializar()
    app.state.runner = criar_runner()
    try:
        yield
    finally:
        await app.state.runner.close()


app = FastAPI(title="Assistente do Residencial Aurora", lifespan=lifespan)


def _runner(request: Request):
    return request.app.state.runner


async def _sessao_ou_404(session_id: str) -> dict[str, str]:
    sessao = await storage.buscar_sessao(session_id)
    if sessao is None:
        raise HTTPException(status_code=404, detail="Sessao nao encontrada.")
    return sessao


async def _turno(
    request: Request,
    sessao: dict[str, str],
    new_message: types.Content | None,
    invocation_id: str | None = None,
) -> TurnoResposta:
    texto = await executar_turno(
        runner=_runner(request),
        user_id=sessao["user_id"],
        session_id=sessao["session_id"],
        new_message=new_message,
        invocation_id=invocation_id,
    )
    return TurnoResposta(
        resposta=texto,
        confirmacoes_pendentes=await confirmacoes.pendentes(sessao["session_id"]),
    )


# --------------------------------------------------------------------------
# Conversa
# --------------------------------------------------------------------------


@app.post("/sessoes", status_code=201, response_model=CriarSessaoResposta)
async def criar_sessao(
    pedido: CriarSessaoPedido, request: Request
) -> CriarSessaoResposta:
    """Cria a sessao do morador.

    Este e o **unico** ponto do sistema em que o apartamento e definido: ele
    vai para o `state` da sessao e, dali em diante, e de la que as tools o
    leem (Garantia 2).
    """
    numero = pedido.apartamento.strip()
    morador = dados.morador_de(numero)
    if morador is None:
        raise HTTPException(status_code=404, detail="Apartamento nao encontrado.")

    user_id = f"apto-{numero}"
    sessao = await _runner(request).session_service.create_session(
        app_name=APP_NAME,
        user_id=user_id,
        state={CHAVE_APARTAMENTO: numero, "morador": morador},
    )
    await storage.registrar_sessao(sessao.id, user_id, numero)
    return CriarSessaoResposta(session_id=sessao.id)


@app.post("/sessoes/{session_id}/mensagens", response_model=TurnoResposta)
async def enviar_mensagem(
    session_id: str, pedido: MensagemPedido, request: Request
) -> TurnoResposta:
    sessao = await _sessao_ou_404(session_id)
    mensagem = types.Content(role="user", parts=[types.Part(text=pedido.texto)])
    return await _turno(request, sessao, mensagem)


@app.post("/sessoes/{session_id}/confirmacoes", response_model=TurnoResposta)
async def responder_confirmacao(
    session_id: str, pedido: ConfirmacaoPedido, request: Request
) -> TurnoResposta:
    """Responde uma confirmacao pendente.

    A resposta so e aceita quando existe uma confirmacao **pendente com esse
    id nesta sessao**. Quem decide isso e o `UPDATE` condicional em
    `confirmacoes.reivindicar`, executado antes de qualquer execucao: id
    desconhecido, id de outra sessao e reenvio de id ja respondido recebem
    `409` sem que nada rode.
    """
    sessao = await _sessao_ou_404(session_id)

    pendente = await confirmacoes.reivindicar(session_id, pedido.id)
    if pendente is None:
        raise HTTPException(
            status_code=409,
            detail="Nao existe confirmacao pendente com esse id nesta sessao.",
        )

    return await _turno(
        request,
        sessao,
        resposta_de_confirmacao(pedido.id, pedido.confirmado),
        invocation_id=pendente["invocation_id"],
    )


@app.get("/sessoes/{session_id}/eventos")
async def listar_eventos(session_id: str, request: Request) -> list[dict[str, Any]]:
    sessao = await _sessao_ou_404(session_id)
    adk_sessao = await _runner(request).session_service.get_session(
        app_name=APP_NAME, user_id=sessao["user_id"], session_id=session_id
    )
    if adk_sessao is None:
        raise HTTPException(status_code=404, detail="Sessao nao encontrada.")
    return [serializar_evento(evento) for evento in adk_sessao.events]


# --------------------------------------------------------------------------
# Rotas de verificacao: leem os dados direto, sem passar pelo modelo.
# --------------------------------------------------------------------------


@app.get("/apartamentos/{numero}/reservas", response_model=list[ReservaResposta])
async def reservas_do_apartamento(numero: str) -> list[dict[str, str]]:
    return await storage.listar_reservas(numero)


@app.get("/apartamentos/{numero}/visitantes", response_model=list[VisitanteResposta])
async def visitantes_do_apartamento(numero: str) -> list[dict[str, str]]:
    return await storage.listar_visitantes(numero)
