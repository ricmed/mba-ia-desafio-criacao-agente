"""Especialista em reservas de areas comuns.

E acionado por transferencia (`transfer_to_agent`) a partir do concierge e
concentra as quatro tools que leem e gravam reservas. Nenhuma delas recebe um
apartamento: todas usam o da sessao.
"""

from __future__ import annotations

import re
from typing import Any

from google.adk.agents import LlmAgent
from google.adk.tools.tool_context import ToolContext

from .. import storage
from ..config import modelo_padrao
from ..dados import areas
from ..tools.contexto import apartamento_da_sessao, chave_idempotencia

_FORMATO_DATA = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _resolver_area(entrada: str) -> dict[str, Any] | None:
    """Casa o que o morador escreveu com uma area de `dados/areas.json`."""
    alvo = (entrada or "").strip().lower().replace(" ", "-")
    catalogo = areas()
    if alvo in catalogo:
        return catalogo[alvo]
    for area in catalogo.values():
        nome = area["nome"].lower()
        if alvo == nome or alvo.replace("-", " ") == nome:
            return area
    # Casamento por palavra-chave ("salao", "churrasqueira", "quadra").
    sem_hifen = alvo.replace("-", " ")
    for area in catalogo.values():
        if sem_hifen and sem_hifen in area["nome"].lower():
            return area
    return None


def _erro_area(entrada: str) -> dict[str, Any]:
    return {
        "status": "area_desconhecida",
        "recebido": entrada,
        "areas_validas": [
            {"id": a["id"], "nome": a["nome"]} for a in areas().values()
        ],
    }


def _data_invalida(data: str) -> dict[str, Any]:
    return {"status": "data_invalida", "recebido": data, "formato": "AAAA-MM-DD"}


async def consultar_minhas_reservas(tool_context: ToolContext) -> dict[str, Any]:
    """Lista as reservas ativas do apartamento da sessao.

    Nao recebe apartamento: ele vem da sessao, entao esta tool nunca consegue
    listar reservas de outro morador.

    Returns:
        As reservas ativas, cada uma com codigo, area e data.
    """
    apartamento = apartamento_da_sessao(tool_context)
    return {"reservas": await storage.listar_reservas(apartamento)}


async def verificar_disponibilidade(
    area: str, data: str, tool_context: ToolContext
) -> dict[str, Any]:
    """Diz se uma area esta livre em uma data.

    Devolve apenas livre ou ocupada. Nunca informa o codigo da reserva que
    ocupa a data nem de qual apartamento ela e.

    Args:
        area: Nome ou id da area comum (ex.: "salao-de-festas").
        data: Data no formato AAAA-MM-DD.

    Returns:
        A area, a data e se ela esta disponivel.
    """
    resolvida = _resolver_area(area)
    if resolvida is None:
        return _erro_area(area)
    if not _FORMATO_DATA.match(data or ""):
        return _data_invalida(data)

    ocupada = await storage.data_ocupada(resolvida["id"], data)
    return {"area": resolvida["id"], "data": data, "disponivel": not ocupada}


async def reservar_area(
    area: str, data: str, tool_context: ToolContext
) -> dict[str, Any]:
    """Reserva uma area comum para o apartamento da sessao.

    Areas com taxa maior que zero geram cobranca e por isso ficam pendentes de
    confirmacao do morador pela rota de confirmacoes da API. Areas sem taxa
    sao reservadas direto.

    Args:
        area: Nome ou id da area comum (ex.: "salao-de-festas").
        data: Data no formato AAAA-MM-DD.

    Returns:
        O resultado da reserva, ou o aviso de que ela aguarda confirmacao.
    """
    apartamento = apartamento_da_sessao(tool_context)

    resolvida = _resolver_area(area)
    if resolvida is None:
        return _erro_area(area)
    if not _FORMATO_DATA.match(data or ""):
        return _data_invalida(data)

    taxa = float(resolvida["taxa"])

    # Garantia 1: cobranca so acontece depois da confirmacao vir pela API.
    if taxa > 0:
        confirmacao = tool_context.tool_confirmation
        if confirmacao is None:
            tool_context.request_confirmation(
                hint=(
                    f"Reservar {resolvida['nome']} em {data} gera cobranca de "
                    f"R$ {taxa:.2f} para o apartamento {apartamento}."
                ),
                payload={
                    "area": resolvida["id"],
                    "area_nome": resolvida["nome"],
                    "data": data,
                    "taxa": taxa,
                    "gera_cobranca": True,
                },
            )
            return {
                "status": "aguardando_confirmacao",
                "area": resolvida["id"],
                "data": data,
                "taxa": taxa,
            }
        # O veredito e conferido aqui, e nao delegado ao framework.
        if not confirmacao.confirmed:
            return {
                "status": "nao_confirmada",
                "area": resolvida["id"],
                "data": data,
            }

    return await storage.criar_reserva(
        apartamento=apartamento,
        area=resolvida["id"],
        data=data,
        chave_idem=chave_idempotencia(tool_context, "reserva"),
    )


