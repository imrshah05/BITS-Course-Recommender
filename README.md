# BITS Academic Course Recommender

A project for the Postman API Labs recruitment task that turns supplied BITS academic documents into structured data, maintains student academic profiles, identifies remaining requirements and eligible courses, and recommends academically valid courses based on natural-language preferences through a dashboard, with optional timetable and clash analysis.

```text
data/
  raw/
    handouts/
  processed/
preprocessing/
backend/
frontend/
tests/
```

Run the dashboard foundation locally with:

```bash
python3 -m backend.api
```

Then open `http://127.0.0.1:8000`. The current dashboard exposes only the static shell and foundational `/api/health` and `/api/config` endpoints.
