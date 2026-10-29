# Petrobras no Follow-Up de Glosas

O painel é exibido com `PETROBRAS_ENABLED=true` no frontend. É necessário publicar
antes a API correspondente, aplicar sua migração `20261029_063` e configurar os
segredos no backend. O frontend não recebe credenciais da operadora.

O fluxo existente dos demais convênios foi preservado. O painel Petrobras permite
consultar competência, selecionar os protocolos retornados pela operadora, importar
glosas e comparar guia/TUSS/carteira com o Oracle. Carteira é uma conferência de
beneficiário; não substitui a guia/TUSS nem é filtro do webservice de demonstrativos.

A API continua como autoridade de permissões, persistência, cruzamento e envio.
O proxy aceita apenas as rotas enumeradas, preserva autenticação e CSRF e não permite
escrita pelo perfil de leitura. Downloads exigem a mesma autorização do item.

As alterações foram verificadas com testes Django e navegador local em desktop e
mobile, usando dados sintéticos. Ainda não houve importação de demonstrativo real
com itens, migração ou deploy desta integração. Não habilitar transmissão antes
da validação do fluxo real e dos prazos junto à operadora.

Documentação da API: `docs/petrobras-tiss.md` no repositório
`hpcardio/hpc_prj_api_prontocardio` (mesma branch `feat/petrobras-tiss-followup`).
Desativar `PETROBRAS_ENABLED` retira o painel sem apagar dados persistidos.
