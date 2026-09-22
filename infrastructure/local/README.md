# Local Aduoryn development

Start infrastructure:

```bash
docker compose -f infrastructure/local/docker-compose.yml up -d
```

Backend:

```bash
cd backend
cp ../infrastructure/local/.env.example .env
uv sync
uv run alembic upgrade head
uv run fastapi dev app/main.py
```

Frontend:

```bash
cd frontend
npm install
VITE_API_BASE_URL=http://localhost:8000 npm run dev
```

The local stack is PostgreSQL + Redis + MinIO. Render/Vercel are not required for development.
