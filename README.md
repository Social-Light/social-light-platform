# SocialLight — Django Media Monitor

A Social Light-style media monitoring platform built with Django and the Anthropic API.

## Features

- Real-time brand mention scanning via Anthropic web search
- Sentiment analysis (positive / neutral / negative)
- Mentions feed with platform, author, and source links
- Sentiment trend chart (Chart.js)
- Trending topics with frequency bars
- Competitor comparison
- Dark-mode dashboard UI

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
```

Edit `.env` and add your keys:

```
ANTHROPIC_API_KEY=sk-ant-...
DJANGO_SECRET_KEY=some-long-random-string
DEBUG=True
```

Get your Anthropic API key from: https://console.anthropic.com

### 3. Collect static files (production) or run dev server

```bash
# Development
python manage.py runserver

# Production — collect statics first
python manage.py collectstatic
```

### 4. Open in browser

Visit: http://127.0.0.1:8000

## Project Structure

```
socialmonitor/
├── manage.py
├── requirements.txt
├── .env.example
├── socialmonitor/          # Django project config
│   ├── settings.py
│   └── urls.py
└── monitor/                # Main app
    ├── views.py            # index + /api/monitor/ endpoint
    ├── urls.py
    ├── templates/monitor/
    │   └── index.html      # Dashboard template
    └── static/monitor/
        ├── css/main.css    # Dark-mode stylesheet
        └── js/app.js       # Frontend logic + Chart.js
```

## API Endpoint

`POST /api/monitor/`

Request body:
```json
{ "brand": "Debswana" }
```

Response: JSON with `summary`, `stats`, `mentions`, `topTopics`, `sentimentOverTime`, `competitors`.

## Deployment

For production, set `DEBUG=False` in `.env`, configure `ALLOWED_HOSTS`, run `collectstatic`, and serve with Gunicorn + Nginx.

