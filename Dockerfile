FROM python:3.7-alpine

RUN mkdir -p /tg
WORKDIR /tg

COPY requirements.txt requirements.txt
RUN pip install -r requirements.txt

