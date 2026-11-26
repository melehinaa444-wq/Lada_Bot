FROM python:3.12-slim
WORKDIR /app
COPY bot.py /app/bot.py
ENV PYTHONUNBUFFERED=1 DATA_DIR=/data
RUN mkdir /data && chown 10001:10001 /data
USER 10001:10001
CMD ["python", "bot.py"]
