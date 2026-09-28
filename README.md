> **Please read this once.** The current deployed version works best with direct interest queries. Gemini semantic interpretation is not yet reliable in deployment, so the application may use its deterministic fallback.

# BITS Course Recommender

A course recommendation system built for BITS Pilani students that combines academic requirements, course information, student history, and natural-language preferences to help students discover relevant courses.

**Live dashboard:** [bits-course-recommender.vercel.app](https://bits-course-recommender.vercel.app)

## About the Project

Choosing courses at BITS can get complicated. Apart from finding something interesting, a student also has to consider their programme, completed courses, CDCs, DELs, HUELs, OPELs, prerequisites, and other academic rules.

This project brings that information together in one system. A student can enter their academic profile, confirm courses they have completed, and describe what they want to study. The recommender searches processed BITS academic data and returns courses that match those interests while keeping academic eligibility separate from interest relevance.

The system uses the academic documents supplied for the Postman API Labs recruitment task. Course recommendations and academic relationships are derived from those sources rather than hardcoded lists.

## How It Works

```text
Raw BITS documents
        ↓
PDF extraction and preprocessing
        ↓
Structured academic datasets
        ↓
Student profile
        ↓
Academic requirement analysis
        ↓
Course discovery and eligibility checks
        ↓
Interest matching and ranking
        ↓
Recommendations
```

The supplied PDFs are processed ahead of time into JSON datasets. The recommendation service reads those datasets at runtime; it does not repeatedly send or parse the complete PDFs for every request.

The preprocessing pipeline handles the supplied:

- BITS Academic Regulations
- BITS Bulletin
- course handouts
- semester timetable

## Academic Safety

The system deliberately keeps two questions separate:

1. Does this course match what the student wants to study?
2. Is there enough source-backed academic information to verify that the student is eligible?

A course can be highly relevant to a query while still having incomplete prerequisite or eligibility evidence. In that case, the dashboard shows **Eligibility verification required** instead of assuming that the student may register for it. Missing information is never treated as permission.

## Recommendation System

Recommendations are generated dynamically from the processed data. Depending on the evidence available, the pipeline considers:

- the student's programme or programmes
- current academic year and semester
- confirmed completed courses
- remaining academic requirements
- CDC, DEL, HUEL, OPEL, and other course relationships
- available prerequisite information
- natural-language interests
- course titles, syllabus text, and topics
- source-backed academic and cross-discipline rules

Strong interest matches are shown as recommended courses. Weaker or incidental matches can be separated into related courses. Academic policy remains deterministic: semantic matching and ranking cannot override prerequisites, eligibility, or recommendation-safety checks.

## Current Gemini Limitation

The intended system uses Gemini to interpret natural-language preferences and perform semantic interest matching against structured course information. That integration is currently not functioning reliably in the deployed application, so the application falls back to deterministic parsing and lexical matching when Gemini is unavailable or returns unusable output.

The current version therefore works best with direct queries such as:

- `I am interested in AI`
- `I want robotics courses`
- `I am interested in probability`
- `I want to explore civil engineering`

More complicated requests that combine several constraints may not work as reliably. For example:

```text
Suggest an AI-related DEL with no midsemester exam and a light workload
```

The fallback is intentional: it keeps the recommendation system functional without allowing an unavailable AI service to bypass academic checks.

## Loading Time

> After clicking **Find my courses** to load recommendations, the application may take some time to return an output.

The backend queries a fairly large processed academic dataset, and the deployed serverless function may also experience a cold start. No specific response-time guarantee is made.

## Data Limitations

Prerequisite information is incomplete for many courses in the supplied documents. As a result, otherwise relevant recommendations may display **Eligibility verification required**.

This does not necessarily mean that the student cannot take the course. It means the supplied source data is insufficient for the application to prove eligibility safely.

The system retains source references and validation states where practical. Ambiguous or incomplete information remains marked for verification rather than being silently corrected or guessed.

## Data Preprocessing Pipeline

The repository contains deterministic preprocessing for:

- page-level PDF text extraction
- batch handout processing
- course identity and course-code normalization
- course metadata extraction
- syllabus and topic extraction
- instructor extraction
- evaluation-component extraction
- midsemester and comprehensive-examination extraction
- attendance and makeup-policy extraction
- prerequisite extraction
- Academic Regulations processing
- Bulletin and programme-requirement extraction
- semester-wise curriculum extraction
- academic-rule normalization and validation
- timetable extraction and structuring
- unified handout- and Bulletin-backed course catalogue construction
- dataset validation and uncertainty tracking
- structured dataset construction

Uncertain information is preserved as uncertain. The preprocessing layer does not invent missing programme relationships, prerequisites, course categories, or academic rules.

## Project Structure

```text
BITS-Course-Recommender/
├── app.py                   # Vercel WSGI entry point
├── backend/                 # Profile, requirements, eligibility, matching, and API services
├── preprocessing/           # PDF extraction and structured-data pipeline
├── data/
│   ├── raw/                 # Supplied PDFs and course handouts
│   └── processed/           # Runtime JSON datasets
├── frontend/                # Dashboard HTML, CSS, and JavaScript
├── tests/                   # Automated preprocessing, backend, and dashboard tests
├── requirements.txt         # Python dependencies
├── vercel.json              # Vercel runtime and packaging configuration
└── README.md
```

## Running Locally

Clone the repository and enter it:

```bash
git clone https://github.com/imrshah05/BITS-Course-Recommender.git
cd BITS-Course-Recommender
```

Create a virtual environment and install the dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
```

Start the dashboard:

```bash
python3 -m backend.api
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000) in a browser.

Gemini is optional. To enable the integration locally, configure the key in the server environment before starting the application:

```bash
export GEMINI_API_KEY="your-api-key"
export GEMINI_MODEL="gemini-2.5-flash"  # optional
python3 -m backend.api
```

Never commit the API key. Environment files are excluded by `.gitignore`.

## Deployment

The dashboard and Python API are deployed together on Vercel:

**Live dashboard:** [bits-course-recommender.vercel.app](https://bits-course-recommender.vercel.app)

Vercel uses `app.py` as the WSGI entry point. The committed processed datasets are bundled for runtime use; preprocessing and raw-PDF parsing do not run during each deployment.

## Current Limitations and Future Work

- make Gemini semantic parsing reliable in the deployed environment
- improve handling of complex queries with several simultaneous constraints
- expand prerequisite coverage where the supplied sources provide enough evidence
- strengthen academic eligibility decisions as more authoritative evidence becomes available
- integrate the processed timetable into clash-aware dashboard recommendations

## Final Note

Most of the work in this project sits behind the dashboard: processing hundreds of handouts, normalizing course identities, extracting academic rules, connecting requirements to courses, preserving uncertainty, building academic and eligibility engines, testing the recommendation pipeline, and deploying the complete application.

The result is functional and deliberately conservative. Gemini semantic parsing and parts of the supplied academic data still have limitations, and the dashboard reports those limits instead of hiding them or guessing.
