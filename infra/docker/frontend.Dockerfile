FROM node:22-alpine AS build

WORKDIR /app

ARG VITE_API_BASE_URL=/api/v1
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}

COPY frontend/web-admin/package*.json ./
RUN npm ci

COPY frontend/web-admin ./
RUN npm run build

FROM nginx:1.27-alpine

COPY infra/nginx/web.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80
