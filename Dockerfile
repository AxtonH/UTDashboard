# Multi-stage Docker build for Dashboard
FROM node:18-alpine AS react-build

# Set working directory for React build
WORKDIR /app

# Copy package files
COPY package*.json ./

# Install dependencies
RUN npm install

# Copy React source code
COPY src/ src/
COPY public/ public/

# Build React app
RUN npm run build

# Stage 2: Python Flask backend
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy Flask backend
COPY app.py .

# Copy built React app from previous stage
COPY --from=react-build /app/build ./build

# Create production Flask app
COPY <<EOF production_app.py
import os
from flask import Flask, send_from_directory
from flask_cors import CORS
import sys

# Import your existing app routes
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from app import *

# Create production app
production_app = Flask(__name__, static_folder='build', static_url_path='')
CORS(production_app)

# Copy all routes from your existing app
for rule in app.url_map.iter_rules():
    endpoint = rule.endpoint
    if endpoint != 'static':
        view_func = app.view_functions.get(endpoint)
        if view_func:
            production_app.add_url_rule(
                rule.rule, 
                endpoint, 
                view_func, 
                methods=rule.methods
            )

@production_app.route('/')
def serve_react_app():
    return send_from_directory(production_app.static_folder, 'index.html')

@production_app.route('/<path:path>')
def serve_react_assets(path):
    if path != "" and os.path.exists(os.path.join(production_app.static_folder, path)):
        return send_from_directory(production_app.static_folder, path)
    else:
        return send_from_directory(production_app.static_folder, 'index.html')

if __name__ == '__main__':
    production_app.run(host='0.0.0.0', port=5000, debug=False)
EOF

# Expose port
EXPOSE 5000

# Environment variables
ENV FLASK_APP=production_app.py
ENV FLASK_ENV=production

# Start command
CMD ["python", "production_app.py"]
