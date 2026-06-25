#!/usr/bin/env bash
set -e
mkdir -p data/uploads data/backups
docker build -t hepta-jornada:homologacao .
docker run --rm -p 8000:8000 \
  -e HEPTA_DB_PATH=/var/data/hepta_jornada.db \
  -e HEPTA_UPLOAD_DIR=/var/data/uploads \
  -e HEPTA_BACKUP_DIR=/var/data/backups \
  -v "$(pwd)/data:/var/data" \
  hepta-jornada:homologacao
