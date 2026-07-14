FROM python:3.13-slim

WORKDIR /app

# Instalar dependências primeiro para aproveitar o cache de camadas
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

# Configuração para logging sem buffering
ENV PYTHONUNBUFFERED=1

EXPOSE 5000

CMD ["python", "app.py"]
