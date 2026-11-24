# ProntoGlosa / Pluggy — saldos e extratos

Serviço FastAPI independente na API .38, publicado no prefixo `/app_glosas/openfinance` pelo nginx HTTPS existente. Não reiniciar nem modificar o código da API principal ou do serviço de contas a pagar. Imagem e composição próprias, reversão removendo somente a nova rota e os dois novos containers.

Escopo: contas BANK, saldo disponível e contábil quando informado, extratos paginados por conta. Sem pagamentos, cartões, investimentos, identidade, conciliação ou baixas. Token Connect solicita somente ACCOUNTS e TRANSACTIONS. Piloto inicialmente sandbox; dados reais dependem de habilitação e consentimento dos responsáveis no banco.

Autenticação JWT existente e consulta de usuário ativo com perfil TI. Cada titular recebe identificador opaco e pertence ao usuário que o cadastrou. Uma conexão só pode ser vinculada ao titular informado em clientUserId, confirmado pela API Pluggy. PF e PJ permanecem separados.

Persistência aditiva em schema PostgreSQL prontoglosa_openfinance. Webhook autenticado por segredo em cabeçalho, gravado antes do 202, deduplicado por eventId. Worker com lease, retentativas e processamento fora da requisição. Saldo anterior preservado em falha. Snapshot completo apenas após todas as páginas recebidas, para refletir exclusões. Sem confiar em links recebidos no webhook. Dinheiro armazenado como Decimal.

Credenciais Pluggy exclusivamente no servidor em arquivo 0600; nenhuma chave no código, navegador ou relatório. Se o secret existente não estiver disponível, preparar todo o serviço e solicitar ao usuário o preenchimento protegido antes da ativação final.
