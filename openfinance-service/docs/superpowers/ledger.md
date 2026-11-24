# Execution ledger

- Ruling: serviço isolado, sem alteração nos repositórios dirty ou reinício da API principal — evita interferir em desenvolvimento concorrente; publicação autorizada pelo pedido de integração e webhook HTTPS.
- Ruling: role PostgreSQL própria com schema próprio e SELECT nas quatro colunas de autenticação existentes — banco principal tinha privilégio suficiente para provisionar; serviço não recebe credenciais DBA.
- Ruling: base da imagem reaproveita digest da imagem local da API, com tag exclusiva — libs já instaladas; não reconstruir a aplicação principal nem depender de downloads durante implantação.
- Ruling: piloto sandbox, sem habilitar dados reais/contratos — credencial disponível na aplicação de Desenvolvimento; real depende do consentimento bancário e liberação Pluggy.
- Tarefa 1: oito testes vermelhos por ausência do módulo, implementação e oito verdes.
- Tarefa 2: testes de API/store vermelhos por ausência do módulo, implementação e 16 testes verdes; auth JWT e casos adicionais adicionados depois.
- Validação real: HTTPS 200; sem segredo e sem JWT 401; Pluggy autenticação aceita, nove subscriptions; sandbox 1 conta e 26 transações, quatro eventos recebidos e saldo idêntico; deduplicação pública confirmada. Nenhum registro financeiro existente foi alterado.
- Melhorias de revisão: lookup de transações em lotes (teste de 500 movimentos passou de 504 SELECTs para menos de 15); DNS Docker dinâmico no nginx para suportar recriação do container; tombstone independente para excluir antes da primeira sincronização e durante coleta; verificação do cabeçalho antes de reaproveitar webhook.
- Revisão independente requesting-code-review encontrou o tombstone ausente e o reaproveitamento de header desconhecido; ambos reproduzidos por testes vermelhos e corrigidos. Revisão não acessou rede nem segredos.
