# CulinaryVault — Agent Instructions

Use this file for the minimum repo-specific guidance needed to work safely. For common pitfalls, state handling patterns, and debugging notes, read [PATTERNS_ANALYSIS.md](PATTERNS_ANALYSIS.md) instead of duplicating them here.

## Project Overview

RecipeSnap is a local recipe management app built with Streamlit. Use SQLite for local development and PostgreSQL in deployment. [app.py](app.py) owns the UI and business logic; [db.py](db.py) owns all database access and schema setup.

## Run the App

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Architecture

| File | Responsibility |
|------|---------------|
| [app.py](app.py) | Streamlit pages, form logic, AI/web/OCR helpers, theming |
| [db.py](db.py) | Dual SQLite/PostgreSQL access, schema creation, migrations, CRUD helpers |
| [PATTERNS_ANALYSIS.md](PATTERNS_ANALYSIS.md) | Deeper conventions and known pitfalls |

## Database Rules

- Use only the connection helpers in `db.py`; do not import database drivers directly from `app.py`.
- Local development uses the SQLite file `culinaryvault.db`; deployment uses PostgreSQL when `DATABASE_URL` is set.
- `init_db()` creates tables and applies backend-specific migrations. Add schema changes there only.
- `recipes` and `meal_plan_entries` are user-scoped. Always pass `user_id` and `is_admin` through query helpers.
- Default admin credentials are created on first run: `admin` / `admin123`.

## App Conventions

- Validate and strip user input before DB writes. Prefer `(value or "").strip()` and `.lower()` for comparisons.
- Use parameterized SQL only.
- Wrap user-facing actions in `try/except` and convert failures into `st.error()` or a safe fallback.
- Call `st.rerun()` after state mutations.
- Use unique, ID-prefixed widget keys inside loops.
- Keep Streamlit state hierarchical, such as `page`, `auth_user`, `cook_mode_*`, and `chatbot_*`.

## Women-Centric UI Guidance

- Use an inclusive, confidence-focused visual tone for women users without stereotypes or gendered assumptions.
- Keep all global styling changes inside `inject_styles()` in [app.py](app.py).
- Prefer warm, elegant palettes and high readability; enforce strong contrast for text and controls in both Streamlit light and dark themes.
- Preserve keyboard focus visibility and clear hover/active states for all interactive controls.
- Use respectful, neutral copy in labels and helpers; avoid identity assumptions in feature text.
- Keep layouts clean and responsive with existing Streamlit primitives (`st.columns`, `st.container`, `st.expander`) and avoid fixed-width UI blocks.

## Optional Features

- OCR uses RapidOCR first, then falls back to `pytesseract` if installed.
- Web recipe parsing uses `urllib` plus JSON-LD and regex fallbacks; do not add `requests` or BeautifulSoup.
- AI support is dual-provider: OpenAI or GitHub Models. The key lookup order is sidebar input, then `st.secrets["OPENAI_API_KEY"]`, then `os.getenv("OPENAI_API_KEY")`.
- Optional imports must stay guarded so the app still runs when the dependency is missing.

## Keep in Mind

- Do not introduce a web backend or ORM.
- Do not store secrets in code or on disk.
- If you need more context on a pattern, link to existing docs instead of copying them here.
