#!/usr/bin/env python3
"""test_cbaa_standalone.py — Unit & Integration tests for CBAA/CBBA logic.
Verifies convergence, anti-flapping hysteresis, fair tie-breaking, and bundle allocation.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "docker" / "drone-agent"))

from swarm.cbaa import CBAA

def simulate_consensus(agents, max_rounds=20):
    """Simulates iterative mesh broadcast until all agents agree or max_rounds reached."""
    rounds = 0
    for r in range(max_rounds):
        rounds += 1
        changed_any = False
        current_vectors = [(a.me, list(a.y), list(a.z)) for a in agents]
        for a in agents:
            for sender_id, y_vec, z_vec in current_vectors:
                if sender_id != a.me:
                    if a.merge(y_vec, z_vec):
                        changed_any = True
        if not changed_any:
            break
    return rounds

def test_basic_convergence():
    print("--- TEST 1: Basic Convergence (5 agents, 5 tasks) ---")
    m = 5
    agents = [CBAA(i, m) for i in range(1, 6)]
    
    for i, a in enumerate(agents):
        scores = {j: 1000 - abs(i - j) * 100 for j in range(m)}
        a.reset(scores, set(range(m)))
        a.select()
        
    rounds = simulate_consensus(agents)
    winners = {j: agents[0].z[j] for j in range(m)}
    print(f"Converged in {rounds} rounds.")
    print(f"Winners: {winners}")
    
    for a in agents:
        assert a.z == agents[0].z, f"Agent {a.me} disagreed with consensus!"
        assert a.x is not None, f"Agent {a.me} has no active task!"
    print("PASS: Unanimous consensus and active task per drone verified.\n")

def test_fair_tie_breaking():
    print("--- TEST 2: Fair Tie-Breaking Test ---")
    m = 10
    # In CBBA mode with max_bundle=2, 5 agents can take all 10 tasks
    agents = [CBAA(i, m, max_bundle=2) for i in range(1, 6)]
    
    for a in agents:
        scores = {j: 2000 for j in range(m)}
        a.reset(scores, set(range(m)))
        a.select()
        
    rounds = simulate_consensus(agents)
    winners = {j: agents[0].z[j] for j in range(m)}
    print(f"Tie-breaking winners across 10 tasks: {winners}")
    
    counts = {i: sum(1 for w in winners.values() if w == i) for i in range(1, 6)}
    print(f"Task distribution by agent ID: {counts}")
    
    # Check that tasks are distributed fairly across all 5 agents (each gets 2 tasks!)
    for i in range(1, 6):
        assert counts[i] == 2, f"Agent {i} did not get 2 tasks! Got {counts[i]}"
    print("PASS: All 10 tasks assigned and perfectly balanced (2 tasks per drone)!\n")

def test_flapping_hysteresis():
    print("--- TEST 3: Anti-Flapping Hysteresis Test ---")
    m = 2
    # eps = 10 points threshold
    a1 = CBAA(1, m, eps=10)
    a2 = CBAA(2, m, eps=10)
    
    a1.reset({0: 1000}, {0})
    a1.select()
    a2.reset({0: 990}, {0})
    a2.select()
    
    simulate_consensus([a1, a2])
    print(f"Initial owner of Task 0: Agent {a1.z[0]} (bid: {a1.y[0]})")
    assert a1.z[0] == 1
    
    # CASE A: Small noise +2 points (1002 vs 1000). Since 1002 < 1000 + eps(10), Agent 2 must NOT flap/steal!
    a2.c[0] = 1002
    did_select = a2.select()
    print(f"Small noise +2 pts (1002 vs 1000): did a2 steal? {did_select} (a2 bundle: {a2.bundle})")
    assert not did_select, "Agent 2 flapped on small noise below hysteresis threshold!"
    
    # CASE B: Significant advantage +15 points (1015 vs 1000). Since 1015 >= 1000 + eps(10), Agent 2 takes over!
    a2.c[0] = 1015
    did_select_legit = a2.select()
    print(f"Legitimate advantage +15 pts (1015 vs 1000): did a2 take over? {did_select_legit}")
    assert did_select_legit, "Agent 2 failed to take over task with legitimate advantage!"
    
    a1.merge(a2.y, a2.z)
    print(f"Consensus updated! New owner of Task 0: Agent {a1.z[0]} (bid: {a1.y[0]})")
    assert a1.z[0] == 2
    print("PASS: Anti-flapping hysteresis successfully filtered noise while allowing true advantage!\n")

def test_bundle_allocation_cbba():
    print("--- TEST 4: Bundle Allocation (CBBA Multi-Task) ---")
    m = 6
    # 2 agents, 6 tasks, max_bundle = 3
    a1 = CBAA(1, m, max_bundle=3)
    a2 = CBAA(2, m, max_bundle=3)
    
    # a1 prefers even tasks (0, 2, 4), a2 prefers odd tasks (1, 3, 5)
    s1 = {0: 1000, 2: 900, 4: 800, 1: 500, 3: 400, 5: 300}
    s2 = {1: 1000, 3: 900, 5: 800, 0: 500, 2: 400, 4: 300}
    
    a1.reset(s1, set(range(m)))
    a1.select()
    a2.reset(s2, set(range(m)))
    a2.select()
    
    rounds = simulate_consensus([a1, a2])
    print(f"CBBA converged in {rounds} rounds.")
    print(f"Agent 1 bundle: {a1.bundle} (active task x: {a1.x})")
    print(f"Agent 2 bundle: {a2.bundle} (active task x: {a2.x})")
    print(f"Winners: {{j: a1.z[j] for j in range(m)}}")
    
    assert a1.bundle == [0, 2, 4]
    assert a2.bundle == [1, 3, 5]
    assert set(a1.bundle + a2.bundle) == set(range(m))
    print("PASS: All 6 tasks partitioned into bundles without conflicts!\n")

if __name__ == "__main__":
    test_basic_convergence()
    test_fair_tie_breaking()
    test_flapping_hysteresis()
    test_bundle_allocation_cbba()
    print("==================================================")
    print("ALL CBAA / CBBA UPGRADE TESTS PASSED (4/4)!")
    print("==================================================")
