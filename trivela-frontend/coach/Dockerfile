# AI coach service (built by CV/docker-compose.yml). No dependencies: Node's own http and fetch.
FROM node:22-alpine
WORKDIR /coach
COPY server.mjs ./
COPY lib/ lib/
ENV NODE_ENV=production
USER node
EXPOSE 5600
HEALTHCHECK --interval=30s --timeout=5s CMD wget -qO- http://127.0.0.1:5600/health || exit 1
CMD ["node", "server.mjs"]
