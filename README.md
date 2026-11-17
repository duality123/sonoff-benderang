# Sistem Timbangan 3 Tahap

Full-stack CRUD sederhana:
- FastAPI
- PostgreSQL
- SQLAlchemy
- Jinja2 + HTML/CSS/JavaScript
- UUID untuk seluruh primary key
- Relasi Tahap 1 -> Tahap 2 -> Tahap 3
- Dashboard
- CRUD Tahap 1, Tahap 2, Tahap 3
- Tracking berdasarkan barcode

## 1. Install

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

Linux/macOS:
```bash
source venv/bin/activate
pip install -r requirements.txt
```

## 2. PostgreSQL

Buat database:

```sql
CREATE DATABASE timbangan_db;
```

Copy `.env.example` menjadi `.env`, lalu sesuaikan:

```env
DATABASE_URL=postgresql+psycopg2://postgres:postgres@localhost:5432/timbangan_db
```

## 3. Jalankan

```bash
uvicorn app.main:app --reload
```

Buka:
http://127.0.0.1:8000

API docs:
http://127.0.0.1:8000/docs

## Catatan

Saat startup, tabel dibuat otomatis menggunakan SQLAlchemy `create_all`.
Untuk production, gunakan Alembic untuk migration.

## Struktur

```text
timbangan_crud/
├── app/
│   ├── main.py
│   ├── database.py
│   ├── models.py
│   ├── schemas.py
│   ├── crud.py
│   ├── routers.py
│   ├── templates/
│   │   └── index.html
│   └── static/
│       ├── style.css
│       └── app.js
├── .env.example
├── requirements.txt
└── README.md
```
