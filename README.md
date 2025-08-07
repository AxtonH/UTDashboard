# Odoo Creative Department Dashboard

A beautiful Flask + React dashboard that displays employees from the Creative Department in your Odoo instance.

## Features

- 🔗 **Odoo Integration**: Connects to your Odoo instance via XML-RPC
- 🎨 **Modern UI**: Beautiful, responsive design with gradient backgrounds
- 📊 **Real-time Data**: Fetches employee data directly from Odoo
- 🔄 **Refresh Functionality**: Manual refresh button to update data
- 📱 **Mobile Responsive**: Works perfectly on all device sizes
- ⚡ **Fast Performance**: Optimized for quick loading and smooth interactions

## Prerequisites

- Python 3.7 or higher
- Node.js 14 or higher
- npm or yarn

## Installation & Setup

### 1. Clone or Download the Project

Make sure you have all the files in your project directory.

### 2. Backend Setup (Flask)

1. **Install Python dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Start the Flask backend:**
   ```bash
   python app.py
   ```
   
   The Flask server will start on `http://localhost:5000`

### 3. Frontend Setup (React)

1. **Install Node.js dependencies:**
   ```bash
   npm install
   ```

2. **Start the React development server:**
   ```bash
   npm start
   ```
   
   The React app will start on `http://localhost:3000`

## Usage

1. Make sure both servers are running:
   - Flask backend on port 5000
   - React frontend on port 3000

2. Open your browser and navigate to `http://localhost:3000`

3. The dashboard will automatically fetch and display all employees from the Creative Department

4. Use the "Refresh" button to manually update the data

## API Endpoints

- `GET /api/creative-employees` - Returns all employees in the Creative Department
- `GET /api/health` - Health check endpoint

## Configuration

The Odoo connection details are configured in `app.py`:

```python
ODOO_URL = "https://prezlab-staging-22061821.dev.odoo.com"
ODOO_DB = "prezlab-staging-22061821"
ODOO_USERNAME = "omar.elhasan@prezlab.com"
ODOO_PASSWORD = "Omar@@1998"
```

## Troubleshooting

### Common Issues

1. **"Error connecting to the server"**
   - Make sure the Flask backend is running on port 5000
   - Check that the Odoo credentials are correct
   - Verify the Odoo URL is accessible

2. **"No employees found"**
   - Check if there are employees assigned to the Creative Department in Odoo
   - Verify the department name contains "creative" (case-insensitive)

3. **CORS errors**
   - The Flask app includes CORS headers, but if you encounter issues, check that the proxy is set correctly in `package.json`

### Debug Mode

To run the Flask app in debug mode (recommended for development):
```bash
python app.py
```

The app will automatically reload when you make changes to the Python files.

## Project Structure

```
Dashboard-1/
├── app.py                 # Flask backend server
├── requirements.txt       # Python dependencies
├── package.json          # Node.js dependencies
├── public/
│   └── index.html        # Main HTML file
└── src/
    ├── App.js            # Main React component
    ├── App.css           # React component styles
    ├── index.js          # React entry point
    └── index.css         # Global styles
```

## Security Notes

- The current implementation includes credentials directly in the code for demonstration
- For production use, consider using environment variables or a secure configuration management system
- Ensure your Odoo instance has proper security measures in place

## License

This project is created for demonstration purposes. 