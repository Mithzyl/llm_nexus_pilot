FROM mysql:8.4
# Source the script from the image, avoiding executable bind-mount differences on macOS/Linux.
COPY deployments/production/mysql-app-user.sh /docker-entrypoint-initdb.d/10-app-user.sh
RUN chmod 0644 /docker-entrypoint-initdb.d/10-app-user.sh
