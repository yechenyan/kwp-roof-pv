# Review application

- `backend`: Python/FastAPI package `roof-pv-review`, SQLite persistence, versioned GPKG export. No inference dependency.
- `frontend`: React/TypeScript/Vite application `@roof-pv/web-app`, Leaflet map, English and Chinese locale resources.

Build with `pnpm build` at the repository root. Serve with `uv run --package roof-pv-review roof-pv-review`. For frontend development use `pnpm dev` with the API on port 8780. Read the root documentation for exact scope, sample setup and tests.
