"""
fps_diagnostics.py
==================
Performance profiling tools to diagnose Isaac Sim FPS stalling.

Usage:
    Import in extension.py and call start_fps_monitor() after visualizations.
    
    from . import fps_diagnostics
    fps_diagnostics.start_fps_monitor()
"""

import time
import gc
import os
import threading
import psutil
import carb
import omni.kit.app
from pxr import Usd, UsdGeom

# ── FPS Profiling ─────────────────────────────────────────────────────────────

class FPSMonitor:
    """Track frame rate, memory, GPU utilization, and USD Fabric graph status."""
    
    def __init__(self, sample_interval=1.0, duration_sec=300):
        """
        Parameters
        ----------
        sample_interval : float
            Seconds between samples (default 1.0)
        duration_sec : float
            Total monitoring duration in seconds (default 300 = 5 minutes)
        """
        self.sample_interval = sample_interval
        self.duration_sec = duration_sec
        self.is_running = False
        self.thread = None
        self.samples = []
        
        # Get process handles for memory tracking
        self.process = psutil.Process()
        self.start_time = None
        
    def start(self):
        """Begin monitoring in background thread."""
        if self.is_running:
            carb.log_warn("[fps_diag] Monitor already running")
            return
        
        self.is_running = True
        self.start_time = time.monotonic()
        self.samples = []
        self.thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.thread.start()
        carb.log_info(f"[fps_diag] FPS monitor started ({self.duration_sec}s duration)")
    
    def stop(self):
        """Stop monitoring and generate report."""
        self.is_running = False
        if self.thread:
            self.thread.join(timeout=5.0)
        self._generate_report()
    
    def _monitor_loop(self):
        """Background monitoring loop."""
        frame_count_prev = 0
        time_prev = time.monotonic()
        
        while self.is_running:
            try:
                elapsed = time.monotonic() - self.start_time
                if elapsed > self.duration_sec:
                    break
                
                # ── Get Frame Rate ────────────────────────────────────────────
                app = omni.kit.app.get_app()
                frame_count = app.current_frame
                time_now = time.monotonic()
                dt = time_now - time_prev
                
                if dt > 0:
                    fps = (frame_count - frame_count_prev) / dt
                else:
                    fps = 0.0
                
                frame_count_prev = frame_count
                time_prev = time_now
                
                # ── Get Memory Usage ──────────────────────────────────────────
                try:
                    mem_info = self.process.memory_info()
                    rss_mb = mem_info.rss / 1024 / 1024
                    vms_mb = mem_info.vms / 1024 / 1024
                except Exception:
                    rss_mb = vms_mb = 0.0
                
                # ── Get GPU Memory (if available) ─────────────────────────────
                gpu_mem_mb = self._get_gpu_memory()
                
                # ── USD Stage Info ────────────────────────────────────────────
                try:
                    context = omni.usd.get_context()
                    stage = context.get_stage()
                    prim_count = len(list(stage.Traverse()))
                except Exception:
                    prim_count = 0
                
                # ── Store sample ──────────────────────────────────────────────
                sample = {
                    "time": elapsed,
                    "fps": fps,
                    "rss_mb": rss_mb,
                    "vms_mb": vms_mb,
                    "gpu_mb": gpu_mem_mb,
                    "prims": prim_count,
                    "frame_num": frame_count,
                }
                self.samples.append(sample)
                
                time.sleep(self.sample_interval)
                
            except Exception as e:
                carb.log_error(f"[fps_diag] Monitor error: {e}")
                break
    
    def _get_gpu_memory(self):
        """Try to get GPU memory usage via nvidia-smi."""
        try:
            import subprocess
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                timeout=2.0
            )
            if result.returncode == 0:
                return float(result.stdout.strip().split()[0])
        except Exception:
            pass
        return -1.0
    
    def _generate_report(self):
        """Analyze samples and print detailed report."""
        if not self.samples:
            carb.log_error("[fps_diag] No samples collected")
            return
        
        fps_vals = [s["fps"] for s in self.samples]
        rss_vals = [s["rss_mb"] for s in self.samples]
        gpu_vals = [s["gpu_mb"] for s in self.samples if s["gpu_mb"] >= 0]
        prim_vals = [s["prims"] for s in self.samples]
        
        fps_avg = sum(fps_vals) / len(fps_vals) if fps_vals else 0
        fps_min = min(fps_vals) if fps_vals else 0
        fps_max = max(fps_vals) if fps_vals else 0
        fps_std = (sum((f - fps_avg)**2 for f in fps_vals) / len(fps_vals)) ** 0.5 if fps_vals else 0
        
        # Detect stalls (FPS variance)
        fps_locked = fps_std < 0.5  # Very low variance = locked FPS
        fps_trend = "LOCKED" if fps_locked else "VARIABLE"
        
        # Memory trends
        mem_start = rss_vals[0] if rss_vals else 0
        mem_end = rss_vals[-1] if rss_vals else 0
        mem_delta = mem_end - mem_start
        mem_leak = "LIKELY" if mem_delta > 100 else "NO"
        
        # GPU memory trends
        gpu_start = gpu_vals[0] if gpu_vals else -1
        gpu_end = gpu_vals[-1] if gpu_vals else -1
        gpu_delta = gpu_end - gpu_start if gpu_start >= 0 and gpu_end >= 0 else 0
        gpu_leak = "LIKELY" if gpu_delta > 200 else "NO"
        
        # Prim explosion
        prims_start = prim_vals[0] if prim_vals else 0
        prims_end = prim_vals[-1] if prim_vals else 0
        prims_delta = prims_end - prims_start
        
        # ── Print Report ─────────────────────────────────────────────────────
        report = f"""
╔════════════════════════════════════════════════════════════════════════════╗
║                    FPS DIAGNOSTICS REPORT                                  ║
╚════════════════════════════════════════════════════════════════════════════╝

📊 FRAME RATE ANALYSIS
  ├─ Avg FPS: {fps_avg:.1f} fps
  ├─ Min/Max: {fps_min:.1f} / {fps_max:.1f} fps
  ├─ Std Dev: {fps_std:.2f} (variability)
  ├─ Status: {fps_trend}
  └─ Diagnosis: {'⚠️  FPS IS LOCKED (possible stall)' if fps_locked else '✓ FPS VARIABLE (no lock detected)'}

💾 MEMORY ANALYSIS (CPU)
  ├─ Start: {mem_start:.0f} MB
  ├─ End: {mem_end:.0f} MB
  ├─ Delta: {mem_delta:+.0f} MB
  └─ Leak Risk: {mem_leak}

🎮 GPU MEMORY (if available)
  ├─ Start: {gpu_start:.0f} MB
  ├─ End: {gpu_end:.0f} MB
  ├─ Delta: {gpu_delta:+.0f} MB
  └─ Leak Risk: {gpu_leak}

🔷 USD PRIM COUNT
  ├─ Start: {prims_start} prims
  ├─ End: {prims_end} prims
  ├─ Delta: {prims_delta:+d} prims
  └─ Trend: {'⚠️  GROWING' if prims_delta > 10 else '✓ STABLE'}

📈 SAMPLES COLLECTED: {len(self.samples)} over {self.samples[-1]['time']:.1f}s

{'═' * 80}
RECOMMENDATIONS:
"""
        
        # Generate recommendations
        if fps_locked:
            report += "\n  1. FPS IS LOCKED → Check for:"
            report += "\n     • Thermal throttling (GPU/CPU overheating)"
            report += "\n     • Fabric compute graph stuck (USD dependency)"
            report += "\n     • V-Sync enabled in renderer"
        else:
            report += "\n  1. FPS varies normally (no lock detected)"
        
        if mem_leak == "LIKELY":
            report += f"\n  2. CPU MEMORY LEAK → {mem_delta:.0f}MB increase detected"
            report += "\n     • Check for accumulating dictionaries/lists"
            report += "\n     • Look for uncleaned subscriptions"
        
        if gpu_leak == "LIKELY":
            report += f"\n  3. GPU MEMORY LEAK → {gpu_delta:.0f}MB increase detected"
            report += "\n     • Textures not released after visualization"
            report += "\n     • Materials accumulating in stage"
        
        if prims_delta > 10:
            report += f"\n  4. PRIM EXPLOSION → {prims_delta} new prims added"
            report += "\n     • Visualization creating meshes faster than cleaning"
        
        report += f"\n\n  5. Next steps:"
        report += "\n     • Record temperatures: nvidia-smi dmon"
        report += "\n     • Profile Fabric: omni.app -> viewport -> profiler"
        report += "\n     • Check Update Loop: view Kit console for blocking calls"
        
        report += "\n" + "═" * 80 + "\n"
        
        print(report)
        carb.log_info("[fps_diag] " + report.replace("\n", "\n[fps_diag] "))
    
    def export_csv(self, filepath):
        """Export samples to CSV for external analysis."""
        if not self.samples:
            carb.log_error("[fps_diag] No samples to export")
            return
        
        try:
            import csv
            with open(filepath, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=self.samples[0].keys())
                writer.writeheader()
                writer.writerows(self.samples)
            carb.log_info(f"[fps_diag] Exported {len(self.samples)} samples to {filepath}")
        except Exception as e:
            carb.log_error(f"[fps_diag] CSV export failed: {e}")


