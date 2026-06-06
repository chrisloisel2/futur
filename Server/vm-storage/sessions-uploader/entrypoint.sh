#!/bin/sh
# Rend les variables d'environnement Docker disponibles pour les jobs cron
printenv | grep -v '^_=' > /etc/environment

# Lance le watcher de sessions en arrière-plan (notation BDD à réception SFTP)
python /app/SessionsWatcher.py &

exec cron -f
