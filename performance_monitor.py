#!/usr/bin/env python3
"""
GPON Application Performance Monitor
Tracks database performance, SNMP cache efficiency, and system metrics
"""

import time
import psutil
import sqlite3
from datetime import datetime, timedelta
from models import db
from app import snmp_cache

class PerformanceMonitor:
    def __init__(self):
        self.start_time = time.time()
        self.metrics = {
            'db_queries': 0,
            'snmp_cache_hits': 0,
            'snmp_cache_misses': 0,
            'polling_cycles': 0,
            'avg_response_time': 0.0
        }
    
    def get_database_stats(self):
        """Get SQLite database performance statistics"""
        try:
            with db() as conn:
                # Database size and page info
                cursor = conn.execute("PRAGMA page_count")
                page_count = cursor.fetchone()[0]
                
                cursor = conn.execute("PRAGMA page_size")
                page_size = cursor.fetchone()[0]
                
                # Cache statistics
                cursor = conn.execute("PRAGMA cache_size")
                cache_size = cursor.fetchone()[0]
                
                # WAL mode info
                cursor = conn.execute("PRAGMA journal_mode")
                journal_mode = cursor.fetchone()[0]
                
                # Table sizes
                stats = {}
                for table in ['olts', 'gpon', 'ponports', 'onu_seen', 'onu_notes']:
                    cursor = conn.execute(f"SELECT COUNT(*) FROM {table}")
                    stats[f'{table}_count'] = cursor.fetchone()[0]
                
                return {
                    'database_size_mb': round((page_count * page_size) / 1024 / 1024, 2),
                    'page_count': page_count,
                    'page_size': page_size,
                    'cache_size': cache_size,
                    'journal_mode': journal_mode,
                    'table_stats': stats
                }
        except Exception as e:
            return {'error': str(e)}
    
    def get_snmp_cache_stats(self):
        """Get SNMP cache performance statistics"""
        total_entries = len(snmp_cache)
        current_time = time.time()
        
        # Count fresh vs stale entries
        fresh_entries = 0
        stale_entries = 0
        
        for cache_key, (data, cached_time) in snmp_cache.items():
            if current_time - cached_time < 30:  # 30 second TTL
                fresh_entries += 1
            else:
                stale_entries += 1
        
        hit_rate = 0.0
        if self.metrics['snmp_cache_hits'] + self.metrics['snmp_cache_misses'] > 0:
            hit_rate = (self.metrics['snmp_cache_hits'] / 
                       (self.metrics['snmp_cache_hits'] + self.metrics['snmp_cache_misses'])) * 100
        
        return {
            'total_entries': total_entries,
            'fresh_entries': fresh_entries,
            'stale_entries': stale_entries,
            'cache_hit_rate': round(hit_rate, 2),
            'cache_hits': self.metrics['snmp_cache_hits'],
            'cache_misses': self.metrics['snmp_cache_misses']
        }
    
    def get_system_stats(self):
        """Get system performance statistics"""
        return {
            'cpu_percent': psutil.cpu_percent(interval=1),
            'memory_percent': psutil.virtual_memory().percent,
            'disk_usage': psutil.disk_usage('/').percent,
            'network_connections': len(psutil.net_connections()),
            'uptime_hours': round((time.time() - self.start_time) / 3600, 2)
        }
    
    def get_recent_performance(self):
        """Get performance data for recent operations"""
        try:
            with db() as conn:
                # Recent polling activity
                cursor = conn.execute("""
                    SELECT COUNT(*) as recent_onus 
                    FROM onu_seen 
                    WHERE last_seen >= datetime('now', '-1 hour')
                """)
                recent_activity = cursor.fetchone()[0]
                
                # Database growth rate
                cursor = conn.execute("""
                    SELECT COUNT(*) as new_onus
                    FROM onu_seen 
                    WHERE first_seen >= datetime('now', '-24 hours')
                """)
                daily_growth = cursor.fetchone()[0]
                
                return {
                    'recent_onu_activity': recent_activity,
                    'daily_onu_growth': daily_growth,
                    'polling_cycles': self.metrics['polling_cycles'],
                    'avg_response_time_ms': round(self.metrics['avg_response_time'] * 1000, 2)
                }
        except Exception as e:
            return {'error': str(e)}
    
    def generate_report(self):
        """Generate comprehensive performance report"""
        report = {
            'timestamp': datetime.now().isoformat(),
            'database': self.get_database_stats(),
            'snmp_cache': self.get_snmp_cache_stats(),
            'system': self.get_system_stats(),
            'performance': self.get_recent_performance()
        }
        
        return report
    
    def print_summary(self):
        """Print performance summary to console"""
        report = self.generate_report()
        
        print("=" * 60)
        print("GPON Application Performance Summary")
        print("=" * 60)
        
        # Database stats
        db_stats = report['database']
        if 'error' not in db_stats:
            print(f"📊 Database: {db_stats['database_size_mb']}MB, {db_stats['journal_mode']} mode")
            print(f"   Tables: {db_stats['table_stats']['olts_count']} OLTs, "
                  f"{db_stats['table_stats']['gpon_count']} ONUs, "
                  f"{db_stats['table_stats']['ponports_count']} ports")
        
        # SNMP cache stats
        cache_stats = report['snmp_cache']
        print(f"🔄 SNMP Cache: {cache_stats['total_entries']} entries, "
              f"{cache_stats['cache_hit_rate']}% hit rate")
        
        # System stats
        sys_stats = report['system']
        print(f"💻 System: {sys_stats['cpu_percent']}% CPU, "
              f"{sys_stats['memory_percent']}% RAM, "
              f"{sys_stats['disk_usage']}% disk")
        
        # Performance stats
        perf_stats = report['performance']
        if 'error' not in perf_stats:
            print(f"⚡ Performance: {perf_stats['recent_onu_activity']} active ONUs (1h), "
                  f"{perf_stats['daily_onu_growth']} new ONUs (24h)")
        
        print("=" * 60)

def main():
    """Main function for standalone execution"""
    monitor = PerformanceMonitor()
    monitor.print_summary()

if __name__ == "__main__":
    main()