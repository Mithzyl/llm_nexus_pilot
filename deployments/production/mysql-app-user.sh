#!/bin/sh
# MySQL fresh-volume initialization only. Environment passwords come from initialize.py.
# Fixed identifiers and a restricted password alphabet avoid executable SQL interpolation.
# The official entrypoint sources this file: keep strict options inside a subshell.
(
set -eu
case "${MYSQL_APP_PASSWORD:-}" in ''|*[!a-f0-9]*) exit 1 ;; esac
[ "${#MYSQL_APP_PASSWORD}" -eq 64 ] || exit 1
MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql --protocol=socket --user=root <<SQL
CREATE USER IF NOT EXISTS 'nexuspilot_app'@'%' IDENTIFIED BY '${MYSQL_APP_PASSWORD}';
GRANT SELECT, INSERT, UPDATE, DELETE ON nexuspilot.* TO 'nexuspilot_app'@'%';
SQL
)
