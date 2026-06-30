from pathlib import Path
from datetime import datetime
import shutil, zipfile, pytz
from app.config import BASE_DIR, DB_PATH, UPLOAD_DIR, BACKUP_DIR

# Definição do fuso horário de Brasília
TZ_SP = pytz.timezone('America/Sao_Paulo')

BACKUP_DIR.mkdir(exist_ok=True)

# Ajuste: capturando o timestamp no fuso de Brasília
stamp = datetime.now(TZ_SP).strftime('%Y%m%d_%H%M%S')

zip_path = BACKUP_DIR / f'backup_hepta_jornada_{stamp}.zip'

with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as z:
    if Path(DB_PATH).exists():
        z.write(DB_PATH, arcname='hepta_jornada.db')
    if UPLOAD_DIR.exists():
        for p in UPLOAD_DIR.rglob('*'):
            if p.is_file():
                z.write(p, arcname=str(Path('uploads') / p.relative_to(UPLOAD_DIR)))

print(f'Backup criado: {zip_path}')