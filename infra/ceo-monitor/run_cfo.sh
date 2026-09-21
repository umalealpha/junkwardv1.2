#!/bin/bash
# CFO Omni Brief — cron entry point (CFO 2026-09-10).
#
# Mirrors run.sh, with one important difference: it needs NO graph.env. The
# CEO brief reads aiyer@ with the CEO_GRAPH_* app, whose secret lives in
# /opt/ceo-monitor/graph.env. This brief reads pganesharajah@ with the
# GRAPH_READER_* app, and those variables are ALREADY inside the backend
# container from /etc/alpha-finance/.env — verified 2026-09-10. So there is no
# new secret to hold and nothing extra to place on the host.
set -e
cd /opt/alpha-finance
/usr/bin/docker compose --env-file /etc/alpha-finance/.env cp \
    /opt/ceo-monitor/ceo_engine.py backend:/tmp/ceo_engine.py
/usr/bin/docker compose --env-file /etc/alpha-finance/.env exec -T \
    -e CFO_SEND=1 \
    -e CFO_TO=pganesharajah@alphadirect.co.bw \
    backend python manage.py shell < /opt/ceo-monitor/cfo_driver.py
