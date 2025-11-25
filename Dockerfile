FROM python:3.11-slim

# Установка системных зависимостей
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    make \
    && rm -rf /var/lib/apt/lists/*

# Рабочая директория
WORKDIR /app

# Копирование requirements
COPY requirements.txt .

# Установка Python зависимостей
RUN pip install --no-cache-dir -r requirements.txt

# Копирование приложения
COPY . .

# Создание директории для сессий
RUN mkdir -p sessions

# Порт
EXPOSE 8001

# Запуск
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
