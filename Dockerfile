FROM python:3.7.5

COPY . requirements.txt
RUN pip install -r requirements.txt
RUN mkdir -p /tg
WORKDIR /tg

