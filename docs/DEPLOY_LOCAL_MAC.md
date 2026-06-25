# Execução local no Mac

```bash
cd hepta_jornada_app_v1_8_pipeline
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
python run.py
```

Abrir no navegador:

```text
http://localhost:8000
```

Para parar:

```text
CTRL+C
```
