> **Please read this once.** The deployed version currently works best with simple, direct interest queries. I built Gemini support into the project, but it is not completely reliable in deployment yet, so the app may use its deterministic fallback instead.

# BITS Course Recommender

I built this course recommendation system for BITS Pilani students. It brings together academic requirements, course information, a student's academic history, and natural-language preferences to help students find courses that are relevant to them.

**Live dashboard:** [bits-course-recommender.vercel.app](https://bits-course-recommender.vercel.app)

## About the Project

Choosing courses at BITS can get complicated very quickly. Finding an interesting course is only one part of it. A student also has to think about their programme, completed courses, CDCs, DELs, HUELs, OPELs, prerequisites, and several other academic rules.

I wanted to bring all of that information into one place. A student can enter their academic profile, confirm the courses they have already completed, and describe what they would like to study. The recommender then searches the processed BITS academic data and finds relevant courses. Interest relevance and academic eligibility are kept separate throughout the process.

I built the system using the academic documents supplied for the Postman API Labs recruitment task. The recommendations and academic relationships come from those sources instead of hardcoded course lists.

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

I preprocess the supplied PDFs into JSON datasets ahead of time. When somebody uses the dashboard, the recommendation service reads those structured datasets instead of parsing hundreds of PDF pages again for every request.

The preprocessing pipeline handles the supplied:

- BITS Academic Regulations
- BITS Bulletin
- course handouts
- semester timetable

## Academic Safety

One decision I made early was to keep these two questions separate:

1. Does this course match what the student wants to study?
2. Is there enough source-backed academic information to verify that the student is eligible?

A course can be a very strong match for someone's interests while still having incomplete prerequisite or eligibility evidence. In that situation, the dashboard shows **Eligibility verification required** instead of assuming the student can register for it. I would rather show uncertainty honestly than turn missing information into permission.

## Recommendation System

The recommendations are generated dynamically from the processed data. Depending on what the source documents actually contain, the pipeline considers:

- the student's programme or programmes
- current academic year and semester
- confirmed completed courses
- remaining academic requirements
- CDC, DEL, HUEL, OPEL, and other course relationships
- available prerequisite information
- natural-language interests
- course titles, syllabus text, and topics
- source-backed academic and cross-discipline rules

Strong interest matches are shown as recommended courses, while weaker or incidental matches can be separated into related courses. The academic side stays deterministic: semantic matching and ranking are never allowed to override prerequisites, eligibility, or recommendation-safety checks.

## Current Gemini Limitation

I added Gemini to interpret natural-language preferences and match them semantically against structured course information. At the moment, this integration is not working reliably in the deployed application. When Gemini is unavailable or returns an unusable response, the app falls back to deterministic parsing and lexical matching.

Because of that, the current version works best with direct queries such as:

- `I am interested in AI`
- `I want robotics courses`
- `I am interested in probability`
- `I want to explore civil engineering`

More complicated requests that combine several constraints may not work as reliably yet. For example:

```text
Suggest an AI-related DEL with no midsemester exam and a light workload
```

I kept the fallback deliberately so the recommender still works when Gemini does not. Gemini is only used to understand interests; it never gets to bypass the academic checks.

## Loading Time

> After clicking **Find my courses** to load recommendations, the application may take some time to return an output.

The backend has to query a fairly large processed academic dataset, and the deployed serverless function can also have a cold start. It may take a little while, so there is no fixed response-time guarantee.

## Data Limitations

Prerequisite information is incomplete for many courses in the supplied documents. Because of this, even a relevant recommendation may display **Eligibility verification required**.

This does not necessarily mean the student cannot take that course. It only means the source data I was given is not enough for the application to prove eligibility safely.

Wherever practical, I preserve source references and validation states. If something is ambiguous or incomplete, it stays marked for verification rather than being silently corrected or guessed.

## Data Preprocessing Pipeline

The preprocessing work in this repository includes:

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

Uncertain information stays uncertain. The preprocessing layer does not invent missing programme relationships, prerequisites, course categories, or academic rules just to produce a cleaner-looking result.

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

Gemini is optional. To try the integration locally, configure the key in the server environment before starting the application:

```bash
export GEMINI_API_KEY="your-api-key"
export GEMINI_MODEL="gemini-2.5-flash"  # optional
python3 -m backend.api
```

Do not commit the API key. Environment files are already excluded by `.gitignore`.

## Deployment

I deployed the dashboard and Python API together on Vercel:

**Live dashboard:** [bits-course-recommender.vercel.app](https://bits-course-recommender.vercel.app)

Vercel uses `app.py` as the WSGI entry point. The processed datasets are bundled for runtime use, so the deployment does not rerun preprocessing or parse the raw PDFs each time.

## Current Limitations and Future Work

- make Gemini semantic parsing reliable in the deployed environment
- improve handling of complex queries with several simultaneous constraints
- expand prerequisite coverage where the supplied sources provide enough evidence
- strengthen academic eligibility decisions as more authoritative evidence becomes available
- integrate the processed timetable into clash-aware dashboard recommendations

## Final Note

A lot of the work in this project is not immediately visible on the dashboard. I processed hundreds of handouts, normalized course identities, extracted academic rules, connected requirements to courses, tracked uncertainty, built the academic and eligibility engines, tested the recommendation pipeline, and deployed the complete application.

The project is functional, but I have intentionally kept it conservative. Gemini semantic parsing still needs work, and parts of the supplied academic data are incomplete. The dashboard reports those limitations instead of hiding them or making up an answer.
