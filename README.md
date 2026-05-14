# RecipeSnap

A professional recipe management app built with Streamlit.

- Local development: SQLite (`culinaryvault.db`)
- Deployment: PostgreSQL when `DATABASE_URL` is set

## Features

- Full recipe CRUD (Create, Read, Update, Delete)
- Structured recipe fields:
  - Title
  - Description
  - Ingredients (multi-line)
  - Instructions (step-by-step)
  - Tips for best result
  - Servings
  - Preparation time
  - Cooking time
  - Difficulty level
  - Category
  - Tags
  - Optional image
- Favorites and ratings (stored per recipe)
- Photo-to-text OCR to extract recipe text from images and auto-fill form fields
- Cook Mode with automatic ingredient scaling and guided next/previous step navigation across selected recipes
- Tips preview in Cook Mode before starting cooking
- Search and filter recipes by:
  - Free-text search
  - Category
  - Difficulty
- Filter by favorites and minimum rating
- Meal planner calendar for assigning recipes to dates/meals
- Excel export and import (XLSX) for recipe backup/migration
- Local persistence with SQLite (`culinaryvault.db`)
- Clean and polished Streamlit UI

## Tech Stack

- Streamlit (frontend + app logic)
- SQLite (local storage)
- Pillow (image handling)

## Project Structure

```
CULINARYVAULT/
├── app.py
├── db.py
├── requirements.txt
└── README.md
```

## SQLite Storage Model

All recipe-related data is stored in SQLite tables:

- `recipes`
  - Core recipe fields
  - `tips_for_best_result`
  - `servings`
  - `is_favorite` flag
  - `rating` value
  - Image BLOB
- `meal_plan_entries`
  - Planned date
  - Meal type
  - Linked `recipe_id` (foreign key)
  - Optional notes

No in-memory-only recipe records are used. Create, update, planner, favorite, and rating operations all persist directly to SQLite.

## Run Locally

1. Install dependencies:

```bash
pip install -r requirements.txt
```

2. Start the app:

```bash
streamlit run app.py
```

3. Open the URL shown in your terminal (typically `http://localhost:8501`).

## Run with Docker

Build image:

```bash
docker build -t recipesnap:latest .
```

Run with local SQLite in container:

```bash
docker run --rm -p 8501:8501 recipesnap:latest
```

Run with PostgreSQL for deployment-like setup:

```bash
docker run --rm -p 8501:8501 \
  -e DATABASE_URL="postgresql://USER:PASSWORD@HOST:5432/DBNAME" \
  recipesnap:latest
```

Persist SQLite file across container restarts (optional):

```bash
docker run --rm -p 8501:8501 \
  -v "${PWD}:/app" \
  recipesnap:latest
```

## OCR Setup (Photo to Text)

The photo-to-text feature now uses a Python OCR engine installed through `requirements.txt`, so no separate Tesseract system installation is required for the default flow.

Notes:

1. Install the project requirements normally with `pip install -r requirements.txt`.
2. The first OCR use may take a little longer while models initialize.
3. An optional fallback to `pytesseract` is still supported if it is already present in your environment.

## Notes

- The SQLite database file is created automatically on first run.
- Uploaded images are stored directly in the database as BLOBs.
- This project is lightweight and suitable for local deployment.
