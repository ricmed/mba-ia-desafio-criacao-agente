# Assistente do Residencial Aurora

Assistente de condomínio construído com **Google ADK 2.9.1** e exposto por uma
**API FastAPI** em `http://localhost:8000`. Pelo chat, o morador reserva áreas
comuns, cancela as próprias reservas, autoriza visitantes e tira dúvidas sobre
o regulamento interno.

O princípio que organiza o projeto: **o modelo decide o caminho, o código
decide o que é permitido.** Nenhuma das cinco garantias depende do prompt — se
uma mensagem tentar contorná-las, ela esbarra em código, não em uma instrução.

---

## Arquitetura

### Um concierge e três especialistas

```
concierge  (LlmAgent, raiz)              src/aurora/agentes/concierge.py
│  sem tools de domínio, sem regulamento nas instruções
│
├── agente_reservas                      src/aurora/agentes/reservas.py
│     consultar_minhas_reservas · verificar_disponibilidade
│     reservar_area · cancelar_minha_reserva
│
├── agente_visitantes                    src/aurora/agentes/visitantes.py
│     listar_meus_visitantes · autorizar_visitante
│
└── agente_regulamento                   src/aurora/agentes/regulamento.py
      buscar_no_regulamento
```

| Agente | Responsabilidade | Como é acionado | Por quê |
|---|---|---|---|
| `concierge` | Recebe o morador, entende o pedido e encaminha. Não tem tool de domínio nenhuma. | Recebe toda mensagem que entra pela API. | Concentrar roteamento num agente sem tools deixa o prompt dele curto e estável, e garante que nenhuma ação sobre reservas ou visitantes passe por ele. Também é o que mantém o regulamento fora das instruções do agente principal (Garantia 4). |
| `agente_reservas` | Lê e grava reservas; verifica disponibilidade. | Transferência (`transfer_to_agent`) a partir do concierge. | Agrupa as quatro tools que tocam a tabela de reservas. Concentrar aqui a regra de taxa (`reservar_area`) evita que a decisão "isso gera cobrança?" se espalhe. |
| `agente_visitantes` | Lê e grava autorizações de visita. | Transferência a partir do concierge. | Autorizar visitante é a única ação que libera acesso físico ao prédio; separá-la deixa a regra "sempre confirma" isolada e óbvia. |
| `agente_regulamento` | Responde dúvidas sobre as normas. | Transferência a partir do concierge. | Isola o único agente que vê texto do regulamento. Seu prompt manda sempre consultar a tool antes de responder, então ele não improvisa regra. |

