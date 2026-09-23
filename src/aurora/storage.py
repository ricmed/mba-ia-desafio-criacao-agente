"""Armazenamento do estado mutavel do condominio, em SQLite.

Duas propriedades deste modulo sao garantias do desafio e nao dependem de
nada que o modelo decida:

* **Exclusividade da reserva** (Garantia 5): o indice parcial unico
  ``ux_area_data_ativa`` recusa, no instante do ``INSERT``, uma segunda
  reserva ativa para a mesma area na mesma data. Nao existe janela entre
  conferir e gravar.
* **Idempotencia** (Garantia 1 + retomada do ADK): toda gravacao carrega uma
  ``chave_idem`` unica derivada do id da chamada de tool. Como o ADK garante
  que uma tool roda *pelo menos* uma vez ao retomar uma invocacao, repetir a
  execucao devolve a gravacao original em vez de duplicar o efeito.
"""

from __future__ import annotations

import secrets
import sqlite3
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import aiosqlite

from .config import caminho_banco_condominio

_SCHEMA = """
CREATE TABLE IF NOT EXISTS reservas (
    codigo      TEXT PRIMARY KEY,
    apartamento TEXT NOT NULL,
    area        TEXT NOT NULL,
    data        TEXT NOT NULL,
    ativa       INTEGER NOT NULL DEFAULT 1,
    chave_idem  TEXT UNIQUE
);

-- Garantia 5: no maximo uma reserva ATIVA por area/data, validado pelo banco.
CREATE UNIQUE INDEX IF NOT EXISTS ux_area_data_ativa
    ON reservas (area, data) WHERE ativa = 1;

-- Um codigo usado nunca volta a ficar disponivel, nem apos cancelamento.
CREATE TABLE IF NOT EXISTS codigos_usados (
    codigo TEXT PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS visitantes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    apartamento TEXT NOT NULL,
    nome        TEXT NOT NULL,
    data        TEXT NOT NULL,
    chave_idem  TEXT UNIQUE
);

-- Vinculo sessao -> apartamento. E gravado na criacao da sessao e nunca
-- reescrito: e a origem do apartamento que as tools usam (Garantia 2).
CREATE TABLE IF NOT EXISTS sessoes (
    session_id  TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    apartamento TEXT NOT NULL,
    criada_em   TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Registro proprio das confirmacoes pendentes: e ele, e nao o ADK, que
-- decide se a rota de confirmacoes responde 200 ou 409 (Garantia 1).
CREATE TABLE IF NOT EXISTS confirmacoes (
    session_id      TEXT NOT NULL,
    confirmation_id TEXT NOT NULL,
    invocation_id   TEXT NOT NULL,
    acao            TEXT NOT NULL,
    detalhes        TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pendente',
    criada_em       TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (session_id, confirmation_id)
);
"""


@asynccontextmanager
async def _conectar() -> AsyncIterator[aiosqlite.Connection]:
    conexao = await aiosqlite.connect(caminho_banco_condominio(), timeout=30.0)
    try:
        conexao.row_factory = aiosqlite.Row
        await conexao.execute("PRAGMA journal_mode = WAL")
        await conexao.execute("PRAGMA busy_timeout = 15000")
        yield conexao
    finally:
        await conexao.close()


async def inicializar() -> None:
    async with _conectar() as conexao:
        await conexao.executescript(_SCHEMA)
        await conexao.commit()


async def restaurar(
    reservas: list[dict[str, Any]], visitantes: list[dict[str, Any]]
) -> None:
    """Volta reservas e visitantes ao estado dos arquivos de `dados/`."""
    async with _conectar() as conexao:
        await conexao.executescript(_SCHEMA)
        await conexao.execute("DELETE FROM reservas")
        await conexao.execute("DELETE FROM codigos_usados")
        await conexao.execute("DELETE FROM visitantes")
        await conexao.execute("DELETE FROM confirmacoes")
        for reserva in reservas:
            await conexao.execute(
                "INSERT INTO reservas (codigo, apartamento, area, data, ativa)"
                " VALUES (?, ?, ?, ?, 1)",
                (
                    reserva["codigo"],
                    reserva["apartamento"],
                    reserva["area"],
                    reserva["data"],
                ),
            )
            await conexao.execute(
                "INSERT INTO codigos_usados (codigo) VALUES (?)", (reserva["codigo"],)
            )
        for visitante in visitantes:
            await conexao.execute(
                "INSERT INTO visitantes (apartamento, nome, data) VALUES (?, ?, ?)",
                (visitante["apartamento"], visitante["nome"], visitante["data"]),
            )
        await conexao.commit()


# --------------------------------------------------------------------------
# Sessoes
# --------------------------------------------------------------------------


async def registrar_sessao(session_id: str, user_id: str, apartamento: str) -> None:
    """Vincula a sessao ao apartamento. Chamado uma unica vez, na criacao."""
    async with _conectar() as conexao:
        await conexao.execute(
            "INSERT OR IGNORE INTO sessoes (session_id, user_id, apartamento)"
            " VALUES (?, ?, ?)",
            (session_id, user_id, apartamento),
        )
        await conexao.commit()


async def buscar_sessao(session_id: str) -> dict[str, str] | None:
    async with _conectar() as conexao:
        cursor = await conexao.execute(
            "SELECT session_id, user_id, apartamento FROM sessoes WHERE session_id = ?",
            (session_id,),
        )
        linha = await cursor.fetchone()
        return dict(linha) if linha is not None else None


# --------------------------------------------------------------------------
# Reservas
# --------------------------------------------------------------------------


