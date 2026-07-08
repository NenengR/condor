#!/bin/bash
set -e

# Create data directory
mkdir -p /app/data

# Generate config.yml if it doesn't exist
if [ ! -f /app/config.yml ]; then
    cat > /app/config.yml << EOF
servers:
  railway:
    host: ${HUMMINGBOT_API_HOST:-hummingbot-api.railway.internal}
    port: ${HUMMINGBOT_API_PORT:-8000}
    username: ${HUMMINGBOT_API_USERNAME:-admin}
    password: ${HUMMINGBOT_API_PASSWORD:-admin}

default_server: railway

admin_id: ${ADMIN_USER_ID:-0}

users: {}

server_access:
  railway:
    owner_id: ${ADMIN_USER_ID:-0}
    created_at: null
    shared_with: {}

chat_defaults:
  ${ADMIN_USER_ID:-0}: railway

version: 1
EOF
    echo "Generated config.yml"
fi

exec uv run python main.py