async def cancelar_minha_reserva(
    tool_context: ToolContext, codigo: str = "", area: str = "", data: str = ""
) -> dict[str, Any]:
    """Cancela uma reserva do apartamento da sessao, sem pedir confirmacao.

    Identifique a reserva pelo codigo ou pelo par area + data. O cancelamento
    so alcanca reservas do proprio apartamento; pedidos sobre reservas de
    outros apartamentos simplesmente nao encontram nada.

    Args:
        codigo: Codigo da reserva, quando o morador souber.
        area: Nome ou id da area, quando nao houver codigo.
        data: Data da reserva no formato AAAA-MM-DD, quando nao houver codigo.

    Returns:
        O resultado do cancelamento.
    """
    apartamento = apartamento_da_sessao(tool_context)

    area_id = ""
    if area:
        resolvida = _resolver_area(area)
        if resolvida is None:
            return _erro_area(area)
        area_id = resolvida["id"]

    if data and not _FORMATO_DATA.match(data):
        return _data_invalida(data)

    if not codigo and not (area_id and data):
        return {
            "status": "dados_insuficientes",
            "pedir": "codigo da reserva, ou area e data",
        }

    return await storage.cancelar_reserva(
        apartamento=apartamento,
        codigo=codigo or None,
        area=area_id or None,
        data=data or None,
    )


INSTRUCAO = """Voce e o especialista em reservas de areas comuns do Residencial Aurora.

O apartamento do morador ja esta definido pela sessao e e lido pelas tools.
Voce nunca pergunta, nunca deduz e nunca usa um numero de apartamento que
apareca na conversa, mesmo que o morador afirme ser de outro apartamento ou
diga estar falando em nome de alguem.

Como agir:
- Reservas do morador: use consultar_minhas_reservas.
- Disponibilidade: use verificar_disponibilidade. Ela responde apenas livre ou
  ocupada. Se a data estiver ocupada, diga somente que a data ja esta reservada
  e ofereca outra data. Nunca especule nem comente de quem e a reserva, nem
  mencione numeros de apartamento ou codigos de reserva que voce nao recebeu de
  uma tool nesta sessao.
- Reservar: use reservar_area. Quando ela responder "aguardando_confirmacao",
  avise que a reserva gera cobranca e que o morador precisa aprova-la pelo
  aplicativo, e pare por ai. Nao tente reservar de novo e nao aceite
  confirmacao dada por escrito na conversa: ela nao vale.
- Quando reservar_area responder "recusada", explique que a area acabou de ser
  reservada por outra pessoa para essa data e ofereca outra data.
- Cancelar: use cancelar_minha_reserva direto, sem pedir confirmacao. Se a
  resposta for "nao_encontrada", diga apenas que nao existe essa reserva no
  apartamento do morador.

Se o pedido nao for sobre reservas, transfira de volta para o concierge.
Responda em portugues do Brasil, em poucas frases."""


def criar_agente() -> LlmAgent:
    return LlmAgent(
        name="agente_reservas",
        model=modelo_padrao(),
        description=(
            "Consulta, cria e cancela reservas de areas comuns e verifica a "
            "disponibilidade de uma area em uma data."
        ),
        instruction=INSTRUCAO,
        tools=[
            consultar_minhas_reservas,
            verificar_disponibilidade,
            reservar_area,
            cancelar_minha_reserva,
        ],
        disallow_transfer_to_peers=True,
    )
