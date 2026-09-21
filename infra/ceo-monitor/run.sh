#!/bin/bash
set -e
cd /opt/alpha-finance
set -a; . /opt/ceo-monitor/graph.env; set +a
/usr/bin/docker compose --env-file /etc/alpha-finance/.env cp /opt/ceo-monitor/ceo_engine.py backend:/tmp/ceo_engine.py
/usr/bin/docker compose --env-file /etc/alpha-finance/.env exec -T -e CEO_SEND=1 -e CEO_TO=aiyer@alphadirect.co.bw -e CEO_CC=pganesharajah@alphadirect.co.bw -e CEO_GRAPH_CLIENT_ID -e CEO_GRAPH_TENANT -e CEO_GRAPH_CLIENT_SECRET backend python manage.py shell < /opt/ceo-monitor/ceo_driver.py
