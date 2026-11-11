#!/bin/sh
set -e

python -c "from web_service import init_db; init_db()"

. /etc/apache2/envvars
mkdir -p "$APACHE_RUN_DIR" "$APACHE_LOCK_DIR" "$APACHE_LOG_DIR"
rm -f "$APACHE_PID_FILE"
exec apache2 -D FOREGROUND
