#!/usr/bin/env python3
"""
Prepare Dashboard for Free Online Deployment
This script checks everything is ready for deployment
"""

import os
import json
import subprocess
import sys

def check_file_exists(filename, required=True):
    """Check if a file exists"""
    exists = os.path.exists(filename)
    status = "✅" if exists else ("❌" if required else "⚠️")
    print(f"{status} {filename}")
    return exists

def run_command(command, cwd=None):
    """Run a command and return success status"""
    try:
        result = subprocess.run(command, shell=True, cwd=cwd, check=True, 
                              capture_output=True, text=True)
        return True, result.stdout
    except subprocess.CalledProcessError as e:
        return False, e.stderr

def check_react_build():
    """Check if React can build successfully"""
    print("\n🔨 Testing React build...")
    
    # Check if node_modules exists
    if not os.path.exists("node_modules"):
        print("📦 Installing npm dependencies...")
        success, output = run_command("npm install")
        if not success:
            print(f"❌ npm install failed: {output}")
            return False
    
    # Try to build
    print("🏗️ Building React app...")
    success, output = run_command("npm run build")
    
    if success and os.path.exists("build"):
        print("✅ React build successful!")
        return True
    else:
        print(f"❌ React build failed: {output}")
        return False

def check_python_deps():
    """Check Python dependencies"""
    print("\n🐍 Checking Python dependencies...")
    
    success, output = run_command("pip install -r requirements.txt")
    if success:
        print("✅ Python dependencies OK!")
        return True
    else:
        print(f"❌ Python dependencies failed: {output}")
        return False

def create_env_example():
    """Create environment variables example"""
    env_content = """# Copy this to your deployment platform's environment variables

# Required - Your Odoo Configuration
ODOO_URL=https://your-odoo-instance.com
ODOO_DB=your_database_name
ODOO_USERNAME=your_odoo_username
ODOO_PASSWORD=your_odoo_password

# Optional
FLASK_SECRET_KEY=your-random-secret-key-here
PORT=5000
"""
    
    with open("deployment_env_vars.txt", "w") as f:
        f.write(env_content)
    
    print("✅ Created deployment_env_vars.txt")

def main():
    """Main function"""
    print("🚀 Preparing Dashboard for Free Online Deployment\n")
    
    # Check required files
    print("📁 Checking required files...")
    required_files = [
        "package.json",
        "requirements.txt", 
        "app.py",
        "src/App.js"
    ]
    
    deployment_files = [
        "production_app.py",
        "railway.json",
        "render.yaml",
        "nixpacks.toml"
    ]
    
    all_good = True
    
    for file in required_files:
        if not check_file_exists(file, required=True):
            all_good = False
    
    print("\n📦 Checking deployment files...")
    for file in deployment_files:
        check_file_exists(file, required=False)
    
    if not all_good:
        print("\n❌ Missing required files. Please ensure you're in the dashboard root directory.")
        sys.exit(1)
    
    # Test builds
    if not check_react_build():
        print("\n❌ React build failed. Please fix the issues above.")
        sys.exit(1)
    
    if not check_python_deps():
        print("\n❌ Python dependencies check failed.")
        sys.exit(1)
    
    # Create environment variables file
    create_env_example()
    
    # Final instructions
    print("\n🎉 Everything looks good for deployment!")
    print("\n📋 Next Steps:")
    print("1. Push your code to GitHub")
    print("2. Choose a deployment platform:")
    print("   • Railway (Recommended): https://railway.app")
    print("   • Render: https://render.com") 
    print("   • Vercel: https://vercel.com")
    print("3. Set environment variables (see deployment_env_vars.txt)")
    print("4. Deploy and share your dashboard URL!")
    
    print("\n📖 For detailed instructions, see: FREE_DEPLOYMENT.md")

if __name__ == "__main__":
    main()
