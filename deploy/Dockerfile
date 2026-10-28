FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements-service.txt .
RUN --mount=type=secret,id=proxy_ca if [ -f /run/secrets/proxy_ca ]; then export PIP_CERT=/run/secrets/proxy_ca; fi; pip install --no-cache-dir -r requirements-service.txt && useradd -u 10001 -m service
COPY --chown=service:service service ./service
RUN SERVICE_TESTING=1 python -m service.manage collectstatic --noinput && mkdir -p /data/backups && chown -R service:service /data /app/service/staticfiles
USER service
EXPOSE 8000
CMD ["sh", "-c", "umask 077; python -m service.manage migrate --noinput && exec gunicorn service.wsgi:application --no-control-socket --bind 0.0.0.0:8000 --workers 2 --threads 4 --timeout 120 --access-logfile - --access-logformat '%(m)s %(s)s %(L)s' --error-logfile - --limit-request-line 4094"]
