# HEPTA Jornada V1.9 — Homologação Render + Supabase

Aplicação de gestão de jornada e folhas HEPTA para homologação.

## Principal ajuste da V1.9
- Aplicação preparada para hospedar no Render.
- Banco de dados externo no Supabase/PostgreSQL via `DATABASE_URL`.
- Persistência de usuários, registros, logs, folhas, aprovações e e-mails no Supabase.
- PDFs anexados também são persistidos em `file_blobs`, evitando perda em redeploy do Render.
- Mantém SQLite como fallback para execução local.

## Rodar local no Mac

```bash
cd HEPTA_Jornada_v1_9_render_supabase
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python run.py
```

Acesse:

```text
http://127.0.0.1:8000
```

## Rodar local apontando para Supabase

```bash
export DATABASE_URL='postgresql://postgres.xxxxx:SENHA@aws-0-sa-east-1.pooler.supabase.com:6543/postgres?sslmode=require'
python run.py
```

## Deploy Render + Supabase

Veja:

```text
docs/DEPLOY_RENDER_SUPABASE.md
```

## Login inicial

```text
admin@hepta.com.br
Hepta@123
```

## Importante
O Render Free pode reiniciar a aplicação e perder arquivos locais. Nesta versão, o PDF é salvo também no Supabase na tabela `file_blobs`.
