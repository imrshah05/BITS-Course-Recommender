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

Then open `http://127.0.0.1:8000`.

Gemini is optional and is used only to understand natural-language preferences and
match interests to source-backed course text. Configure it server-side before starting
the dashboard:

```bash
export GEMINI_API_KEY="your-api-key"
export GEMINI_MODEL="gemini-2.5-flash"  # optional
python3 -m backend.api
```

If `GEMINI_API_KEY` is absent, unreachable, or returns invalid structured output, the
existing deterministic intent parser and lexical matcher are used. Academic rules,
requirements, prerequisites, eligibility, and recommendation safety never use Gemini.
Environment files and keys are excluded by `.gitignore`.
