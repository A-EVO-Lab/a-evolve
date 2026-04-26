---
name: adaptive-cruise-control
description: Implement Adaptive Cruise Control simulation with PID controllers for speed and distance control
---

# Adaptive Cruise Control Implementation

## Critical Requirements

### acc_system.py - AdaptiveCruiseControl class
The class must be **self-contained and work when instantiated with only config dict**. The test does:
```python
acc = AdaptiveCruiseControl(config)
result = acc.compute(ego_speed=20.0, lead_speed=None, distance=None, dt=0.1)
```

**IMPORTANT:** Initialize BOTH `speed_pid` and `distance_pid` inside `__init__` with default gains (e.g., from config or reasonable defaults). Do NOT leave them as None. The test creates the ACC object with only the config and immediately calls compute().

```python
class AdaptiveCruiseControl:
    def __init__(self, config):
        self.set_speed = config['acc_settings']['set_speed']
        self.time_headway = config['acc_settings']['time_headway']
        self.min_gap = config['acc_settings']['minimum_gap']
        self.ttc_threshold = config['acc_settings']['emergency_ttc_threshold']
        self.accel_limits = config['vehicle']['acceleration_limits']
        
        # MUST initialize both PIDs with default gains - NOT None!
        self.speed_pid = PIDController(kp=1.0, ki=0.1, kd=0.05)
        self.distance_pid = PIDController(kp=0.5, ki=0.05, kd=0.1)
        self._prev_mode = 'cruise'
    
    def set_pid_gains(self, speed_gains, distance_gains):
        """Allow simulation.py to set tuned gains."""
        self.speed_pid = PIDController(**speed_gains)
        self.distance_pid = PIDController(**distance_gains)
    
    def compute(self, ego_speed, lead_speed, distance, dt):
        # Returns (acceleration_cmd, mode, distance_error)
        ...
```

### Distance Control - Safe Following Distance
The test checks: `distance >= 0.9 * (ego_speed * 1.5 + 10.0)` for ALL follow-mode rows between t=30-60s.

**Strategy for passing distance control:**
- Safe distance = ego_speed * time_headway + min_gap
- Use aggressive distance PID gains to maintain safe distance
- When transitioning to follow mode, immediately target safe distance
- Consider using feedforward: if distance is below safe distance, apply stronger braking
- PID distance gains should prioritize not dropping below safe distance

### Tuning Tips
- Speed PID: kp=2.0-3.0, ki=0.1-0.3, kd=0.1-0.3 (for rise time <10s, overshoot <5%)
- Distance PID: kp=0.8-1.5, ki=0.05-0.2, kd=0.3-0.8 (for maintaining safe distance)
- The distance controller's error should be: `safe_distance - actual_distance` (positive when too close)
- When in follow mode and distance < safe_distance * 0.95, apply emergency-like deceleration

### simulation.py
- Read PID gains from tuning_results.yaml
- Do NOT embed auto-tuning logic
- Use sensor_data.csv for lead vehicle data
- Output exactly 1501 rows to simulation_results.csv

### Output Files
1. `pid_controller.py` - PIDController class
2. `acc_system.py` - AdaptiveCruiseControl class  
3. `simulation.py` - Simulation runner
4. `tuning_results.yaml` - PID gains
5. `simulation_results.csv` - 1501 rows: time,ego_speed,acceleration_cmd,mode,distance_error,distance,ttc
6. `acc_report.md` - Report with system design, PID tuning, results
