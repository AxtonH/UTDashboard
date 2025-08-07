import os
from flask import Flask, send_from_directory, jsonify, request
from flask_cors import CORS
import sys

# Import your existing app routes
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from app import *

# Create production app
production_app = Flask(__name__, static_folder='build', static_url_path='')
CORS(production_app, origins=["*"])  # More permissive for deployment

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

# Health check endpoint for deployment platforms
@production_app.route('/health')
def health_check():
    """Health check endpoint"""
    return jsonify({"status": "healthy", "message": "Dashboard is running"})

if __name__ == '__main__':
    # Get port from environment (required for cloud platforms)
    port = int(os.environ.get('PORT', 5000))
    
    # Get your local IP for network access (only for local deployment)
    try:
        import socket
        hostname = socket.gethostname()
        local_ip = socket.gethostbyname(hostname)
        
        print(f"🚀 Dashboard deployed and accessible at:")
        print(f"   Local: http://localhost:{port}")
        print(f"   Network: http://{local_ip}:{port}")
    except:
        print(f"🚀 Dashboard running on port {port}")
    
    production_app.run(host='0.0.0.0', port=port, debug=False)
