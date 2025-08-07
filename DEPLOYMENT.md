# Dashboard Deployment Guide

## 🚀 Quick Local Network Deployment (Recommended)

### Prerequisites
- Python 3.8+ installed
- Node.js 16+ installed
- Access to your Odoo instance

### Steps

1. **Run the deployment script:**
   ```bash
   python deploy_local.py
   ```

2. **Start the production server:**
   ```bash
   python production_app.py
   ```

3. **Access your dashboard:**
   - Local: http://localhost:5000
   - Network: http://YOUR_IP:5000 (share with your team)

---

## 🐳 Docker Deployment

### Prerequisites
- Docker and Docker Compose installed

### Steps

1. **Build and run with Docker Compose:**
   ```bash
   docker-compose up --build -d
   ```

2. **Access your dashboard:**
   - http://localhost:5000

3. **Stop the service:**
   ```bash
   docker-compose down
   ```

---

## 🌐 Cloud Deployment Options

### Option A: DigitalOcean App Platform

1. **Create a new app** on DigitalOcean
2. **Connect your GitHub repository**
3. **Configure build settings:**
   - Build Command: `npm run build && pip install -r requirements.txt`
   - Run Command: `python production_app.py`
4. **Set environment variables** (see env.example)
5. **Deploy**

### Option B: Heroku

1. **Install Heroku CLI**
2. **Create Heroku app:**
   ```bash
   heroku create your-dashboard-name
   ```
3. **Add buildpacks:**
   ```bash
   heroku buildpacks:add heroku/nodejs
   heroku buildpacks:add heroku/python
   ```
4. **Set environment variables:**
   ```bash
   heroku config:set ODOO_URL=your-odoo-url
   heroku config:set ODOO_DB=your-db
   # ... other variables
   ```
5. **Deploy:**
   ```bash
   git push heroku main
   ```

### Option C: AWS EC2

1. **Launch an EC2 instance** (Ubuntu 20.04 LTS)
2. **Install dependencies:**
   ```bash
   sudo apt update
   sudo apt install python3 python3-pip nodejs npm nginx
   ```
3. **Clone your repository**
4. **Run deployment script:**
   ```bash
   python3 deploy_local.py
   ```
5. **Configure Nginx** (optional, for custom domain)
6. **Setup systemd service** for auto-restart

---

## 🔧 Configuration

### Environment Variables

Copy `env.example` to `.env` and configure:

```bash
cp env.example .env
nano .env  # or your preferred editor
```

### Required Variables:
- `ODOO_URL`: Your Odoo instance URL
- `ODOO_DB`: Database name
- `ODOO_USERNAME`: Odoo username
- `ODOO_PASSWORD`: Odoo password

### Optional Variables:
- `PORT`: Custom port (default: 5000)
- `FLASK_SECRET_KEY`: Secret key for Flask sessions

---

## 🔒 Security Considerations

### For Production Deployment:

1. **Use HTTPS:** Set up SSL certificates
2. **Environment Variables:** Never commit sensitive data
3. **Firewall:** Restrict access to necessary ports only
4. **Updates:** Keep dependencies updated
5. **Backup:** Regular backups of any cached data

### Network Security:
```bash
# Example firewall rules (Ubuntu/Debian)
sudo ufw allow 22    # SSH
sudo ufw allow 5000  # Dashboard
sudo ufw enable
```

---

## 📊 Monitoring & Maintenance

### Health Check Endpoint:
Your dashboard includes a health check at: `/api/health`

### Logs:
- Docker: `docker-compose logs -f dashboard`
- Local: Check console output

### Performance:
- Monitor CPU/Memory usage
- Check Odoo connection status
- Monitor cache hit rates

---

## 🆘 Troubleshooting

### Common Issues:

1. **"Connection to Odoo failed"**
   - Check ODOO_URL, credentials
   - Verify network connectivity

2. **"React app not loading"**
   - Ensure `npm run build` completed successfully
   - Check static file serving

3. **"API endpoints not working"**
   - Verify Flask routes are imported correctly
   - Check CORS settings

4. **Performance issues**
   - Monitor cache usage
   - Check Odoo server performance
   - Consider adding more caching

### Getting Help:
- Check application logs
- Verify environment variables
- Test Odoo connectivity separately
