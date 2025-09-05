# GPON App Performance Configuration

# SNMP Settings
SNMP_TIMEOUT = 1.0
SNMP_RETRIES = 1
SNMP_BULK_SIZE = 100
SNMP_CACHE_TTL = 30  # seconds

# Database Settings
DB_CONNECTION_POOL_SIZE = 10
DB_CACHE_SIZE = 10000
DB_TIMEOUT = 30

# Threading Settings
MAX_WORKERS_OLT_SCAN = 8
MAX_WORKERS_BACKGROUND = 4
BACKGROUND_POLL_INTERVAL = 300  # 5 minutes

# Application Settings
ENABLE_SNMP_CACHE = True
ENABLE_COMPRESSION = True
LOG_LEVEL = "INFO"  # DEBUG, INFO, WARNING, ERROR

# Performance Monitoring
ENABLE_METRICS = True
METRICS_INTERVAL = 60  # seconds