async def listar_reservas(apartamento: str) -> list[dict[str, str]]:
    async with _conectar() as conexao:
        cursor = await conexao.execute(
            "SELECT codigo, area, data FROM reservas"
            " WHERE apartamento = ? AND ativa = 1 ORDER BY data, area",
            (apartamento,),
        )
        return [dict(linha) for linha in await cursor.fetchall()]


async def data_ocupada(area: str, data: str) -> bool:
    """Responde apenas se a data esta ocupada, nunca de quem e a reserva."""
    async with _conectar() as conexao:
        cursor = await conexao.execute(
            "SELECT 1 FROM reservas WHERE area = ? AND data = ? AND ativa = 1",
            (area, data),
        )
        return await cursor.fetchone() is not None


def _novo_codigo() -> str:
    return f"RSV-{secrets.randbelow(900_000) + 100_000}"


async def criar_reserva(
    apartamento: str, area: str, data: str, chave_idem: str
) -> dict[str, Any]:
    """Grava a reserva ou a recusa, de forma atomica.

    Devolve sempre um resultado normal (`criada` ou `recusada`): uma disputa
    perdida nao e um erro, e uma resposta de negocio.
    """
    async with _conectar() as conexao:
        await conexao.execute("BEGIN IMMEDIATE")
        try:
            cursor = await conexao.execute(
                "SELECT codigo, area, data FROM reservas WHERE chave_idem = ?",
                (chave_idem,),
            )
            existente = await cursor.fetchone()
            if existente is not None:
                # Retomada do ADK reexecutou a tool: devolve a gravacao original.
                await conexao.rollback()
                return {
                    "status": "criada",
                    "codigo": existente["codigo"],
                    "area": existente["area"],
                    "data": existente["data"],
                    "repetida": True,
                }

            codigo = _novo_codigo()
            while True:
                cursor = await conexao.execute(
                    "INSERT OR IGNORE INTO codigos_usados (codigo) VALUES (?)",
                    (codigo,),
                )
                if cursor.rowcount == 1:
                    break
                codigo = _novo_codigo()

            try:
                await conexao.execute(
                    "INSERT INTO reservas"
                    " (codigo, apartamento, area, data, ativa, chave_idem)"
                    " VALUES (?, ?, ?, ?, 1, ?)",
                    (codigo, apartamento, area, data, chave_idem),
                )
            except sqlite3.IntegrityError:
                # O indice unico barrou: outra reserva ativa ja ocupa area/data.
                await conexao.rollback()
                return {
                    "status": "recusada",
                    "motivo": "area_ja_reservada_nessa_data",
                    "area": area,
                    "data": data,
                }

            await conexao.commit()
            return {"status": "criada", "codigo": codigo, "area": area, "data": data}
        except Exception:
            await conexao.rollback()
            raise


async def cancelar_reserva(
    apartamento: str,
    codigo: str | None = None,
    area: str | None = None,
    data: str | None = None,
) -> dict[str, Any]:
    """Cancela uma reserva do proprio apartamento.

    O `apartamento` vem sempre da sessao. Reservas de outros apartamentos nem
    sao alcancadas pelo WHERE, entao nao ha o que vazar para a conversa.
    """
    async with _conectar() as conexao:
        await conexao.execute("BEGIN IMMEDIATE")
        try:
            if codigo:
                filtro, valores = "codigo = ?", (codigo,)
            elif area and data:
                filtro, valores = "area = ? AND data = ?", (area, data)
            else:
                await conexao.rollback()
                return {"status": "dados_insuficientes"}

            cursor = await conexao.execute(
                "SELECT codigo, area, data FROM reservas"
                " WHERE apartamento = ? AND ativa = 1 AND " + filtro,
                (apartamento, *valores),
            )
            alvo = await cursor.fetchone()
            if alvo is None:
                await conexao.rollback()
                return {"status": "nao_encontrada"}

            await conexao.execute(
                "UPDATE reservas SET ativa = 0, chave_idem = NULL WHERE codigo = ?",
                (alvo["codigo"],),
            )
            await conexao.commit()
            return {
                "status": "cancelada",
                "codigo": alvo["codigo"],
                "area": alvo["area"],
                "data": alvo["data"],
            }
        except Exception:
            await conexao.rollback()
            raise


# --------------------------------------------------------------------------
# Visitantes
# --------------------------------------------------------------------------


async def listar_visitantes(apartamento: str) -> list[dict[str, str]]:
    async with _conectar() as conexao:
        cursor = await conexao.execute(
            "SELECT nome, data FROM visitantes WHERE apartamento = ?"
            " ORDER BY data, nome",
            (apartamento,),
        )
        return [dict(linha) for linha in await cursor.fetchall()]


async def autorizar_visitante(
    apartamento: str, nome: str, data: str, chave_idem: str
) -> dict[str, Any]:
    async with _conectar() as conexao:
        await conexao.execute("BEGIN IMMEDIATE")
        try:
            cursor = await conexao.execute(
                "SELECT nome, data FROM visitantes WHERE chave_idem = ?", (chave_idem,)
            )
            existente = await cursor.fetchone()
            if existente is not None:
                await conexao.rollback()
                return {
                    "status": "autorizado",
                    "nome": existente["nome"],
                    "data": existente["data"],
                    "repetida": True,
                }

            await conexao.execute(
                "INSERT INTO visitantes (apartamento, nome, data, chave_idem)"
                " VALUES (?, ?, ?, ?)",
                (apartamento, nome, data, chave_idem),
            )
            await conexao.commit()
            return {"status": "autorizado", "nome": nome, "data": data}
        except Exception:
            await conexao.rollback()
            raise
