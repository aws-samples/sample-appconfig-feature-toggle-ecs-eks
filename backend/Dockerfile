FROM public.ecr.aws/docker/library/python:3.13-slim

WORKDIR /app

# Install dependencies first to leverage layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

# Unbuffered logging
ENV PYTHONUNBUFFERED=1

EXPOSE 5000

CMD ["python", "app.py"]
