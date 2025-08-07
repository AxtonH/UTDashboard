#!/usr/bin/env python3
"""
Local Network Deployment Script for Dashboard
This script builds the React frontend and serves both frontend and backend
"""

import os
import sys
import subprocess
import shutil
from pathlib import Path

def run_command(command, cwd=None):
    """Run a command and handle errors"""
    try:
        result = subprocess.run(command, shell=True, cwd=cwd, check=True, 
                              capture_output=True, text=True)
        print(f"✅ {command}")
        return result.stdout
    except subprocess.CalledProcessError as e:
        print(f"❌ Error running: {command}")
        print(f"Error output: {e.stderr}")
        return None

def build_react_app():
    """Build the React application for production"""
    print("🔨 Building React application...")
    
    # Install dependencies
    run_command("npm install")
    
    # Build the React app
    build_result = run_command("npm run build")
    
    if os.path.exists("build"):
        print("✅ React build completed successfully")
        return True
    else:
        print("❌ React build failed")
        return False

def setup_flask_for_production():
    """Setup Flask to serve the React build"""
    
    # Create production Flask app
    production_app = '''import os
from flask import Flask, send_from_directory, jsonify, request
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
    """Serve the React application"""
    return send_from_directory(production_app.static_folder, 'index.html')

@production_app.route('/<path:path>')
def serve_react_assets(path):
    """Serve React static assets"""
    if path != "" and os.path.exists(os.path.join(production_app.static_folder, path)):
        return send_from_directory(production_app.static_folder, path)
    else:
        return send_from_directory(production_app.static_folder, 'index.html')

if __name__ == '__main__':
    # Get your local IP for network access
    import socket
    hostname = socket.gethostname()
    local_ip = socket.gethostbyname(hostname)
    
    print(f"🚀 Dashboard deployed and accessible at:")
    print(f"   Local: http://localhost:5000")
    print(f"   Network: http://{local_ip}:5000")
    print(f"   Share this URL with your team!")
    
    production_app.run(host='0.0.0.0', port=5000, debug=False)
'''
    
    with open('production_app.py', 'w') as f:
        f.write(production_app)
    
    print("✅ Production Flask app created")

def main():
    """Main deployment function"""
    print("🚀 Starting Dashboard Deployment...")
    
    # Check if we're in the right directory
    if not os.path.exists('package.json') or not os.path.exists('app.py'):
        print("❌ Please run this script from the dashboard root directory")
        sys.exit(1)
    
    # Build React app
    if not build_react_app():
        sys.exit(1)
    
    # Setup production Flask
    setup_flask_for_production()
    
    print("\n🎉 Deployment setup complete!")
    print("\n📋 To start your deployed dashboard:")
    print("   python production_app.py")
    print("\n💡 Your dashboard will be accessible to anyone on your network!")

if __name__ == "__main__":
    main()