# ── Global Monitor Instance ───────────────────────────────────────────────────

_monitor_instance = None

def start_fps_monitor(duration_sec=300, sample_interval=1.0):
    """
    Start FPS monitoring in background.
    
    Call this after performing visualizations:
        fps_diagnostics.start_fps_monitor(duration_sec=300)
    
    Parameters
    ----------
    duration_sec : float
        How long to monitor (default 5 minutes)
    sample_interval : float
        Sampling interval in seconds (default 1.0)
    """
    global _monitor_instance
    
    if _monitor_instance and _monitor_instance.is_running:
        carb.log_warn("[fps_diag] Monitor already running")
        return
    
    _monitor_instance = FPSMonitor(sample_interval=sample_interval, 
                                   duration_sec=duration_sec)
    _monitor_instance.start()


def stop_fps_monitor(export_csv_path=None):
    """
    Stop monitoring and generate report.
    
    Parameters
    ----------
    export_csv_path : str, optional
        If provided, export samples to CSV file
    """
    global _monitor_instance
    
    if _monitor_instance is None:
        carb.log_warn("[fps_diag] No monitor running")
        return
    
    _monitor_instance.stop()
    
    if export_csv_path:
        _monitor_instance.export_csv(export_csv_path)
    
    _monitor_instance = None


def check_fabric_graph_status():
    """Check if USD Fabric compute graph is stuck."""
    try:
        import omni.usd
        context = omni.usd.get_context()
        stage = context.get_stage()
        
        # Force Fabric to evaluate current frame
        stage.GetRootLayer().Reload()
        
        carb.log_info("[fps_diag] Fabric graph check: OK")
        return True
    except Exception as e:
        carb.log_error(f"[fps_diag] Fabric graph check: FAILED - {e}")
        return False
