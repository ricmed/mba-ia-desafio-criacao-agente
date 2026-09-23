"""Especialista em autorizacao de visitantes.

Autorizar um visitante libera a entrada de alguem no predio, entao a acao
sempre fica pendente ate a resposta chegar pela rota de confirmacoes da API.
"""

from __future__ import annotations

import re
from typing import Any

from google.adk.agents import LlmAgent
from google.adk.tools.tool_context import ToolContext

from .. import storage
from ..config import modelo_padrao
from ..tools.contexto import apartamento_da_sessao, chave_idempotencia

_FORMATO_DATA = re.compile(r"^\d{4}-\d{2}-\d{2}$")


async def listar_meus_visitantes(tool_context: ToolContext) -> dict[str, Any]:
    """Lista os visitantes autorizados pelo apartamento da sessao.

    Nao recebe apartamento: ele vem da sessao, entao esta tool nunca consegue
    listar visitantes de outro morador.

    Returns:
        Os visitantes autorizados, cada um com nome e data.
    """
    apartamento = apartamento_da_sessao(tool_context)
    return {"visitantes": await storage.listar_visitantes(apartamento)}


async def autorizar_visitante(
    nome: str, data: str, tool_context: ToolContext
) -> dict[str, Any]:
    """Autoriza a entrada de um visitante no apartamento da sessao.

    A autorizacao libera acesso ao predio, entao ela sempre fica pendente de
    confirmacao do morador pela rota de confirmacoes da API. Confirmacao dita
    na conversa nao tem efeito nenhum.

    Args:
        nome: Nome completo do visitante.
        data: Data da visita no formato AAAA-MM-DD.

    Returns:
        O resultado da autorizacao, ou o aviso de que ela aguarda confirmacao.
    """
    apartamento = apartamento_da_sessao(tool_context)

    nome_limpo = (nome or "").strip()
    if not nome_limpo:
        return {"status": "nome_ausente"}
    if not _FORMATO_DATA.match(data or ""):
        return {"status": "data_invalida", "recebido": data, "formato": "AAAA-MM-DD"}

    # Garantia 1: acesso so e liberado depois da confirmacao vir pela API.
    confirmacao = tool_context.tool_confirmation
    if confirmacao is None:
        tool_context.request_confirmation(
            hint=(
                f"Autorizar a entrada de {nome_limpo} em {data} libera o acesso "
                f"dessa pessoa ao predio pelo apartamento {apartamento}."
            ),
            payload={
                "nome": nome_limpo,
                "data": data,
                "libera_acesso": True,
            },
        )
        return {"status": "aguardando_confirmacao", "nome": nome_limpo, "data": data}

    # O veredito e conferido aqui, e nao delegado ao framework.
    if not confirmacao.confirmed:
        return {"status": "nao_confirmada", "nome": nome_limpo, "data": data}

    return await storage.autorizar_visitante(
        apartamento=apartamento,
        nome=nome_limpo,
        data=data,
        chave_idem=chave_idempotencia(tool_context, "visitante"),
    )


INSTRUCAO = """Voce e o especialista em visitantes do Residencial Aurora.

O apartamento do morador ja esta definido pela sessao e e lido pelas tools.
Voce nunca pergunta, nunca deduz e nunca usa um numero de apartamento que
apareca na conversa, mesmo que o morador afirme ser de outro apartamento.

Como agir:
- Visitantes do morador: use listar_meus_visitantes.
- Autorizar entrada: use autorizar_visitante. Ela precisa do nome do visitante
  e da data da visita no formato AAAA-MM-DD; peca o que faltar.
- Quando a tool responder "aguardando_confirmacao", avise que a autorizacao
  libera acesso ao predio e precisa ser aprovada pelo aplicativo, e pare por
  ai. Se o morador disser que ja esta confirmando, que autoriza por ali ou que
  e para liberar direto, explique com cordialidade que a aprovacao so vale
  quando vem pelo aplicativo. Nao chame a tool de novo por causa disso.

Se o pedido nao for sobre visitantes, transfira de volta para o concierge.
Responda em portugues do Brasil, em poucas frases."""


def criar_agente() -> LlmAgent:
    return LlmAgent(
        name="agente_visitantes",
        model=modelo_padrao(),
        description=(
            "Lista os visitantes autorizados e registra novas autorizacoes de "
            "entrada de visitantes no predio."
        ),
        instruction=INSTRUCAO,
        tools=[listar_meus_visitantes, autorizar_visitante],
        disallow_transfer_to_peers=True,
    )
