ARG BASE_IMAGE
FROM ${BASE_IMAGE}
USER root
RUN mkdir -p /opt/prontoglosa-openfinance && chown 10001:10001 /opt/prontoglosa-openfinance
WORKDIR /opt/prontoglosa-openfinance
COPY --chown=10001:10001 openfinance/ ./openfinance/
USER 10001:10001
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
ENTRYPOINT []
CMD ["python", "-m", "uvicorn", "openfinance.runtime:get_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
