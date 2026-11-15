# Review application

- `backend`: Python/FastAPI package `roof-pv-review`, SQLite persistence, versioned GPKG export. No inference dependency.
- `frontend`: React/TypeScript/Vite application `@roof-pv/web-app`, Leaflet map, English and Chinese locale resources.

Schema v4 preserves PV-presence review and adds separately versioned MaStR identity decisions. The UI uses lightweight overviews, exact detail requests and bounded viewport geometry queries. Registry comparison includes array groups, alternatives, tied-score counts, public coordinate markers and independent evidence forms. No registry identity is automatically accepted.

Build with `pnpm build` at the repository root. Serve with `uv run --package roof-pv-review roof-pv-review`. For frontend development use `pnpm dev` with the API on port 8780. Read the root documentation for exact scope, sample setup and tests.
