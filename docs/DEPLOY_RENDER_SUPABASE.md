# HEPTA Jornada V1.9 — Render + Supabase

## Objetivo
Hospedar a aplicação no Render e manter o banco de dados no Supabase/PostgreSQL para não perder a massa de dados durante a homologação.

## 1. Criar banco no Supabase
1. Acesse o Supabase.
2. Crie um projeto.
3. Vá em Project Settings > Database.
4. Copie a connection string do PostgreSQL.
5. Use a versão com SSL, preferencialmente pelo pooler, por exemplo:
   `postgresql://postgres.xxxxx:SENHA@aws-0-sa-east-1.pooler.supabase.com:6543/postgres?sslmode=require`

## 2. Subir o código no GitHub
No Mac:

```bash
git init
git add .
git commit -m "HEPTA Jornada V1.9 Render Supabase"
git branch -M main
git remote add origin https://github.com/SEU_USUARIO/hepta-jornada.git
git push -u origin main
```

## 3. Criar serviço no Render
1. New > Web Service.
2. Conecte o repositório do GitHub.
3. Selecione Docker.
4. Configure a variável obrigatória:
   - `DATABASE_URL` = connection string do Supabase.
5. Configure as variáveis SMTP se quiser envio real de e-mail.
6. Faça o deploy.

## 4. Persistência dos PDFs
No Render Free, o filesystem é efêmero. Por isso, esta versão grava o PDF anexado também na tabela `file_blobs` do Supabase. Se o arquivo local sumir em um redeploy, o download usa a cópia persistida no banco.

## 5. Primeiro acesso
- Usuário: `admin@hepta.com.br`
- Senha: `Hepta@123`

Troque a senha no primeiro acesso.

## 6. Observações de homologação
- O banco fica no Supabase.
- Logs, usuários, jornadas, aprovações, folhas e fila de e-mails ficam no PostgreSQL.
- Os PDFs ficam persistidos no banco em `file_blobs`.
- Para produção, o ideal é usar Supabase Storage ou storage interno, em vez de BLOB no banco.
