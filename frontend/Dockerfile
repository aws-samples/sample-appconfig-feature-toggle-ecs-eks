FROM python:3.13-slim

WORKDIR /app

# Instalar dependências com versões específicas
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Criar diretórios necessários
RUN mkdir -p templates static

# Copiar código da aplicação e templates
COPY app.py .
COPY templates/ ./templates/

# Expor porta para o tráfego web
EXPOSE 80

# Configuração para logging
ENV PYTHONUNBUFFERED=1

# Comando para executar a aplicação
CMD ["python", "app.py"]
