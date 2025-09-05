"""
Performance monitoring and metrics for GPON App
"""
import time
import threading
import psutil
from collections import defaultdict, deque
from datetime import datetime

class PerformanceMonitor:
    def __init__(self):
        self._metrics = defaultdict(deque)
        self._start_time = time.time()
        self._request_count = 0
        self._snmp_calls = 0
        self._db_queries = 0
        self._cache_hits = 0
        self._cache_misses = 0
        self._lock = threading.Lock()
        
    def record_request(self, duration):
        """Record HTTP request duration"""
        with self._lock:
            self._request_count += 1
            self._metrics['request_duration'].append((time.time(), duration))
            
    def record_snmp_call(self, duration, success=True):
        """Record SNMP call metrics"""
        with self._lock:
            self._snmp_calls += 1
            self._metrics['snmp_duration'].append((time.time(), duration))
            if success:
                self._metrics['snmp_success'].append(time.time())
            else:
                self._metrics['snmp_failure'].append(time.time())
                
    def record_db_query(self, duration):
        """Record database query duration"""
        with self._lock:
            self._db_queries += 1
            self._metrics['db_duration'].append((time.time(), duration))
            
    def record_cache_hit(self):
        """Record cache hit"""
        with self._lock:
            self._cache_hits += 1
            
    def record_cache_miss(self):
        """Record cache miss"""
        with self._lock:
            self._cache_misses += 1
            
    def get_system_metrics(self):
        """Get current system metrics"""
        return {
            'cpu_percent': psutil.cpu_percent(),
            'memory_percent': psutil.virtual_memory().percent,
            'disk_usage': psutil.disk_usage('/').percent,
            'network_io': psutil.net_io_counters()._asdict(),
        }
        
    def get_app_metrics(self):
        """Get application metrics"""
        uptime = time.time() - self._start_time
        cache_total = self._cache_hits + self._cache_misses
        cache_ratio = (self._cache_hits / cache_total * 100) if cache_total > 0 else 0
        
        return {
            'uptime_seconds': uptime,
            'total_requests': self._request_count,
            'requests_per_second': self._request_count / uptime if uptime > 0 else 0,
            'total_snmp_calls': self._snmp_calls,
            'total_db_queries': self._db_queries,
            'cache_hit_ratio': cache_ratio,
            'cache_hits': self._cache_hits,
            'cache_misses': self._cache_misses,
        }
        
    def cleanup_old_metrics(self, max_age=3600):
        """Remove metrics older than max_age seconds"""
        cutoff = time.time() - max_age
        with self._lock:
            for metric_name, metric_data in self._metrics.items():
                while metric_data and metric_data[0][0] < cutoff:
                    metric_data.popleft()

# Global performance monitor instance
performance_monitor = PerformanceMonitor()