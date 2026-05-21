# Garrinha Finance Backend

Backend financeiro para dashboard pessoal, usando dados do **Open Finance Brasil** via **Pluggy/MeuPluggy**.

## Stack

- **Python 3.11+** com **FastAPI**
- **SQLite** (banco local, sem precisar de servidor externo)
- **httpx** para chamadas à API Pluggy
- Sync automático em background a cada 6h

## Setup

```bash
cd finance-backend
python -m venv venv
source venv/bin/activate   # Linux/Mac
# venv\Scripts\activate    # Windows

pip install -r requirements.txt
```

## Configuração

Copie o `.env.example` e preencha suas credenciais da Pluggy:

```bash
cp .env.example .env
# Edite .env com suas credenciais
```

Credenciais obtidas em [dashboard.pluggy.ai](https://dashboard.pluggy.ai).

### Variáveis de Ambiente

| Variável | Obrigatório | Descrição |
|----------|-------------|-----------|
| `PLUGGY_CLIENT_ID` | ✅ | Client ID do Dashboard Pluggy |
| `PLUGGY_CLIENT_SECRET` | ✅ | Client Secret do Dashboard Pluggy |
| `AUTO_SYNC` | ❌ | Iniciar sync automático (default: `true`) |
| `SYNC_INTERVAL_HOURS` | ❌ | Intervalo do sync automático em horas (default: `6`) |

> ⚠️ As credenciais atuais são de desenvolvimento/teste. Para produção, crie credenciais dedicadas.

## Executar

```bash
uvicorn main:app --reload --port 8000
```

Ou via script Python:

```bash
python main.py
```

A API estará disponível em `http://localhost:8000`.

### Documentação Interativa

- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`

## Endpoints

### Status e Sincronia

| Método | Rota | Descrição |
|--------|------|-----------|
| `GET` | `/api/status` | Status do sistema, última sincronia, contagens |
| `GET` | `/api/sync/status` | Status detalhado da sincronia |
| `POST` | `/api/sync` | Dispara sincronia manual (`?lookback_days=90`) |
| `GET` | `/api/sync/logs` | Histórico de sincronias (`?limit=10`) |

### Contas

| Método | Rota | Descrição |
|--------|------|-----------|
| `GET` | `/api/accounts` | Lista todas as contas com saldos |
| `GET` | `/api/accounts/{id}` | Detalhe de uma conta específica |

### Transações

| Método | Rota | Descrição |
|--------|------|-----------|
| `GET` | `/api/transactions` | Transações com filtros e paginação |
| `GET` | `/api/transactions/summary` | Resumo de receitas/despesas do período |

**Parâmetros de filtro (transactions):**

| Parâmetro | Descrição |
|-----------|-----------|
| `date_from` | Data inicial (YYYY-MM-DD, default: 30 dias atrás) |
| `date_to` | Data final (YYYY-MM-DD, default: hoje) |
| `account_id` | Filtrar por conta |
| `category` | Filtrar por categoria |
| `type` | `CREDIT` (receita) ou `DEBIT` (despesa) |
| `page` | Número da página (default: 1) |
| `page_size` | Itens por página (default: 50, max: 500) |

### Categorias

| Método | Rota | Descrição |
|--------|------|-----------|
| `GET` | `/api/categories` | Gastos agregados por categoria |

### Investimentos

| Método | Rota | Descrição |
|--------|------|-----------|
| `GET` | `/api/investments` | Lista todos os investimentos |

## Exemplos de Uso

```bash
# Status do sistema
curl http://localhost:8000/api/status

# Disparar sync manual
curl -X POST http://localhost:8000/api/sync

# Listar contas
curl http://localhost:8000/api/accounts

# Transações dos últimos 30 dias
curl "http://localhost:8000/api/transactions?date_from=2026-04-21&date_to=2026-05-21"

# Resumo do período
curl "http://localhost:8000/api/transactions/summary?date_from=2026-04-01&date_to=2026-05-21"

# Gastos por categoria
curl "http://localhost:8000/api/categories?date_from=2026-01-01"

# Investimentos
curl http://localhost:8000/api/investments
```

## Estrutura do Projeto

```
finance-backend/
├── main.py              # FastAPI app + endpoints
├── database.py          # SQLite config + models
├── pluggy_client.py     # Cliente HTTP da API Pluggy
├── sync_service.py      # Serviço de sincronização
├── models.py            # Pydantic models (schemas)
├── requirements.txt     # Dependências
├── .env.example         # Exemplo de variáveis de ambiente
├── .pluggy-items.json   # Itens salvos (criado automaticamente)
└── finance.db           # Banco SQLite (criado automaticamente)
```

## Integração com Frontend

O backend está configurado com CORS para aceitar requests de:

- `http://localhost:5173` (Vite dev server)
- `http://localhost:3000`
- `http://127.0.0.1:5173`
- `http://127.0.0.1:3000`

Para adicionar outras origens, edite `allow_origins` no arquivo `main.py`.

## Fluxo de Autorização

Se o Item do MeuPluggy não estiver autorizado, a API retornará uma URL de OAuth. O usuário precisa:

1. Abrir a URL retornada pelo `POST /api/sync`
2. Autorizar o acesso no MeuPluggy
3. Executar o sync novamente

O Item ID fica salvo em `.pluggy-items.json` na raiz do projeto Garrinha.

## Erros Comuns

| Problema | Solução |
|----------|---------|
| `PLUGGY_CLIENT_ID não configurado` | Exportar variáveis de ambiente ou criar `.env` |
| `Item precisa de autorização OAuth` | Abrir URL retornada, autorizar, tentar sync novamente |
| `Rate limit exceeded` | Aguardar alguns segundos e tentar novamente |
| Item não encontrado | Verificar se o Item existe e está com status `UPDATED` |

## Licença

Uso pessoal — parte do projeto Garrinha.