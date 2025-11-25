FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y gcc g++ make && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir -p sessions

COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

EXPOSE 8001

ENTRYPOINT ["/bin/sh", "entrypoint.sh"]
