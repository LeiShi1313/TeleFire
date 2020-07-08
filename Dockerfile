FROM python:3.8-slim

RUN mkdir -p /tg
WORKDIR /tg

COPY requirements.txt requirements.txt
RUN pip install -r requirements.txt

