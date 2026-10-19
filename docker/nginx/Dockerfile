# QuickStock JA - TLS-terminating reverse proxy
FROM nginx:1.27-alpine

RUN rm -f /etc/nginx/conf.d/default.conf

COPY docker/nginx/quickstock.conf /etc/nginx/conf.d/quickstock.conf

EXPOSE 80 443

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD wget -qO- http://127.0.0.1/nginx-health || exit 1
