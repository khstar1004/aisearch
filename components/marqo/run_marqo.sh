#!/bin/bash

# set the default value to info and convert to lower case
export MARQO_LOG_LEVEL=${MARQO_LOG_LEVEL:-info}
MARQO_LOG_LEVEL=`echo "$MARQO_LOG_LEVEL" | tr '[:upper:]' '[:lower:]'`

# set the default host to 0.0.0.0
export MARQO_HOST=${MARQO_HOST:-"0.0.0.0"}


# set default number of workers to 1
if [ -z "${MARQO_API_WORKERS}" ]; then
  export MARQO_API_WORKERS=1
fi

if ! [[ "$MARQO_API_WORKERS" =~ ^[0-9]+$ ]] || [ "$MARQO_API_WORKERS" -lt 1 ]; then
  echo "Invalid MARQO_API_WORKERS='$MARQO_API_WORKERS'; falling back to 1" >&2
  export MARQO_API_WORKERS=1
fi

# Keep this above Haeorum's HAEORUM_BACKEND_HTTP_MAX_IDLE_SECONDS so API clients
# can rotate idle keep-alive connections before Uvicorn closes them.
export MARQO_API_KEEPALIVE_TIMEOUT=${MARQO_API_KEEPALIVE_TIMEOUT:-75}
if ! [[ "$MARQO_API_KEEPALIVE_TIMEOUT" =~ ^[0-9]+$ ]] || [ "$MARQO_API_KEEPALIVE_TIMEOUT" -lt 1 ]; then
  echo "Invalid MARQO_API_KEEPALIVE_TIMEOUT='$MARQO_API_KEEPALIVE_TIMEOUT'; falling back to 75" >&2
  export MARQO_API_KEEPALIVE_TIMEOUT=75
fi

# Compress JSON search responses when clients advertise gzip support. Set to 0
# to disable if a proxy already handles response compression.
export MARQO_API_GZIP_MINIMUM_SIZE=${MARQO_API_GZIP_MINIMUM_SIZE:-1024}
if ! [[ "$MARQO_API_GZIP_MINIMUM_SIZE" =~ ^[0-9]+$ ]]; then
  echo "Invalid MARQO_API_GZIP_MINIMUM_SIZE='$MARQO_API_GZIP_MINIMUM_SIZE'; falling back to 1024" >&2
  export MARQO_API_GZIP_MINIMUM_SIZE=1024
fi

# Start the Marqo API in the foreground so container signal handling is direct.
cd /app/marqo/src/marqo/tensor_search
exec uvicorn api:app --host "$MARQO_HOST" --port 8882 --workers "$MARQO_API_WORKERS" --timeout-keep-alive "$MARQO_API_KEEPALIVE_TIMEOUT" --log-level "$MARQO_LOG_LEVEL"
