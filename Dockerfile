FROM node:22-slim AS frontend
WORKDIR /web
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN npm install -g pnpm@11.25.0 && pnpm install --frozen-lockfile
COPY frontend/ .
RUN mkdir -p public/pdf-assets && cp -R node_modules/pdfjs-dist/cmaps node_modules/pdfjs-dist/standard_fonts node_modules/pdfjs-dist/wasm public/pdf-assets/ && cp node_modules/pdfjs-dist/LICENSE public/pdf-assets/LICENSE.pdfjs && pnpm exec tsc -b && pnpm exec vite build

FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends fonts-wqy-microhei && rm -rf /var/lib/apt/lists/*
COPY requirements.lock.txt ./
RUN pip install --no-cache-dir -r requirements.lock.txt && useradd -u 10001 -m qiang
COPY backend/ ./backend/
COPY scripts/ ./scripts/
COPY --from=frontend /web/dist ./frontend/dist/
RUN mkdir -p /app/data && chown -R qiang:qiang /app
USER qiang
ENV QIANG_HOST=0.0.0.0
EXPOSE 8000
VOLUME ["/app/data"]
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health')"
CMD ["python", "scripts/start.py"]
