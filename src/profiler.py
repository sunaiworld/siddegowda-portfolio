import time
import logging
from contextlib import contextmanager

log = logging.getLogger(__name__)

class Profiler:
    def __init__(self):
        self.categories = {
            "Yahoo/yfinance": 0.0,
            "Google Sheets": 0.0,
            "Python processing": 0.0,
            "Other": 0.0
        }
        self.counters = {
            "Stocks processed": 0,
            "Yahoo requests": 0,
            "Sheets requests": 0,
            "Rows written": 0,
            "Formatting operations": 0
        }
        self.stages = []
        self.overall_start = time.time()
        self._current_stage = {}

    def increment(self, counter_name, count=1):
        if counter_name in self.counters:
            self.counters[counter_name] += count
        else:
            self.counters[counter_name] = count

    def start_stage(self, name):
        self._current_stage[name] = time.time()

    def stop_stage(self, name, category="Other"):
        if name in self._current_stage:
            elapsed = time.time() - self._current_stage.pop(name)
            log.info(f"{name.ljust(40, '.')} {elapsed:.1f} sec")
            if category in self.categories:
                self.categories[category] += elapsed
            else:
                self.categories["Other"] += elapsed
            
            self.stages.append((name, elapsed))
            return elapsed
        return 0.0

    @contextmanager
    def stage(self, name, category="Other"):
        self.start_stage(name)
        try:
            yield
        finally:
            self.stop_stage(name, category)

    def print_summary(self):
        overall_elapsed = time.time() - self.overall_start
        
        # Sort stages for bottlenecks
        sorted_stages = sorted(self.stages, key=lambda x: x[1], reverse=True)
        
        def format_time(seconds):
            mins, secs = divmod(int(seconds), 60)
            return f"{mins}m {secs}s" if mins > 0 else f"{secs}s"

        print("\n" + "="*10 + " PERFORMANCE SUMMARY " + "="*10)
        print(f"Total runtime: {format_time(overall_elapsed)}\n")
        
        print(f"Yahoo/yfinance:       {format_time(self.categories['Yahoo/yfinance']):>8}")
        print(f"Google Sheets:         {format_time(self.categories['Google Sheets']):>8}")
        print(f"Python processing:     {format_time(self.categories['Python processing']):>8}")
        print(f"Other:                 {format_time(self.categories['Other']):>8}\n")
        
        for k, v in self.counters.items():
            print(f"{k}: {v:,}")
            
        print("\nTop bottlenecks:")
        for i, (name, elapsed) in enumerate(sorted_stages[:5], 1):
            # Clean up [XX] from name
            clean_name = name.split("]", 1)[-1].strip() if "]" in name else name
            print(f"{i}. {clean_name} ({elapsed:.1f} sec)")
            
        print("="*41 + "\n")

# Global singleton
profiler = Profiler()
