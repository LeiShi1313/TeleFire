FROM python:3.8-slim

RUN apt-get update && apt-get install gcc -y && apt-get clean

RUN mkdir -p /tg
WORKDIR /tg

COPY requirements.txt requirements.txt
RUN pip install -r requirements.txt

