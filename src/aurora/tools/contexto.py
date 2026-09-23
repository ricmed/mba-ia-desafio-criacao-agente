"""Origem unica do apartamento usado pelas tools (Garantia 2).

O apartamento e gravado no `state` da sessao no momento da criacao dela, que
e o unico ponto do sistema em que ele e definido. Nenhuma tool de dominio
declara um parametro `apartamento`: todas chamam `apartamento_da_sessao()`.
Por isso nao existe caminho em que o modelo — convencido por uma mensagem do
morador ou por qualquer outro texto — escolha em nome de quem a acao ocorre.
"""

from __future__ import annotations

from google.adk.tools.tool_context import ToolContext

CHAVE_APARTAMENTO = "apartamento"


class SessaoSemApartamento(RuntimeError):
    """A sessao nao carrega apartamento: nenhuma acao de dominio pode rodar."""


def apartamento_da_sessao(tool_context: ToolContext) -> str:
    numero = tool_context.state.get(CHAVE_APARTAMENTO)
    if not numero:
        raise SessaoSemApartamento(
            "A sessao nao esta vinculada a um apartamento."
        )
    return str(numero)


def chave_idempotencia(tool_context: ToolContext, sufixo: str) -> str:
    """Chave estavel para uma gravacao, derivada da chamada de tool.

    Ao retomar uma invocacao, o ADK pode reexecutar a mesma chamada de tool
    (a garantia e *pelo menos uma vez*). O id da chamada nao muda nessa
    reexecucao, entao ele identifica a gravacao pretendida e o armazenamento
    reconhece a repeticao em vez de duplicar o efeito.
    """
    return f"{tool_context.function_call_id}:{sufixo}"
