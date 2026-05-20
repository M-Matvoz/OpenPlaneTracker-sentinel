#!/bin/bash
python /app/orchestrator.py
exec uvicorn main:app --host 0.0.0.0 --port 8001 --reload
