"""Registro proprio das confirmacoes pendentes (Garantia 1).

A rota de confirmacoes nao pergunta ao ADK se um id esta pendente: ela
consulta esta tabela. Quem decide entre `200` e `409` e um `UPDATE`
condicional no SQLite, que so afeta uma linha ainda `pendente` da *mesma*
sessao. Isso cobre, de uma vez:

* id inexistente -> nenhuma linha afetada -> 409;
* id de outra sessao -> nenhuma linha afetada -> 409;
* reenvio de um id ja respondido -> nenhuma linha afetada -> 409;
* duas respostas simultaneas -> apenas uma vence o UPDATE.

Como a marcacao acontece *antes* de a execucao ser retomada, nao existe
caminho em que a acao rode duas vezes por causa da rota.
"""

from __future__ import annotations

import json
from typing import Any

from .storage import _conectar


async def registrar(
    session_id: str,
    confirmation_id: str,
    invocation_id: str,
    acao: str,
    detalhes: dict[str, Any],
) -> None:
    """Guarda uma confirmacao pedida pelo assistente, se ainda nao existir."""
    async with _conectar() as conexao:
        await conexao.execute(
            "INSERT OR IGNORE INTO confirmacoes"
            " (session_id, confirmation_id, invocation_id, acao, detalhes, status)"
            " VALUES (?, ?, ?, ?, ?, 'pendente')",
            (
                session_id,
                confirmation_id,
                invocation_id,
                acao,
                json.dumps(detalhes, ensure_ascii=False),
            ),
        )
        await conexao.commit()


async def pendentes(session_id: str) -> list[dict[str, Any]]:
    """Todas as confirmacoes ainda pendentes da sessao, em ordem de criacao."""
    async with _conectar() as conexao:
        cursor = await conexao.execute(
            "SELECT confirmation_id, acao, detalhes FROM confirmacoes"
            " WHERE session_id = ? AND status = 'pendente'"
            " ORDER BY criada_em, rowid",
            (session_id,),
        )
        return [
            {
                "id": linha["confirmation_id"],
                "acao": linha["acao"],
                "detalhes": json.loads(linha["detalhes"]),
            }
            for linha in await cursor.fetchall()
        ]


async def reivindicar(session_id: str, confirmation_id: str) -> dict[str, Any] | None:
    """Marca a confirmacao como respondida, de forma atomica.

    Devolve os dados da confirmacao quando a marcacao aconteceu, e `None`
    quando nao havia nada pendente com esse id nessa sessao — caso em que a
    rota responde `409` sem executar coisa alguma.
    """
    async with _conectar() as conexao:
        await conexao.execute("BEGIN IMMEDIATE")
        try:
            cursor = await conexao.execute(
                "UPDATE confirmacoes SET status = 'respondida'"
                " WHERE session_id = ? AND confirmation_id = ? AND status = 'pendente'",
                (session_id, confirmation_id),
            )
            if cursor.rowcount == 0:
                await conexao.rollback()
                return None

            cursor = await conexao.execute(
                "SELECT invocation_id, acao, detalhes FROM confirmacoes"
                " WHERE session_id = ? AND confirmation_id = ?",
                (session_id, confirmation_id),
            )
            linha = await cursor.fetchone()
            await conexao.commit()
            return {
                "invocation_id": linha["invocation_id"],
                "acao": linha["acao"],
                "detalhes": json.loads(linha["detalhes"]),
            }
        except Exception:
            await conexao.rollback()
            raise
