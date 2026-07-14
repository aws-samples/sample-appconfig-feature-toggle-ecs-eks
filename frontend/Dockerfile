FROM public.ecr.aws/docker/library/python:3.13-slim

WORKDIR /app

# Install dependencies with specific versions
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Create required directories
RUN mkdir -p templates static

# Copy application code and templates
COPY app.py .
COPY templates/ ./templates/

# Expose port for web traffic
EXPOSE 80

# Unbuffered logging
ENV PYTHONUNBUFFERED=1

# Run the application
CMD ["python", "app.py"]
