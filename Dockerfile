FROM python:3.12-slim

WORKDIR /app

# Dependências primeiro (aproveita cache do Docker)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Código e página
COPY main.py .
COPY gerar_json.py .
COPY frota_routes.py .
COPY static/ ./static/

# A porta interna do serviço
EXPOSE 80

# Sobe o FastAPI na porta 80 do container
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "80"]
