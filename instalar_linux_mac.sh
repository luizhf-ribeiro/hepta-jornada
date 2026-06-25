#!/bin/bash
cd "$(dirname "$0")"
python3 -m venv venv
source venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
mkdir -p uploads backups
cp -n .env.example .env 2>/dev/null || true
echo "Instalação concluída. Execute: ./iniciar_linux_mac.sh"
