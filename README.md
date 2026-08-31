# UFC Fight Forecasting Engine

A data-driven UFC analytics engine that rates every fighter through time
and predicts fight outcomes with calibrated probabilities.

## What it does

- **P4P Rankings** -> Glicko-2 rating system built on all historical UFC bouts, 
  normalised across weight classes and adjusted for title fight context
- **Fight Predictor** -> Calibrated win probabilities from point-in-time features
  (rating differentials, physical mismatch, experience, activity, layoff),
  validated on a chronological holdout
- **Weekly Refresh** -> Scheduled job that scrapes new events, recomputes every
  rating and rebuilds the modelling dataset without manual intervention

## Tech Stack

- **Scraping**: Python, Playwright, BeautifulSoup
- **Database**: PostgreSQL
- **Backend**: FastAPI
- **ML/Modelling**: scikit-learn, LightGBM, custom Glicko-2 implementation

## Status

🚧 In active development
