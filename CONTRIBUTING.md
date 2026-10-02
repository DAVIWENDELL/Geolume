# Contribuindo com o GeoLume

Obrigado pelo interesse em contribuir.

## Antes de começar

1. Leia `AGENTS.md` e a documentação relevante.
2. Não inclua senhas, tokens, arquivos `.env.local` ou dados de clientes.
3. Preserve fixtures, jobs e resultados existentes.
4. Não altere contratos de API, formatos de PDF ou autenticação sem atualizar os testes e a documentação correspondente.

## Ambiente local

O ambiente de desenvolvimento usa Docker Compose:

```powershell
docker compose up -d api redis celery postgis
Invoke-RestMethod http://localhost:8000/health
```

Para o worker:

```powershell
docker compose build worker
docker compose run --rm worker
```

## Testes web

Com Node 24, Chrome e o stack local em execução:

```powershell
cd tests-web
npm run test:unit
npm run test:e2e
```

## Pull requests

- Explique o problema e a solução;
- Liste os testes executados;
- Inclua capturas ou artefatos quando houver mudança visual ou de PDF;
- Mantenha o escopo pequeno;
- Atualize a documentação relevante quando houver mudança de contrato ou decisão do projeto.
