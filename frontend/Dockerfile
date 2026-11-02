# syntax=docker/dockerfile:1
# DEV image (Vite dev server). The production image belongs to a later sub-project.
# Build context is frontend/: docker build frontend
FROM node:22.23.4-slim
RUN npm install -g pnpm@12.16.1
WORKDIR /app
RUN chown node:node /app
USER node
COPY --chown=node:node package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY --chown=node:node . .
EXPOSE 5173
CMD ["pnpm", "dev", "--host", "0.0.0.0", "--port", "5173"]
