"""Especialista em duvidas sobre o regulamento interno.

Este agente existe para que o regulamento seja *consultado*, nao carregado.
Nem ele nem o concierge recebem o documento nas instrucoes: o texto so chega
ao modelo como resultado de uma tool, ja reduzido aos artigos pertinentes.
"""

from __future__ import annotations

from typing import Any

from google.adk.agents import LlmAgent

from .. import regulamento as indice
from ..config import modelo_padrao


def buscar_no_regulamento(pergunta: str) -> dict[str, Any]:
    """Busca no regulamento interno os artigos que respondem a uma duvida.

    Devolve no maximo tres artigos, todos do mesmo capitulo, escolhidos por
    uma busca deterministica sobre o documento. O regulamento inteiro nunca e
    devolvido.

    Args:
        pergunta: A duvida do morador, com as palavras dele.

    Returns:
        Os artigos pertinentes, cada um com capitulo, identificacao e texto.
    """
    encontrados = indice.buscar(pergunta)
    if not encontrados:
        return {
            "status": "sem_resultado",
            "capitulos_disponiveis": indice.capitulos(),
        }
    return {"status": "ok", "artigos": encontrados}


INSTRUCAO = """Voce e o especialista no regulamento interno do Residencial Aurora.

Voce nao sabe o conteudo do regulamento de cor: sempre chame
buscar_no_regulamento antes de responder, passando a duvida do morador com as
palavras dele.

Responda apenas com o que estiver nos artigos devolvidos pela tool, citando o
artigo em que a resposta se apoia (por exemplo, "Art. 22"). Seja direto: se a
pergunta e sobre um horario, comece pelo horario. Nunca invente regra, prazo,
valor ou horario que nao esteja no texto devolvido. Se a tool devolver
"sem_resultado", diga que nao localizou a regra e ofereca os capitulos listados.

Nao reproduza artigos inteiros nem trate de assuntos alem do que foi
perguntado. Se o pedido nao for uma duvida sobre o regulamento, transfira de
volta para o concierge.

Responda em portugues do Brasil, em poucas frases."""


def criar_agente() -> LlmAgent:
    return LlmAgent(
        name="agente_regulamento",
        model=modelo_padrao(),
        description=(
            "Responde duvidas sobre as regras do condominio consultando o "
            "regulamento interno: horarios, proibicoes, deveres e penalidades."
        ),
        instruction=INSTRUCAO,
        tools=[buscar_no_regulamento],
        disallow_transfer_to_peers=True,
    )
