FROM python:3.13-slim

WORKDIR /app

ARG INSTALL_MSSQL_ODBC=false
ARG ACCEPT_MS_ODBC_EULA=false

RUN if [ "$INSTALL_MSSQL_ODBC" = "true" ]; then \
      if [ "$ACCEPT_MS_ODBC_EULA" != "true" ]; then \
        echo "Set --build-arg ACCEPT_MS_ODBC_EULA=true to install Microsoft ODBC Driver 18 for SQL Server."; \
        exit 1; \
      fi; \
      apt-get update \
      && apt-get install -y --no-install-recommends ca-certificates curl gnupg unixodbc unixodbc-dev \
      && . /etc/os-release \
      && curl -fsSLO "https://packages.microsoft.com/config/debian/${VERSION_ID%%.*}/packages-microsoft-prod.deb" \
      && dpkg -i packages-microsoft-prod.deb \
      && rm packages-microsoft-prod.deb \
      && apt-get update \
      && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 \
      && rm -rf /var/lib/apt/lists/*; \
    fi

COPY pyproject.toml ./
COPY commerce_ai_search ./commerce_ai_search
COPY resources ./resources
COPY legacy ./legacy

RUN pip install --no-cache-dir .

ENV PYTHONPATH=/app
ENV AISEARCH_RESOURCE_ROOT=/app/resources

EXPOSE 8000

CMD ["uvicorn", "commerce_ai_search.main:app", "--host", "0.0.0.0", "--port", "8000"]
