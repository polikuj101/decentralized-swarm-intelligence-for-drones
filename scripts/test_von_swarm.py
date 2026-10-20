#!/usr/bin/env python3
"""test_von_swarm.py — Benchmark and evaluation of Von (ModernBERT option-marker)
for Drone Swarm System 1 tactical decisions with doctrinal operational criteria.
"""
import time
import json
import numpy as np
import von

# Operational doctrine criteria: mapping flight/telemetry conditions to tactical actions
CHOICES = {
    "DECLARE_FAILOVER": "When a peer drone is silent or unresponsive for over 3 seconds, declare failover so its sector is reassigned.",
    "EVADE_JAMMING": "When high RF noise floor, degraded comms, or electronic warfare jamming is detected, evade jamming.",
    "HOLD_SECTOR": "When GPS is lost or degraded but IMU drift is low to moderate, continue holding the assigned sector.",
    "HOLD_POSITION": "When navigation drift is dangerously high or critical collision risk exists, hover and hold position.",
    "RETURN_HOME": "When battery is critically low or the drone is completely isolated with no comms, abort and return to base.",
    "SCAN_TARGET": "When the drone is idle with no active sector and an unconfirmed target is nearby, scan the target."
}

SCENARIOS = [
    {
        "name": "Peer drone heartbeat lost (Failover needed)",
        "state": "phase nominal. link good. gps ok. sigma low. silent long. lost some. task sector. Peer drone-3 has been silent for 4.2 seconds, heartbeat missing.",
        "expected": "DECLARE_FAILOVER"
    },
    {
        "name": "EW Jamming attack detected (Degraded comms)",
        "state": "phase degraded_comms. link poor. gps ok. sigma low. silent short. lost none. task sector. High RF noise floor and severe packet loss from enemy EW jammer detected.",
        "expected": "EVADE_JAMMING"
    },
    {
        "name": "GPS lost with mild drift (Hold sector)",
        "state": "phase degraded_nav. link good. gps lost. sigma mid. silent none. lost none. task sector. Optical flow maintaining local hold in assigned sector.",
        "expected": "HOLD_SECTOR"
    },
    {
        "name": "Critical navigation failure & high drift (Hold position / hover)",
        "state": "phase degraded_nav. link fair. gps lost. sigma high. silent none. lost none. task sector. IMU error exceeding 30 meters drift, hazard of collision.",
        "expected": "HOLD_POSITION"
    },
    {
        "name": "Critical battery / isolation (Return home)",
        "state": "phase critical. link poor. gps lost. sigma critical. silent long. lost most. task sector. Battery depleted to 11%, swarm link dead.",
        "expected": "RETURN_HOME"
    },
    {
        "name": "Idle drone with newly detected target (Scan target)",
        "state": "phase nominal. link good. gps ok. sigma low. silent none. lost none. task idle. Open target T1 detected within sensor range.",
        "expected": "SCAN_TARGET"
    }
]

def main():
    print("=" * 70)
    print("VON SYSTEM 1 DECISION EVALUATION ON DRONE SWARM TACTICAL STATES")
    print("=" * 70)
    
    print("Warming up model...")
    _ = von.decide("warmup", CHOICES)
    
    correct = 0
    latencies = []
    
    print("\n--- SCENARIO EVALUATION ---")
    for sc in SCENARIOS:
        t0 = time.perf_counter()
        ans = von.decide(sc["state"], CHOICES, instructions="Select the correct military drone swarm tactical action for this telemetry state:")
        dt_ms = (time.perf_counter() - t0) * 1000.0
        latencies.append(dt_ms)
        
        is_ok = (ans.choice == sc["expected"])
        if is_ok:
            correct += 1
            status = "PASS"
        else:
            status = f"FAIL (got {ans.choice})"
            
        print(f"\nScenario: {sc['name']}")
        print(f"  State: {sc['state'][:80]}...")
        print(f"  Expected: {sc['expected']:<18} | Verdict: [{status}] (latency: {dt_ms:.1f}ms)")
        print(f"  Chosen:   {ans.choice} (P={ans.probabilities.get(ans.choice, 0.0):.3f}, Conf={ans.confidence:.3f})")
        sorted_probs = sorted(ans.probabilities.items(), key=lambda x: x[1], reverse=True)[:3]
        print(f"  Top probs: {sorted_probs}")
        
    print("\n" + "=" * 70)
    print("ACCURACY AND LATENCY SUMMARY")
    print("=" * 70)
    print(f"Accuracy: {correct}/{len(SCENARIOS)} ({correct/len(SCENARIOS)*100:.1f}%)")
    print(f"Mean Latency: {np.mean(latencies):.2f} ms")
    print("=" * 70)

if __name__ == "__main__":
    main()
