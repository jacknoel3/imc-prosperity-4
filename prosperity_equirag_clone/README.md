# Prosperity Visualizer Local Clone

This folder is a mirrored local clone of `https://prosperity.equirag.com/` as fetched on April 14, 2026.

## What Is Included

- `index.html`: local entry page with relative asset paths
- `assets/index.js`: mirrored production JavaScript bundle
- `assets/index.css`: mirrored production CSS bundle
- `index.remote.html`: the unmodified upstream HTML snapshot

## Privacy Defaults

The local entry page adds a small browser-side guard:

- blocks telemetry hosts such as `posthog` and `sentry`
- blocks outbound `POST` requests to `api.prosperity.equirag.com`

That means:

- local file loading and client-side visualization should still work
- read-only upstream API features may still work if the app calls them with `GET`
- uploads or opt-in participation posts are blocked by default

## Run It

From the repo root:

```bash
python3 -m http.server 8000
```

Then open:

```text
http://localhost:8000/prosperity_equirag_clone/
```

## Notes

- This is a mirrored production build, not the original source code.
- If the upstream site changes, you may want to re-download the assets.
- If you want, we can later replace the mirrored minified bundle with a cleaner first-party local dashboard tailored to your own workflow.
