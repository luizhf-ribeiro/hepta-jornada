#!/usr/bin/env bash
set -e

echo "Inicializando repositório Git da HEPTA Jornada V1.9..."
git init
git add .
git commit -m "HEPTA Jornada V1.9 Render Supabase" || true
git branch -M main

echo "\nPróximos comandos:"
echo "git remote add origin https://github.com/SEU_USUARIO/hepta-jornada.git"
echo "git push -u origin main"
echo "\nNo Render, configure DATABASE_URL com a string do Supabase."
echo "Veja docs/DEPLOY_RENDER_SUPABASE.md"
