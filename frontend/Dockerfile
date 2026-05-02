# Stage 1: 构建
FROM node:20-alpine AS builder
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

# Stage 2: Nginx 服务
FROM nginx:alpine
COPY --from=builder /app/dist /usr/share/nginx/html
# SPA 路由：所有 404 回退到 index.html（Vue Router history 模式）
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
