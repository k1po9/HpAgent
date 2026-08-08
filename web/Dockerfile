# syntax=docker/dockerfile:1
# HpAgent Web 生产网关（Phase G G-01）。
#
# 多阶段构建：
#   Stage 1 (build): Node 20 上 `npm ci` + `npm run build` 产出 React/Vite 静态包
#   Stage 2 (runtime): Nginx 只服务静态资源并反向代理 /api + /auth 到 hpagent-api
#
# 生产环境绝不运行 `vite dev` / `npm run dev`（G-01 §6.1）。

# ---- Stage 1: 前端生产构建 ----
FROM node:20-alpine AS build
WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .
RUN npm run build

# ---- Stage 2: Nginx 运行时 ----
FROM nginx:1.27-alpine

COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/nginx.conf

EXPOSE 80
