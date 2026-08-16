# Indus Agent — data/ (bind mount)

Human-readable, human-edited data directory, bind-mounted into the `api` (and `toolbox`)
containers at `/data`. Backed up by the export feature (`/api/export` tar.gz) and by any
folder copy — **storage rule (locked #10)**: app-queried data → Postgres; human-edited data →
files here. No third place.

## Layout
```
data/
  soul.md          # assistant persona (hot-reloaded; created on first boot; GITIGNORED)
  soul.example.md  # distributable persona template (committed)
  docs/             # uploaded RAG documents → docs/YYYY-MM/<uuid>.<ext> (gitignored)
  research/         # generated deep-research reports *.md (gitignored)
  exports/          # backup tar.gz + retention archives (gitignored)
  notes/            # optional manual markdown notes
```

## Git note
`.gitignore` entries are **file/dir-specific** (`data/soul.md`, `data/docs`, `data/exports`,
`data/research`) — never blanket-ignore the whole dir (HP-010) so `soul.example.md` stays
tracked.