# Clinical Lab Data Integration — Starter Repo

A Django + React application for viewing lab results from hospital partners.

## Quick Start (Docker)

```bash
docker compose up --build
```

- Backend: http://localhost:8000
- Frontend: http://localhost:5173

The database is automatically migrated and seeded on first run.

## Local Setup (no Docker)

### Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate --run-syncdb
python manage.py seed_data
python manage.py runserver
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

## Running Tests

```bash
cd backend
pytest
```

## Project Structure

```
backend/         Django 5 + DRF API
  config/        Django settings, root URL conf
  labs/          Models, serializers, views, FHIR utilities
frontend/        React 18 + TypeScript + Vite + Tailwind
  src/
    api/         API client
    components/  React components
    utils/       Utility functions
data/            Hospital data files
```

## API Endpoints

| Endpoint | Description |
|---|---|
| `GET /api/teams/<slug>/patients/` | List patients for a team |
| `GET /api/teams/<slug>/patients/<id>/` | Patient detail with allergies and lab results |
| `GET /api/teams/<slug>/patients/<id>/lab-results/` | Paginated lab results for a patient |

## Assignment

See `assignment.md` for the full assignment description.
