# QuickStock JA - Inventory Management & Cash Registration System

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![Django 5.2+](https://img.shields.io/badge/Django-5.2+-green.svg)](https://www.djangoproject.com/)

## Overview

QuickStock JA is a comprehensive inventory management and point-of-sale (POS) system designed for Caribbean businesses, with support for Jamaican GCT, Barbados VAT, and other regional tax systems. The system consists of a Django web backend and a cross-platform desktop application.

## Features

### 🏪 Point of Sale (POS)
- Barcode scanner support
- Real-time inventory lookup
- Tax calculation (GCT/VAT)
- Discount management
- Receipt printing and email
- Offline mode with sync queue

### 📦 Inventory Management
- SKU/barcode tracking
- Multi-location stock management
- Stock transfers between locations
- Low stock alerts
- Category and brand management
- Supplier management

### 📊 Reports & Analytics
- Sales summaries
- Receipt history
- Inventory valuation
- Export to CSV/Excel
- Audit logs

### 🔐 Security Features
- Role-based access control (Admin, Manager, Cashier)
- Encrypted local cache
- HTTPS enforcement
- API token authentication
- Audit trail logging

### 🌐 Multi-Platform Support
- **Desktop App**: Windows, macOS, Linux (Python/Tkinter)
- **Web Backend**: Django REST API
- **Database**: SQLite (dev) / MySQL (production)

## System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        QuickStock JA                             │
├─────────────────────────────────────────────────────────────────┤
│                                                                   │
│  ┌─────────────────────┐         ┌─────────────────────┐        │
│  │   Desktop Client    │◄───────►│   Web Backend       │        │
│  │   (Python/TK)       │   API   │   (Django REST)     │        │
│  │                     │  HTTPS  │                     │        │
│  │  • POS Interface    │         │  • User Management  │        │
│  │  • Inventory Mgmt   │         │  • Data Storage     │        │
│  │  • Reports          │         │  • API Endpoints    │        │
│  │  • Offline Cache    │         │  • Payment Gateway  │        │
│  └─────────────────────┘         └─────────────────────┘        │
│                                           │                       │
│                                           ▼                       │
│                                   ┌─────────────┐                │
│                                   │   Database  │                │
│                                   │  (MySQL)    │                │
│                                   └─────────────┘                │
│                                                                   │
└─────────────────────────────────────────────────────────────────┘
```

## Quick Start

### Prerequisites

- Python 3.8 or higher
- pip (Python package manager)
- MySQL 8.0+ (for production)

### Installation

#### 1. Clone the Repository

```bash
git clone https://github.com/yourusername/quickstock-ja.git
cd quickstock-ja
```

#### 2. Backend Setup (Django)

```bash
cd Website/BackEnd/quickstock

# Create virtual environment
python -m venv venv

# Activate virtual environment
# Windows:
venv\Scripts\activate
# Linux/macOS:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Copy environment file
cp .env.example .env

# Edit .env with your settings
# - Set DJANGO_SECRET_KEY
# - Configure database settings
# - Set ALLOWED_HOSTS

# Run migrations
python manage.py migrate

# Create superuser
python manage.py createsuperuser

# Collect static files
python manage.py collectstatic

# Run development server
python manage.py runserver
```

The backend will be available at `http://localhost:8000`

#### 3. Desktop Client Setup

```bash
cd Desktop_UI

# Create virtual environment
python -m venv venv

# Activate virtual environment
# Windows:
venv\Scripts\activate
# Linux/macOS:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Copy environment file
cp .env.example .env

# Edit .env with your API URL
# API_BASE_URL=http://localhost:8000

# Run the application
python main.py
```

## Configuration

### Environment Variables

#### Backend (.env)

| Variable | Description | Default |
|----------|-------------|---------|
| `DJANGO_SECRET_KEY` | Django secret key (required) | - |
| `DJANGO_DEBUG` | Debug mode | `True` |
| `DJANGO_ALLOWED_HOSTS` | Comma-separated allowed hosts | `localhost,127.0.0.1` |
| `DJANGO_DB_ENGINE` | Database engine | `django.db.backends.sqlite3` |
| `DJANGO_DB_NAME` | Database name | `db.sqlite3` |
| `DJANGO_DB_USER` | Database user | - |
| `DJANGO_DB_PASSWORD` | Database password | - |
| `DJANGO_DB_HOST` | Database host | `127.0.0.1` |
| `DJANGO_DB_PORT` | Database port | `3306` |

#### Desktop Client (.env)

| Variable | Description | Default |
|----------|-------------|---------|
| `API_BASE_URL` | Backend API URL | `https://api.quickstockja.com` |
| `QUICKSTOCK_ENV` | Environment (dev/prod) | `development` |
| `QUICKSTOCK_LOG_LEVEL` | Logging level | `INFO` |

## Production Deployment

### Backend (Django + Gunicorn + Nginx)

1. **Install system dependencies:**
   ```bash
   sudo apt update
   sudo apt install python3-pip python3-venv mysql-server nginx
   ```

2. **Set up the application:**
   ```bash
   sudo mkdir -p /srv/quickstock
   sudo chown $USER:$USER /srv/quickstock
   cd /srv/quickstock
   git clone <repository-url> current
   cd current/Website/BackEnd/quickstock
   python3 -m venv /srv/quickstock/venv
   source /srv/quickstock/venv/bin/activate
   pip install -r requirements.txt
   ```

3. **Configure environment:**
   ```bash
   sudo mkdir -p /etc/quickstock
   sudo cp .env.production /etc/quickstock/quickstock.env
   # Edit /etc/quickstock/quickstock.env with production values
   ```

4. **Set up Gunicorn:**
   ```bash
   sudo cp deploy/quickstock.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable quickstock
   sudo systemctl start quickstock
   ```

5. **Configure Nginx:**
   ```bash
   sudo cp deploy/nginx-quickstock.conf /etc/nginx/sites-available/
   # Edit the config with your domain and SSL paths
   sudo ln -s /etc/nginx/sites-available/nginx-quickstock.conf /etc/nginx/sites-enabled/
   sudo nginx -t
   sudo systemctl reload nginx
   ```

6. **Set up SSL (Let's Encrypt):**
   ```bash
   sudo apt install certbot python3-certbot-nginx
   sudo certbot --nginx -d yourdomain.com -d www.yourdomain.com
   ```

### Desktop Client (PyInstaller)

```bash
cd Desktop_UI
pip install pyinstaller

# Build for current platform
pyinstaller Inventory_gui.spec

# The executable will be in dist/
```

## API Documentation

### Authentication

```http
POST /api/login/
Content-Type: application/json

{
  "username": "admin",
  "password": "password"
}

Response:
{
  "ok": true,
  "token": "eyJhbGciOi...",
  "user": {
    "id": 1,
    "username": "admin",
    "role": "admin",
    "default_location_id": 1
  }
}
```

### Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/login/` | Authenticate user |
| `GET` | `/api/profile/` | Get user profile |
| `GET` | `/api/inventory/` | List inventory items |
| `POST` | `/api/inventory/` | Create/update item |
| `DELETE` | `/api/inventory/{sku}/` | Delete item |
| `GET` | `/api/locations/` | List locations |
| `POST` | `/api/locations/` | Create location |
| `GET` | `/api/suppliers/` | List suppliers |
| `POST` | `/api/suppliers/` | Create supplier |
| `POST` | `/api/receive-stock/` | Receive stock |
| `POST` | `/api/transfer-stock/` | Transfer stock |
| `POST` | `/api/sales/` | Create sale |
| `GET` | `/api/health/` | Health check |

## Development

### Running Tests

```bash
# Backend tests
cd Website/BackEnd/quickstock
pytest

# Desktop tests
cd Desktop_UI
pytest
```

### Code Style

This project follows PEP 8 style guidelines. Use `black` for formatting:

```bash
pip install black
black .
```

## Project Structure

```
quickstock-ja/
├── Desktop_UI/                 # Desktop application
│   ├── src/
│   │   ├── core/              # Business logic
│   │   ├── services/          # Service layer
│   │   ├── infrastructure/    # Logging, database
│   │   ├── storage/           # Cache and storage
│   │   └── ui/                # User interface
│   ├── main.py                # Entry point
│   ├── Inventory_gui.py       # Main GUI class
│   ├── requirements.txt       # Python dependencies
│   └── .env.example          # Configuration template
│
├── Website/BackEnd/quickstock/ # Django backend
│   ├── quickstock/            # Project settings
│   ├── inventory/             # Main application
│   │   ├── api.py            # REST API endpoints
│   │   ├── models.py         # Database models
│   │   ├── views.py          # Web views
│   │   └── migrations/       # Database migrations
│   ├── deploy/               # Deployment configs
│   ├── scripts/              # Utility scripts
│   └── requirements.txt      # Python dependencies
│
├── PRODUCTION_HARDENING_SUMMARY.md  # Security documentation
├── SECURITY_AUDIT_REPORT.md         # Security audit
├── LICENSE                          # MIT License
└── README.md                        # This file
```

## Security

### Data Protection
- All API communications use HTTPS
- Sensitive data (API tokens) encrypted at rest
- No plaintext credentials stored
- Role-based access control

### Compliance
- GDPR-ready data handling
- Audit logging for all critical operations
- Secure session management

## Payment Integration

### Supported Payment Gateways
- **WiPay** (Caribbean)
- **Stripe** (International)
- **JAM-DEX** (Jamaica Central Bank CBDC)

## Troubleshooting

### Common Issues

**Issue:** Desktop app can't connect to backend
- **Solution:** Check `API_BASE_URL` in `.env` matches your backend URL

**Issue:** "Database locked" errors
- **Solution:** Ensure no other process is accessing the database

**Issue:** SSL certificate errors
- **Solution:** For development, use `QUICKSTOCK_ENV=development`

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Support

For support and questions:
- Email: support@quickstockja.com
- Documentation: https://docs.quickstockja.com

## Acknowledgments

- Caribbean tax calculations based on official government rates
- UI design inspired by modern POS systems
- Built with love for Caribbean businesses

---

**QuickStock JA** - Empowering Caribbean Businesses with Modern Inventory Management