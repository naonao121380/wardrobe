FROM node:22-alpine AS build

ARG NPM_REGISTRY=https://registry.npmmirror.com

WORKDIR /app

COPY package.json package-lock.json* ./
RUN npm config set registry "${NPM_REGISTRY}" \
    && npm install --no-audit --no-fund

COPY tsconfig.json tsconfig.node.json vite.config.ts index.html ./
COPY src ./src
# ⚠️ public/ 必须一起 COPY：vite 构建时会把 public/ 下的文件原样拷进 dist/，
# 少了这行镜像里就没有 favicon.svg。而 nginx 的 SPA 回退会让 /favicon.svg
# 返回 index.html（200 + text/html），浏览器只在控制台留一句 MIME 警告，
# 表现就是「图标没了」但接口一切正常 —— 这个 bug 因此藏了整整一版。
COPY public ./public
RUN npm run build


FROM nginx:1.27-alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80
