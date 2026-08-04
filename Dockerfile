FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy ALL modular python files and project files into the container
COPY . .

CMD ["python", "-u", "app.py"]