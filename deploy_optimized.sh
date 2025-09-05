#!/bin/bash
# Production deployment script for GPON App with optimizations

echo "Starting GPON App optimization deployment..."

# Create virtual environment if it doesn't exist
if [ ! -d ".venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv .venv
fi

# Activate virtual environment
source .venv/bin/activate

# Install optimized requirements
echo "Installing requirements..."
pip install -r requirements.txt

# Initialize database with optimized schema
echo "Initializing optimized database..."
python3 -c "from models import ensure_db; ensure_db()"

# Pre-compile Python files for faster startup
echo "Pre-compiling Python files..."
python3 -m py_compile *.py

# Create systemd service file for production
cat > /tmp/gponapp.service << 'EOF'
[Unit]
Description=GPON Monitoring Application
After=network.target

[Service]
Type=exec
User=gponapp
WorkingDirectory=/opt/gponapp
Environment=PYTHONPATH=/opt/gponapp
ExecStart=/opt/gponapp/.venv/bin/gunicorn --bind 0.0.0.0:5000 --workers 4 --worker-class sync --timeout 30 --keepalive 2 --max-requests 1000 --max-requests-jitter 50 app:app
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

echo "Systemd service file created at /tmp/gponapp.service"
echo "To install: sudo cp /tmp/gponapp.service /etc/systemd/system/"
echo "Then: sudo systemctl daemon-reload && sudo systemctl enable gponapp && sudo systemctl start gponapp"

# Create nginx configuration for reverse proxy
cat > /tmp/gponapp.nginx << 'EOF'
server {
    listen 80;
    server_name your-domain.com;
    
    # Gzip compression for better performance
    gzip on;
    gzip_types text/plain text/css application/json application/javascript text/xml application/xml text/javascript;
    
    # Cache static files
    location /static/ {
        alias /opt/gponapp/static/;
        expires 30d;
        add_header Cache-Control "public, immutable";
    }
    
    # Proxy to Flask app
    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_connect_timeout 30s;
        proxy_send_timeout 30s;
        proxy_read_timeout 30s;
    }
}
EOF

echo "Nginx configuration created at /tmp/gponapp.nginx"

echo "Optimization deployment complete!"
echo ""
echo "Performance improvements include:"
echo "- SNMP caching with 30-second TTL"
echo "- Database connection pooling"
echo "- Optimized database indexes"
echo "- Parallel OLT processing"
echo "- Reduced logging overhead"
echo "- Batch database operations"
echo ""
echo "For production deployment:"
echo "1. Copy service file: sudo cp /tmp/gponapp.service /etc/systemd/system/"
echo "2. Enable service: sudo systemctl enable gponapp"
echo "3. Start service: sudo systemctl start gponapp"
echo "4. Setup nginx: sudo cp /tmp/gponapp.nginx /etc/nginx/sites-available/gponapp"
echo "5. Enable nginx site: sudo ln -s /etc/nginx/sites-available/gponapp /etc/nginx/sites-enabled/"
echo "6. Restart nginx: sudo systemctl restart nginx"