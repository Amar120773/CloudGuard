# Build the SPA, then serve it from nginx with /api proxied to the backend so
# the browser only ever talks to one origin (no CORS in production).
FROM node:22-alpine AS build

WORKDIR /app
COPY package*.json ./
RUN npm ci

COPY . .

# Where the browser should send API requests, baked in at build time.
#   ""                      same-origin /api, proxied by the nginx below
#                           (the default: API and dashboard on one host)
#   https://api.example.com dashboard hosted separately from the API; that
#                           origin must also appear in the API's CORS_ORIGINS
#
#   docker build --build-arg VITE_API_BASE_URL=https://api.example.com .
ARG VITE_API_BASE_URL=""
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL

# NOTE: never pass an API key here. Vite inlines build-time variables into the
# JS bundle, so anything set as VITE_* is readable by every visitor.
RUN npm run build

FROM nginx:1.27-alpine AS runtime
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
# 127.0.0.1, not localhost: busybox resolves localhost to ::1 first and nginx
# listens on IPv4 only, so this would fail against a perfectly healthy server.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget --spider -q http://127.0.0.1:80/ || exit 1

CMD ["nginx", "-g", "daemon off;"]
