FROM python:3.7.5

RUN pip install aiohttp telethon fire
RUN mkdir -p /tg
WORKDIR /tg

