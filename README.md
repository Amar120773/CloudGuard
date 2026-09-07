# CloudGuard ☁️🛡️

**AI-Powered Cloud Security and Cost Optimization Platform**

CloudGuard is an enterprise-grade platform designed to monitor cloud workloads, assess security postures, and predict resource costs. It leverages intelligent machine learning algorithms to process cloud metrics and deliver actionable insights through a premium, high-performance web dashboard.

---

## 🏗️ Architecture

CloudGuard is built with a decoupled, asynchronous architecture capable of handling heavy cloud API ingestion and ML inference:

### **Frontend (Dashboard)**
- **Framework:** React + Vite
- **Styling:** Premium vanilla CSS with dark mode, glassmorphism, and responsive CSS grid.
- **Features:** Dynamic metric visualization, asynchronous data polling, and fallback states.

### **Backend (Analysis Engine)**
- **Framework:** Python / FastAPI
- **Task Queue & Caching:** Celery + Redis for asynchronous background processing and aggressive caching to mitigate API rate limits.
- **Cloud Integration:** Boto3 (mocked via Moto) to simulate ingestion from AWS services (CloudWatch, EC2, Security Hub, Cost Explorer).
- **Data Integrity:** Strict payload typing via Pydantic schemas.

### **Machine Learning (AI Layer)**
- **Cost Forecasting:** Uses Meta's `prophet` algorithm to run time-series forecasting on historical spend data.
- **Security Posture:** Uses `scikit-learn`'s **Isolation Forest** to detect anomalies in VPC flow logs (e.g., traffic spikes, anomalous failed logins).

---

## 🚀 Getting Started

Follow these steps to run the complete CloudGuard stack locally. You will need multiple terminal windows.

### 1. Prerequisites
- Python 3.10+
- Node.js & npm
- Docker (for Redis)

### 2. Start the Redis Broker
Celery and FastAPI require Redis for task routing and caching.
```bash
docker run -d -p 6379:6379 redis:latest
```

### 3. Start the Celery Worker (Backend Tasks)
Open a terminal in the `backend/` directory:
```bash
# Create and activate a virtual environment
python -m venv venv
# Windows: .\venv\Scripts\activate
# Mac/Linux: source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Start the Celery worker
celery -A celery_app worker --loglevel=info -P solo
```

### 4. Start the FastAPI Server (Backend API)
Open a **second terminal** in the `backend/` directory:
```bash
# Activate the same virtual environment
# Windows: .\venv\Scripts\activate
# Mac/Linux: source venv/bin/activate

# Start the uvicorn server
uvicorn main:app --reload
```
*The API will be available at `http://localhost:8000`.*

### 5. Start the Web Dashboard (Frontend)
Open a **third terminal** in the `frontend/` directory:
```bash
# Install dependencies
npm install

# Start the development server
npm run dev
```
*Navigate to the localhost URL provided by Vite (usually `http://localhost:5173`) in your browser to view the dashboard.*

---

## 📂 Project Structure

```text
CloudGuard/
├── backend/
│   ├── ml/
│   │   ├── anomaly_detector.py   # Isolation Forest ML model
│   │   └── cost_forecaster.py    # Prophet ML model
│   ├── services/
│   │   ├── aws_client.py         # Moto wrapper for Boto3
│   │   ├── aws_health.py         # CloudWatch / EC2 logic
│   │   ├── aws_cost.py           # Cost Explorer logic
│   │   └── aws_security.py       # Security Hub logic
│   ├── celery_app.py             # Task queue configuration
│   ├── main.py                   # FastAPI server & endpoints
│   ├── requirements.txt          
│   ├── schemas.py                # Pydantic data contracts
│   └── tasks.py                  # Celery background tasks
└── frontend/
    ├── src/
    │   ├── components/
    │   │   └── Dashboard.jsx     # Main UI component
    │   ├── App.jsx               # React Entry
    │   ├── index.css             # Premium Styling
    │   └── main.jsx
    ├── index.html
    ├── package.json
    └── vite.config.js
```
