from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import redis
import json
import os
from celery.result import AsyncResult
from tasks import update_health_data, update_security_data, update_cost_data
from celery_app import celery_app

app = FastAPI(title="CloudGuard API", description="AI Powered Cloud Security and Cost Optimization")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
redis_client = redis.from_url(redis_url)

CACHE_TTL = 300 # 5 minutes

def get_cached_or_trigger(cache_key: str, celery_task):
    """
    Attempts to fetch data from Redis cache.
    If not found, triggers the Celery task and returns a 'processing' status.
    """
    try:
        cached_data = redis_client.get(cache_key)
        if cached_data:
            return json.loads(cached_data)
    except redis.ConnectionError:
        print("Redis not available, falling back to synchronous execution or mock data")
        # In a robust system, we would handle this gracefully.
        pass

    # Dispatch to Celery
    task = celery_task.delay()
    
    return {
        "status": "processing",
        "task_id": task.id,
        "message": "Data is being fetched and analyzed. Please poll /api/task/{task_id}"
    }

@app.get("/")
def read_root():
    return {"message": "Welcome to CloudGuard API (Phase 2 - Live AWS + ML)"}

@app.get("/api/health")
def get_health():
    return get_cached_or_trigger("cloudguard:health", update_health_data)

@app.get("/api/security")
def get_security():
    return get_cached_or_trigger("cloudguard:security", update_security_data)

@app.get("/api/cost")
def get_cost():
    return get_cached_or_trigger("cloudguard:cost", update_cost_data)

@app.get("/api/summary")
def get_summary():
    """
    Aggregate endpoint. For the frontend to remain simple during transition,
    if Redis is unavailable, we can attempt to execute synchronously for the demo.
    """
    try:
        health = redis_client.get("cloudguard:health")
        security = redis_client.get("cloudguard:security")
        cost = redis_client.get("cloudguard:cost")
        
        if health and security and cost:
            return {
                "health": json.loads(health),
                "security": json.loads(security),
                "cost": json.loads(cost)
            }
    except Exception:
        pass
        
    return {
        "status": "processing",
        "message": "Aggregate data not fully cached. Please check individual endpoints."
    }

@app.get("/api/task/{task_id}")
def get_task_status(task_id: str):
    """Endpoint for the frontend to poll Celery task completion."""
    task_result = AsyncResult(task_id, app=celery_app)
    
    if task_result.state == 'SUCCESS':
        # Determine which cache key to update based on task name
        cache_key = None
        if task_result.name == "tasks.update_health_data":
            cache_key = "cloudguard:health"
        elif task_result.name == "tasks.update_security_data":
            cache_key = "cloudguard:security"
        elif task_result.name == "tasks.update_cost_data":
            cache_key = "cloudguard:cost"
            
        if cache_key:
            redis_client.setex(cache_key, CACHE_TTL, json.dumps(task_result.result))
            
        return {"status": "completed", "data": task_result.result}
        
    elif task_result.state == 'PENDING' or task_result.state == 'STARTED':
        return {"status": "processing"}
    else:
        raise HTTPException(status_code=500, detail=f"Task failed: {task_result.state}")
