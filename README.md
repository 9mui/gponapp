# GPON Monitoring Application

A web-based monitoring and management system for GPON (Gigabit Passive Optical Network) devices, specifically designed for managing OLTs (Optical Line Terminals) and ONUs (Optical Network Units).

## Features

- **Real-time GPON monitoring** - Monitor OLT devices and connected ONUs
- **SNMP-based communication** - Direct device communication via SNMP protocol
- **Web interface** - Modern, responsive dark-themed UI
- **Port management** - View and manage GPON ports and uplinks
- **ONU tracking** - Search and track ONU devices by serial number
- **Performance optimization** - SQLite WAL mode, SNMP caching, parallel processing
- **Background scheduling** - Automatic periodic device polling

## Technology Stack

- **Backend**: Flask 3.0.2, Python 3.8+
- **Database**: SQLite with WAL mode optimization
- **Scheduler**: APScheduler 3.10.4
- **Network**: SNMP protocol for device communication
- **Frontend**: Modern CSS with responsive design
- **Deployment**: Systemd service with gunicorn

## Quick Start

### Prerequisites

- Python 3.8 or higher
- SNMP tools (`snmpwalk`, `snmpset`, `snmpget`)
- Network access to GPON devices

### Installation

1. **Clone and setup**:
   ```bash
   cd /opt/gponapp
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. **Initialize database**:
   ```bash
   python3 -c "from models import ensure_db; ensure_db()"
   ```

3. **Run application**:
   ```bash
   python3 app.py
   ```

4. **Access the web interface**:
   - Open http://localhost:5000 in your browser

### Configuration

- **Database**: SQLite database stored in `instance/onulist.db`
- **Configuration**: Settings available in `config.py`
- **Logging**: Application logs to `gponapp.log`

## Project Structure

```
/opt/gponapp/
├── app.py              # Main Flask application
├── models.py           # Database models and connection handling
├── snmp.py             # SNMP communication functions
├── config.py           # Application configuration
├── schema.sql          # Database schema
├── diagnostic.py       # Health check and diagnostic tools
├── performance.py      # Performance monitoring utilities
├── repair_database.py  # Database repair and maintenance
├── requirements.txt    # Python dependencies
├── templates/          # HTML templates
├── static/            # CSS and static assets
├── tests/             # Unit tests
└── instance/          # Database and instance data
```

## Usage

### Adding OLT Devices

1. Navigate to the main page
2. Click "Добавить OLT" (Add OLT)
3. Fill in:
   - **Hostname**: Device name
   - **IP**: Device IP address
   - **Community**: SNMP community string
   - **Vendor**: Device vendor (BDCOM/Huawei)

### Monitoring ONUs

- **Search by Serial**: Use the search box to find ONUs by 16-character hex serial number
- **Recent ONUs**: View recently discovered ONUs
- **Port view**: Browse ONUs by OLT port

### Refreshing Data

- Click "Обновить данные" (Refresh Data) on OLT pages to update port and ONU information
- Background polling automatically updates data every 5 minutes

## Troubleshooting

### Database Issues

Run the repair tool:
```bash
python3 repair_database.py
```

### SNMP Connectivity

Run diagnostics:
```bash
python3 diagnostic.py
```

### Performance Monitoring

Check system metrics via the performance module or application logs.

## Maintenance

- **Database**: Automatic optimization with SQLite WAL mode
- **Logs**: Monitor `gponapp.log` for errors and performance
- **Backups**: Database repair tool creates automatic backups
- **Updates**: Restart service after code changes

## Service Management

If running as systemd service:

```bash
sudo systemctl start gponapp.service    # Start
sudo systemctl stop gponapp.service     # Stop
sudo systemctl restart gponapp.service  # Restart
sudo systemctl status gponapp.service   # Check status
```

## Security Notes

- Use secure SNMP communities
- Restrict network access to management interfaces
- Consider SNMPv3 for production deployments
- Monitor application logs for security events