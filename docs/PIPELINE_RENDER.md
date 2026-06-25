# Pipeline automática de homologação — Render + GitHub

## 1. Criar repositório no GitHub
1. Acesse GitHub.
2. Crie um repositório privado chamado `hepta-jornada`.
3. Suba o conteúdo deste pacote para a branch `main`.

Comandos no Mac:

```bash
cd hepta_jornada_app_v1_8_pipeline
git init
git add .
git commit -m "Versao V1.8 homologacao com pipeline"
git branch -M main
git remote add origin https://github.com/SEU_USUARIO/hepta-jornada.git
git push -u origin main
```

## 2. Criar serviço no Render
1. Acesse Render.
2. New > Web Service.
3. Conecte o repositório GitHub.
4. Escolha Docker.
5. Start Command: não precisa preencher, o Dockerfile já define.
6. Configure variáveis SMTP se for usar e-mail.

## 3. Deploy automático
O Render pode fazer deploy automático a cada push na branch `main`.

## 4. Deploy por GitHub Actions
No Render, copie o **Deploy Hook URL**.
No GitHub:
1. Settings > Secrets and variables > Actions.
2. New repository secret.
3. Nome: `RENDER_DEPLOY_HOOK_URL`.
4. Valor: cole a URL do Deploy Hook.

A cada push na `main`, o GitHub Actions valida a aplicação e aciona o deploy.

## 5. Atenção sobre arquivos e banco
O pacote usa SQLite e uploads em diretório local.
Em plataformas gratuitas, arquivos podem ser apagados em redeploy/restart.
Para homologação simples, isso pode ser aceitável.
Para homologação real com anexos, use ambiente com disco persistente ou servidor interno.

Caminhos recomendados para ambiente com disco persistente:

```text
HEPTA_DB_PATH=/var/data/hepta_jornada.db
HEPTA_UPLOAD_DIR=/var/data/uploads
HEPTA_BACKUP_DIR=/var/data/backups
```

## 6. Produção futura
Para produção, recomenda-se migrar para PostgreSQL e armazenamento de documentos em volume persistente, NAS, S3 compatível ou servidor interno HEPTA.
