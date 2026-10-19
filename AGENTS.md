# AGENTS.md

## Commands
- Run tests: `cd Website/BackEnd/quickstock && python3 manage.py test`
- Run linter: `pip install flake8 && flake8 Website/BackEnd/quickstock/inventory --max-line-length=120`
- Run typecheck: `pip install mypy && mypy Website/BackEnd/quickstock --ignore-missing-imports`

## Project Structure
- Backend (Django): `Website/BackEnd/quickstock/`
- Desktop UI: `Desktop_UI/` and `desktop/`

## Security Testing
- Security tests: `python3 manage.py test inventory.tests.WeekOneSecurityTests`
- Full test suite: `python3 manage.py test --verbosity=0`
