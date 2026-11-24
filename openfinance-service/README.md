# ProntoGlosa Open Finance

Somente contas bancárias, saldos e extratos. Piloto sandbox. Não inicia pagamentos nem baixa títulos e não altera o histórico dos módulos financeiros.

Serviço separado no servidor API .38; prefixo público `/app_glosas/openfinance`. PostgreSQL em schema próprio, role própria e acesso SELECT apenas às cinco colunas necessárias de `usuarios_api`. Sem conexão Oracle. Mesma autenticação JWT da API existente, restrita a usuários ativos TI ou com contas_pagar_gestao; cada usuário só acessa titulares que cadastrou.

## Endpoints

Todos exigem `Authorization: Bearer <token da API existente>`, exceto `/health` e `/webhook`:
- `POST /owners`: `{"label":"Hospital — empresa X", "kind":"PJ"}` (ou PF).
- `GET /owners`: lista titulares do usuário.
- `POST /owners/{id}/connect-token`: token temporário do Pluggy Connect, solicitando ACCOUNTS e TRANSACTIONS. Não registrar esse retorno em logs.
- `POST /owners/{id}/connections`: `{"item_id":"UUID retornado pelo widget"}`. Confere clientUserId diretamente na Pluggy e agenda sincronização. A mesma vinculação também ocorre quando chega o webhook.
- `GET /owners/{id}/accounts`: saldos disponíveis e contábeis (null se não informado), situação da conexão, horário da última coleta e erro.
- `GET /accounts/{id}/transactions?date_from=2026-10-01&date_to=2026-10-28&page=1&page_size=100`: extrato local, ordenação estável, paginação até 500 e valores decimais em strings. Pendentes mantêm status PENDING e não devem ser somados como lançamentos efetivados.
- `GET /status`: situação da fila, somente usuário autorizado, com contagem dos próprios titulares.
- `POST /webhook`: autenticação exclusiva pelo cabeçalho `X-ProntoGlosa-Webhook-Token`, persistência e retorno 202. Corpo máximo 64 KiB.

Frontend: integrar Pluggy Connect no fluxo autenticado, obtendo Connect Token do backend, usando callback de sucesso para vincular o item. Respeitar `sandbox_only` na configuração do widget e mostrar status/horário junto dos saldos; contas reais continuam bloqueadas até liberação explícita. Não incluir Client Secret ou API Key em HTML/JS. A tela Django foi entregue posteriormente em Financeiro → Saldos e extratos.

## Processamento

Eventos deduplicados por eventId, gravados antes do 202; retentativas com backoff até oito tentativas. Worker único, com lease de uma hora para recuperação após interrupção. Falhas não substituem dados anteriores por zero. Sincronização completa atômica, somente após todas as páginas V2; movimentos ausentes ficam inativos, preservando histórico. Conexões excluídas recebem tombstone e não são reativadas por eventos atrasados. Consulta automática das conexões conhecidas a cada seis horas recupera notificações perdidas; isso não força atualização no banco.

Não seguir links de payloads. Somente host fixo `api.pluggy.ai`; cursor validado por conta. Dados brutos de outros produtos nunca são armazenados. Identificação do titular permanece separada por PF/PJ e usuário; o rótulo cadastrado não equivale à validação jurídica da titularidade no banco.

## Configuração e operação

Arquivo protegido no servidor contém OPENFINANCE_DATABASE_URL, SECRET_KEY, ALGORITHM, AUTH_SCHEMA, PLUGGY_CLIENT_ID, PLUGGY_CLIENT_SECRET, WEBHOOK_SECRET e SANDBOX_ONLY=true. Não versionar nem publicar. O certificado HTTPS existente e seu fluxo de renovação são reutilizados.

`python -m openfinance.runtime` cria apenas as tabelas do novo schema previamente provisionado. API e worker usam a mesma imagem. O segredo PRONTOGLOSA_FRONTEND_KEY permite compatibilidade privada com o JWT legado somente pelo frontend servidor; acesso direto continua exigindo expiração. O comando de registro dos webhooks usa API Pluggy e cabeçalho secreto, separadamente da API pública. A ausência do Client Secret não impede receber/persistir eventos, mas impede sincronizar e emitir Connect Tokens.

Verificar `/health`, `/status`, estado failed/pending da fila e logs do worker. Alertas de worker não contêm payloads ou segredos. Planejar retenção e backup do schema novo no backup PostgreSQL existente antes de usar contas reais.

## Reversão

Remover somente o bloco nginx identificado `ProntoGlosa Open Finance`, executar `nginx -t`, recarregar nginx e parar os dois containers dedicados. Desabilitar os webhooks correspondentes na Pluggy. Preservar schema, role e arquivos de configuração para investigação/retorno. Nunca restaurar integralmente um nginx antigo sobre alterações concorrentes; comparar e remover apenas o bloco desta integração. API principal e contas a pagar não são recriadas.

## Fontes
- https://docs.pluggy.ai/en/docs/authentication
- https://docs.pluggy.ai/en/docs/developer-tools/webhooks-ref
- https://docs.pluggy.ai/en/reference/basic-concepts
- https://docs.pluggy.ai/openapi/pluggy-api.json
