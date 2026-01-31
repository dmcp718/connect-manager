#!/bin/bash
# LucidLink Labs - Run Script (using uv)

cd "$(dirname "$0")"

echo "Starting LucidLink Labs..."
echo "Open http://localhost:8000 in your browser"
echo ""

uv run uvicorn main:app --host 0.0.0.0 --port 8000 --reload