**Por que transferência e não `AgentTool`.** Um especialista embrulhado em
`AgentTool` não propaga o *pause* de confirmação para quem o chamou
([adk-python#7245](https://github.com/google/adk-python/issues/7245)): o
pedido de confirmação se perde e a ação nunca executa. Com `sub_agents`, o
`Runner` encontra o agente que emitiu a chamada de confirmação (`_find_agent_to_run`,
primeira regra) e entrega a resposta a ele. Os especialistas usam
`disallow_transfer_to_peers=True`: quem roteia é o concierge.

### Execução

`src/aurora/agentes/__init__.py` monta o `App` com
`ResumabilityConfig(is_resumable=True)` e o `Runner` com
`SqliteSessionService`. A resumabilidade é o que permite retomar uma invocação
que parou esperando confirmação; a sessão em SQLite é o que faz isso continuar
valendo depois de reiniciar a API.

### Armazenamento

Dois bancos SQLite, ambos em `var/` (fora do Git), sem nenhum serviço externo:

| Banco | Conteúdo | Quem escreve |
|---|---|---|
| `var/condominio.db` | reservas, visitantes, códigos já usados, confirmações, vínculo sessão→apartamento | `src/aurora/storage.py` |
| `var/sessoes.db` | sessões e eventos do ADK | `SqliteSessionService` |

Os arquivos de `dados/` são o estado inicial e nunca são escritos: são lidos
em `src/aurora/dados.py` e servem de catálogo (apartamentos, áreas) e de fonte
para o comando de restauração.

---

## Garantias

### Garantia 1 — cobrança ou acesso só com confirmação

**Onde está:** `src/aurora/agentes/reservas.py:reservar_area`,
`src/aurora/agentes/visitantes.py:autorizar_visitante`,
`src/aurora/confirmacoes.py`, `src/aurora/api/runtime.py`,
`src/aurora/api/app.py:responder_confirmacao`.

A tool decide sozinha se a ação é sensível, olhando a taxa em
`dados/areas.json` — não o que o morador escreveu:

```python
# src/aurora/agentes/reservas.py
if taxa > 0:
    confirmacao = tool_context.tool_confirmation
    if confirmacao is None:
        tool_context.request_confirmation(hint=..., payload={...})
        return {"status": "aguardando_confirmacao", ...}
    if not confirmacao.confirmed:          # o veredito é conferido aqui
        return {"status": "nao_confirmada", ...}
return await storage.criar_reserva(...)
```

`autorizar_visitante` faz o mesmo sem condição: liberar acesso **sempre** pede
confirmação. Reservar a quadra (taxa `0`) não passa por esse bloco e grava
direto.

**Por que não depende do modelo.** O caminho até `storage.criar_reserva` só
existe depois que `tool_context.tool_confirmation` chega preenchido, e ele só
chega preenchido quando o ADK retoma a invocação a partir de uma
`FunctionResponse` chamada `adk_request_confirmation`
(`src/aurora/api/runtime.py:resposta_de_confirmacao`). O ADK ignora qualquer
outro nome de função, inclusive um forjado pelo modelo. Texto na conversa
("já estou confirmando aqui") não produz essa mensagem — só a rota produz. A
conferência explícita de `confirmacao.confirmed` também blinda contra
[adk-python#7148](https://github.com/google/adk-python/issues/7148).

**Os `409`.** A rota não pergunta ao ADK se um id está pendente; ela reivindica
a confirmação num `UPDATE` condicional, **antes** de retomar coisa alguma:

```sql
-- src/aurora/confirmacoes.py:reivindicar
UPDATE confirmacoes SET status = 'respondida'
 WHERE session_id = ? AND confirmation_id = ? AND status = 'pendente'
```

`rowcount == 0` vira `409` sem que nada execute. Um único predicado cobre id
inexistente, id de outra sessão e reenvio de id já respondido.

### Garantia 2 — cada sessão pertence a um apartamento

**Onde está:** `src/aurora/api/app.py:criar_sessao`,
`src/aurora/tools/contexto.py`,
`src/aurora/agentes/__init__.py:bloquear_apartamento_do_modelo`.

O apartamento é definido **uma única vez**, em `POST /sessoes`, e vai para o
`state` da sessão:

```python
sessao = await runner.session_service.create_session(
    app_name=APP_NAME, user_id=f"apto-{numero}",
    state={CHAVE_APARTAMENTO: numero, "morador": morador},
)
```

**Nenhuma tool de domínio declara um parâmetro de apartamento.** Todas chamam
`apartamento_da_sessao(tool_context)`, que lê `tool_context.state`. Confira as
assinaturas: `consultar_minhas_reservas(tool_context)`,
`reservar_area(area, data, tool_context)`,
`cancelar_minha_reserva(tool_context, codigo, area, data)`,
`listar_meus_visitantes(tool_context)`,
`autorizar_visitante(nome, data, tool_context)`. O modelo não tem por onde
passar um apartamento, mesmo que queira.

Os `WHERE` de `storage.py` sempre filtram por esse apartamento, então um
cancelamento pedido para outra unidade simplesmente não encontra nada
(`{"status": "nao_encontrada"}`) — nada de outro morador chega à conversa nem
aos eventos.

`verificar_disponibilidade` devolve **apenas** `{"area", "data", "disponivel"}`.
A consulta correspondente, `storage.data_ocupada`, faz `SELECT 1`: o código e o
apartamento da reserva concorrente nunca saem do banco.

Como defesa em profundidade, `bloquear_apartamento_do_modelo` é registrado como
`before_tool_callback` em todos os agentes e barra qualquer argumento futuro
chamado `apartamento`/`apto`/`unidade` que divirja do valor da sessão.

### Garantia 3 — nada se perde no reinício

**Onde está:** `src/aurora/agentes/__init__.py:criar_session_service`,
`src/aurora/config.py`, `src/aurora/storage.py`.

```python
def criar_session_service() -> SqliteSessionService:
    return SqliteSessionService(str(caminho_banco_sessoes()))
```

Nada relevante vive em memória do processo. Sessões e eventos ficam em
`var/sessoes.db`; reservas, visitantes e **as confirmações pendentes** ficam em
`var/condominio.db`. Uma confirmação pedida antes do reinício continua pendente
depois dele, e `GET /sessoes/{id}/eventos` devolve os eventos gravados, na
ordem, com o conteúdo completo (`evento.model_dump(mode="json")`).

`SqliteSessionService` foi escolhido de propósito: a documentação do ADK
registra que `DatabaseSessionService` e `VertexAiSessionService` não suportam
confirmação de tools. Esta é a combinação que mantém as Garantias 1 e 3 de pé
ao mesmo tempo.

### Garantia 4 — o regulamento é consultado, não carregado

**Onde está:** `src/aurora/regulamento.py`,
`src/aurora/agentes/regulamento.py:buscar_no_regulamento`,
`src/aurora/agentes/concierge.py:INSTRUCAO`.

`dados/regulamento.md` tem 14 capítulos e ~43 mil caracteres. Ele é indexado
uma vez, **na memória do processo**, em 96 artigos, cada um marcado com o
capítulo a que pertence. `regulamento.buscar()` pontua os artigos por
sobreposição de termos (com sinônimos e peso extra para termos do título do
capítulo) e devolve **no máximo três artigos, todos do mesmo capítulo**:

```python
# src/aurora/regulamento.py:buscar
capitulo_alvo = pontuados[0][1].capitulo
do_capitulo = [par for par in pontuados if par[1].capitulo == capitulo_alvo]
selecionados = (do_capitulo or pontuados)[:_MAX_ARTIGOS]
```

**Por que não depende do modelo.** A seleção é determinística: o modelo escreve
a pergunta, mas quem escolhe o que sai do documento é a função de pontuação. O
regulamento inteiro nunca é devolvido, então nenhum evento da sessão carrega
capítulos de outros assuntos, e o histórico não engorda a cada mensagem
seguinte.

A instrução do `concierge` (`src/aurora/agentes/concierge.py:INSTRUCAO`) não
contém regulamento: ela só diz que dúvidas sobre regras vão para o
`agente_regulamento`.

### Garantia 5 — dois moradores, uma reserva

**Onde está:** `src/aurora/storage.py` — índice `ux_area_data_ativa` e
`criar_reserva`.

```sql
CREATE UNIQUE INDEX IF NOT EXISTS ux_area_data_ativa
    ON reservas (area, data) WHERE ativa = 1;
```

```python
# src/aurora/storage.py:criar_reserva
await conexao.execute("BEGIN IMMEDIATE")
...
try:
    await conexao.execute("INSERT INTO reservas ...")
except sqlite3.IntegrityError:
    await conexao.rollback()
    return {"status": "recusada", "motivo": "area_ja_reservada_nessa_data", ...}
```

**Por que não é uma conferência prévia.** A exclusividade é uma propriedade do
índice, aplicada pelo SQLite no instante do `INSERT`, dentro de uma transação
`BEGIN IMMEDIATE`. Não existe intervalo entre verificar e gravar. A reserva
perdedora volta como resultado de negócio (`recusada`), que o especialista
transforma numa resposta normal — a rota devolve `200`, não erro de servidor.

`verificar_disponibilidade` existe para conversar, não para decidir: mesmo que
o modelo pule a checagem, ou que a data fique ocupada entre a checagem e a
gravação, quem decide é o índice.

**Idempotência.** Ao retomar uma invocação, o ADK garante que uma tool roda
*pelo menos* uma vez — e pode rodar de novo. Por isso toda gravação carrega uma
`chave_idem` derivada do id da chamada de tool
(`src/aurora/tools/contexto.py:chave_idempotencia`), única no banco: uma
reexecução devolve a gravação original em vez de criar uma segunda reserva.

**Códigos.** `criar_reserva` sorteia `RSV-NNNNNN` e só o usa depois de
registrá-lo em `codigos_usados` (chave primária). Cancelar marca `ativa = 0` e
mantém a linha, então um código nunca é reaproveitado, nem o de uma reserva
cancelada.

---

## Como rodar

### Pré-requisitos

- Python 3.12 ou superior
- [uv](https://docs.astral.sh/uv/)
- Uma chave da API do Gemini, do [Google AI Studio](https://aistudio.google.com/apikey)

Não há serviço externo: o armazenamento é SQLite em arquivo, criado
automaticamente em `var/`.

### Instalação

```bash
uv sync
```

### Variáveis de ambiente

Copie o modelo e preencha a chave:

```bash
cp .env.example .env
```

| Variável | Obrigatória | Descrição |
|---|---|---|
| `GOOGLE_API_KEY` | sim | Chave do Google AI Studio. |
| `GOOGLE_GENAI_USE_VERTEXAI` | não | `FALSE` para usar a Gemini Developer API (padrão do `.env.example`). |
| `AURORA_MODELO` | não | Modelo Gemini dos agentes. Padrão: `gemini-3.6-flash`. |
| `AURORA_DB_DIR` | não | Onde ficam os bancos SQLite. Padrão: `var`. |
| `AURORA_HOST` / `AURORA_PORT` | não | Endereço da API. Padrão: `127.0.0.1:8000`. |

O `.env` não é versionado; o `.env.example` traz só os nomes.

### Restaurar os dados iniciais

```bash
uv run restaurar-dados
```

Recria reservas e visitantes a partir de `dados/*.json` e limpa as
confirmações. **As sessões e os eventos são preservados**, porque vivem em
outro banco (`var/sessoes.db`) e o histórico de conversa não faz parte do
estado do condomínio. Para começar do zero de verdade, apague a pasta `var/`.

### Subir a API

```bash
uv run aurora
```

A API responde em `http://localhost:8000`.

### Rotas

| Método | Rota | Descrição |
|---|---|---|
| `POST` | `/sessoes` | `{"apartamento": "101"}` → `201 {"session_id": "..."}` |
| `POST` | `/sessoes/{session_id}/mensagens` | `{"texto": "..."}` → `200 {"resposta", "confirmacoes_pendentes"}` |
| `POST` | `/sessoes/{session_id}/confirmacoes` | `{"id", "confirmado"}` → `200` (mesmo formato) ou `409` |
| `GET` | `/sessoes/{session_id}/eventos` | `200` — todos os eventos da sessão, em ordem |
| `GET` | `/apartamentos/{numero}/reservas` | `200` — reservas ativas |
| `GET` | `/apartamentos/{numero}/visitantes` | `200` — visitantes autorizados |

Rotas com `{session_id}` respondem `404` quando a sessão não existe. As duas
rotas de verificação leem o SQLite direto, sem passar pelo modelo.

### Exemplo rápido

```bash
SID=$(curl -s -X POST localhost:8000/sessoes -H 'Content-Type: application/json' -d '{"apartamento":"101"}' | python -c 'import sys,json;print(json.load(sys.stdin)["session_id"])')
curl -s -X POST localhost:8000/sessoes/$SID/mensagens -H 'Content-Type: application/json' -d '{"texto":"Reserve o salao de festas para 2030-04-20"}'
```

### Desenvolvimento

```bash
uv run adk web src/aurora
```

Útil para ver transferências, chamadas de tool e pedidos de confirmação
acontecendo. A API é o alvo da entrega; o `adk web` é só ferramenta de
inspeção.
