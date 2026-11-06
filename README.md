# VIT Bhopal FFCS Timetable Maker 🚀

A high-performance, automated timetable planner designed specifically for VIT Bhopal students under the Fully Flexible Credit System (FFCS).

![Python](https://img.shields.io/badge/Python-3.12-blue?style=for-the-badge&logo=python)
![Flask](https://img.shields.io/badge/Flask-3.x-green?style=for-the-badge&logo=flask)
![CockroachDB](https://img.shields.io/badge/CockroachDB-Serverless-purple?style=for-the-badge&logo=cockroachlabs)
![Vercel](https://img.shields.io/badge/Deployed%20on-Vercel-black?style=for-the-badge&logo=vercel)

## ✨ New Features (v2.0)

### 🧞‍♂️ Smart Auto-Generator
- **Algorithmic Scheduling**: Automatically generates valid timetables based on your selected courses.
- **Custom Preferences**:
    - **Time Mode**: Prioritize Morning, Afternoon, or Middle slots.
    - **Avoidance**: Set strict rules to avoid 8:30 AM or 7:30 PM classes.
    - **Teacher Ranking**: Rank your preferred faculties to ensure you get the best teachers.
- **Unified Strategy**: Uses advanced tiered generation to balance teacher quality with time preferences.

### 💾 Saved Timetables
- **Save Configurations**: Save your perfect timetable drafts to viewing later.
- **Preview & Apply**: View detailed previews of saved timetables and apply them with one click.
- **Management**: Delete old drafts or load them back into the main view.

### 📥 Advanced Import Options
- **HTML Import**: Directly upload the HTML file from the VIT Registration Page (VTOP).
- **CSV Import**: Power user? Upload a structured CSV file with course data.
- **AI/OCR Import**: Use AI to extract the data automatically from screenshots.
- **Manual Entry**: flexible form for single course additions.

## 🌟 Core Features

- **Automated Clash Detection**: Instantly checks if a new course conflicts with your existing schedule (Parallel Processing).
- **Google Login**: Secure authentication for students to save their timetables across devices.
- **PDF Export**: One-click download of your finalized timetable.
- **Interactive UI**:
    - **Visual Legend**: Courses color-coded for distinct visibility.
    - **Optimized Performance**: GZIP compression and parallel fetching for instant loads.
    - **Mobile Friendly**: Responsive design for planning on the go.
- **Cloud Sync**: Data persists in CockroachDB (Serverless Postgres), ensuring you never lose your plan.

## 🛠️ Technology Stack

- **Backend**: Flask (Python) with SQLAlchemy ORM.
- **Database**: CockroachDB (PostgreSQL compatible) - chosen for serverless scalability.
- **Frontend**: Vanilla JavaScript (ES6+), CSS3 (Custom Design), HTML5.
- **Hosting**: Vercel (Serverless Function adapter).
- **Analytics**: Google Analytics 4 (GA4).

## 🚀 Speed Optimizations Provided

This project has been heavily optimized for "F1-level" speed:
1.  **Parallel Imports**: Uploading multiple HTML files happens concurrently via `Promise.all`.
2.  **Database Indexing**: ownership fields (`user_id`, `guest_id`) are indexed for O(1) lookups.
3.  **GZIP Compression**: JSON responses are compressed (reducing size by ~70%).
4.  **Static Caching**: Assets are cached by the browser to minimize network requests.
5.  **Connection Pooling**: Robust `pool_pre_ping` prevents serverless timeout errors.

## 📥 Installation & Local Setup

1.  **Clone the Repository**
    ```bash
    git clone https://github.com/yourusername/ffcs-timetable.git
    cd ffcs-timetable
    ```

2.  **Install Dependencies**
    ```bash
    pip install -r requirements.txt
    ```

3.  **Environment Variables**
    Create a `.env` file in the root directory:
    ```ini
    # Database (CockroachDB or Local Postgres)
    DATABASE_URL="postgresql://user:pass@host:port/dbname?sslmode=require"

    # Google OAuth
    GOOGLE_CLIENT_ID="your-google-client-id"
    GOOGLE_CLIENT_SECRET="your-google-client-secret"
    SECRET_KEY="your-flask-secret-key"
    ```

4.  **Run the App**
    ```bash
    python app.py
    ```
    Access at `http://localhost:5000`.

## ☁️ Deployment (Vercel)

This project is configured for Vercel out-of-the-box using `vercel.json`.

1.  Install Vercel CLI: `npm i -g vercel`
2.  Deploy:
    ```bash
    vercel
    ```
3.  Add Environment Variables in Vercel Dashboard (Settings > Environment Variables).

## 🤝 Shared Course Library

Every successful import (HTML, CSV, AI or manual course import) is also copied, without any uploader
identity, into a shared library that other students can search from **Import Data → Shared Data**.

- Uploads with the same course code and the same slot, venue and faculty rows merge into one version,
  even if the course name, LTPJC, type or category was typed differently (seats are ignored).
- Ranking: one score per version. Completeness (faculty and slots compared with the fullest version) is
  worth up to 5, plus verifications (3 each) and uploads (logged-in 1, guest 0.3), which fade with a
  14-day half-life. Logged-in data beats guest data that is about as complete, but a version with only a
  slot or two loses to a much fuller one.
- Anything with no activity for 30 days is removed by the daily cron, so old semesters clear themselves.
- Admins can hide, delete or reset entries at `/admin/shared`.
- One-time backfill of recent logged-in imports: `python scripts/backfill_shared_library.py` (dry run by
  default; `--apply --live` is required to write to a production database).
- After changing how versions are matched, merge existing duplicates with
  `python scripts/remerge_shared_library.py` (same dry-run and `--apply --live` rules).

## 🔒 Security Settings

Set these in Vercel (Settings > Environment Variables):

| Variable | Why |
|---|---|
| `SECRET_KEY` | **Required.** The app refuses to start on Vercel without it. Use a long random value. |
| `CRON_SECRET` | Recommended. When set, `/api/cron/cleanup` only accepts Vercel's own cron calls. |

All user input is validated on the server, every write request needs a CSRF token, and pages send a
Content-Security-Policy and other security headers.

## 🧪 Tests

The tests always use a throwaway SQLite database and never touch the database in `.env`:

```bash
venv/bin/python -m unittest tests.test_full_app tests.test_mutual_exclusion
```

## 📊 Analytics

Google Analytics 4 is integrated. To enable it:
1. Update `templates/base.html` with your **Measurement ID** (`G-XXXXXXXX`).
2. Current ID: `G-F03ZLSX9P7`.

## 🎨 Color Palette

The app uses a carefully curated palette to distinguish courses:
- **Core**: Light Green, Sky Blue, Light Pink, Plum.
- **Distinct**: Coral, Dark Turquoise, Orchid (Added to prevent confusion).

## 🤝 Contributing

1.  Fork the repo.
2.  Create a feature branch.
3.  Submit a Pull Request.

---
*Made with ❤️ by Mehul K. Patel*
