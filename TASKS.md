# GeoLume — Tarefas e próximos passos

Legenda: `[ ]` pendente · `[x]` verificado no repositório · `[?]` precisa de decisão ou validação externa.

## Estado confirmado

- [x] Worker PyQGIS headless com validação de um polígono GeoJSON.
- [x] Reprojeção para SIRGAS 2000 / UTM dentro da cobertura suportada.
- [x] Geração de `mapa.pdf`, `memorial.pdf` preliminar e `resultado.json`.
- [x] API FastAPI síncrona e assíncrona.
- [x] Redis + Celery com uma sessão QGIS por processo.
- [x] PostgreSQL para usuários, sessões e jobs.
- [x] Upload, histórico, polling, downloads e mapa Leaflet local.
- [x] Personalização opcional da prancha com cores, logo e três layouts.
- [x] Testes unitários, worker, API e Playwright versionados.
- [x] Proteções de autenticação, autorização, CSRF, limites e arquivos.

## Próxima prioridade — produto

- [ ] Demonstrar o fluxo atual a um usuário real e registrar feedback sobre entrada, mapa e PDFs.
- [ ] Confirmar requisitos profissionais do memorial antes de chamá-lo de documento final.
- [ ] Validar arquivos reais maiores e variados sem colocá-los no Git.
- [ ] Definir critérios mensuráveis para tempo de processamento e concorrência.
- [ ] Decidir se KML e Shapefile entram no MVP ou em uma etapa posterior.

## Próxima prioridade — experiência

- [ ] Melhorar acessibilidade e UX pendentes sem alterar o contrato de jobs.
- [ ] Confirmar o provedor de tiles para uso comercial antes de ativar satélite ou abrir tráfego de produção.
- [ ] Refinar estados de erro e recuperação de jobs presos.
- [ ] Separar claramente dados de demonstração de dados de cliente.

## Depois da validação do MVP

- [ ] Modelar clientes/imóveis somente após confirmar o fluxo com usuários.
- [ ] Definir autorização e multi-tenancy de produção.
- [ ] Planejar object storage, backups, HTTPS, secrets e observabilidade.
- [ ] Benchmarkar frio/quente com fixture determinística, RSS e limites do container.
- [ ] Adicionar formatos de entrada com testes de contrato específicos.

## Regras de execução

1. Não começar pelo frontend ou por infraestrutura de produção quando a decisão de produto ainda estiver aberta.
2. Cada tarefa de código deve ter escopo pequeno, teste e critério de aceitação.
3. Mudanças em PDF exigem geração real e inspeção visual.
4. Mudanças no worker QGIS exigem execução dentro do Docker.
5. Não apagar volume PostgreSQL ou artefatos existentes como forma de reset.
6. Marcar como concluída apenas após evidência real, não apenas após editar o arquivo.
