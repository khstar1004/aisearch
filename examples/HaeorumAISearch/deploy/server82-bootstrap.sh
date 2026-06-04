#!/usr/bin/env bash
set -euo pipefail

DOCKER_DATA_ROOT="${HAEORUM_DOCKER_DATA_ROOT:-/home/docker}"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root." >&2
  exit 1
fi

cat >/etc/sysctl.d/99-haeorum-ai-search.conf <<'EOF'
vm.max_map_count=262144
net.ipv4.ip_forward=1
net.bridge.bridge-nf-call-iptables=1
net.bridge.bridge-nf-call-ip6tables=1
EOF

modprobe br_netfilter || true
sysctl --system

mkdir -p /etc/docker "$DOCKER_DATA_ROOT"
cat >/etc/docker/daemon.json <<EOF
{
  "data-root": "$DOCKER_DATA_ROOT",
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "20m",
    "max-file": "5"
  }
}
EOF

systemctl enable --now docker
systemctl restart docker

mkdir -p /etc/haeorum-ai-search
mkdir -p /var/log/haeorum-ai-search
chmod 750 /etc/haeorum-ai-search /var/log/haeorum-ai-search

docker info >/dev/null

echo "Docker:"
docker --version
docker compose version
docker info | egrep 'Logging Driver|Docker Root Dir'

echo "Kernel settings:"
sysctl vm.max_map_count
sysctl net.ipv4.ip_forward
sysctl net.bridge.bridge-nf-call-iptables
sysctl net.bridge.bridge-nf-call-ip6tables

