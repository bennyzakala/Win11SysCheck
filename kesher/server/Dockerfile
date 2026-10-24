FROM node:22-slim
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev
COPY server.js ./
COPY public ./public
ENV NODE_ENV=production DATA_DIR=/data PORT=3000
VOLUME /data
EXPOSE 3000
CMD ["node", "--no-warnings=ExperimentalWarning", "server.js"]
