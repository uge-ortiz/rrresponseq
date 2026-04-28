#!/usr/bin/env python3
"""
Test pattern scripting system.
Tests that the pattern script execution works correctly.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Import the helper functions and Track class
from sequencer import (
    Track, euclidean, rotate, mirror, invert, randomize,
    skip_every, only_every, fill_gap, gen_euclidean_random,
    gen_density, gen_mutation
)

def test_basic_script():
    """Test basic pattern script execution."""
    print("Testing basic pattern script...")

    # Create a track with a pattern
    track = Track(0)
    track.steps = 16
    track.pulses = 5
    track.pattern = euclidean(5, 16)

    # Setup scripts library with test scripts
    test_scripts_lib = {
        'rotate_test': {'code': 'pattern = rotate(pattern, 2)'},
        'mirror_test': {'code': 'pattern = mirror(pattern)'},
        'invert_test': {'code': 'pattern = invert(pattern)'},
        'skip_test': {'code': 'pattern = skip_every(pattern, 2)'},
        'chain_test': {'code': 'pattern = rotate(pattern, 1)\npattern = mirror(pattern)'},
        'empty_test': {'code': ''},
        'error_test': {'code': 'invalid python syntax here >>>'},
    }
    track.scripts_lib = test_scripts_lib

    print(f"Initial pattern: {track.pattern}")

    # Test 1: Rotate pattern
    track.pattern = euclidean(5, 16)
    track.script_id = 'rotate_test'
    track.execute_script()
    rotated = rotate(euclidean(5, 16), 2)
    assert track.pattern == rotated, f"Rotate failed: {track.pattern} != {rotated}"
    print("✓ Rotate script works")

    # Test 2: Mirror pattern
    track.pattern = euclidean(5, 16)
    track.script_id = 'mirror_test'
    track.execute_script()
    expected = mirror(euclidean(5, 16))
    assert track.pattern == expected, f"Mirror failed"
    print("✓ Mirror script works")

    # Test 3: Invert pattern
    track.pattern = euclidean(5, 16)
    track.script_id = 'invert_test'
    track.execute_script()
    expected = invert(euclidean(5, 16))
    assert track.pattern == expected, f"Invert failed"
    print("✓ Invert script works")

    # Test 4: Skip every N
    track.pattern = euclidean(5, 16)
    track.script_id = 'skip_test'
    track.execute_script()
    expected = skip_every(euclidean(5, 16), 2)
    assert track.pattern == expected, f"Skip every failed"
    print("✓ Skip every script works")

    # Test 5: Chain operations
    track.pattern = euclidean(5, 16)
    track.script_id = 'chain_test'
    track.execute_script()
    expected = mirror(rotate(euclidean(5, 16), 1))
    assert track.pattern == expected, f"Chain operations failed"
    print("✓ Chain operations work")

    # Test 6: Access to steps and pulses
    track.pattern = euclidean(5, 16)
    test_scripts_lib['access_test'] = {'code': 'assert steps == 16\nassert pulses == 5'}
    track.script_id = 'access_test'
    try:
        track.execute_script()
        print("✓ Access to steps and pulses works")
    except Exception as e:
        print(f"✗ Steps/pulses access failed: {e}")
        return False

    # Test 7: Empty script (should not crash)
    track.pattern = euclidean(5, 16)
    track.script_id = 'empty_test'
    track.execute_script()
    print("✓ Empty script handled correctly")

    # Test 8: Script with syntax error (should not crash)
    track.pattern = euclidean(5, 16)
    orig_pattern = track.pattern[:]
    track.script_id = 'error_test'
    track.execute_script()  # Should not raise
    # Pattern should remain unchanged after error
    assert track.pattern == orig_pattern, "Pattern should not change on script error"
    print("✓ Syntax errors handled gracefully")

    return True

def test_rebuild_calls_script():
    """Test that rebuild() calls execute_script()."""
    print("\nTesting rebuild integration...")

    track = Track(1)
    track.pulses = 4
    track.steps = 16

    # Setup scripts library
    track.scripts_lib = {
        'rotate_test': {'code': 'pattern = rotate(pattern, 3)'},
    }
    track.script_id = 'rotate_test'

    # Call rebuild which should execute the script
    track.rebuild()

    expected = rotate(euclidean(4, 16), 3)
    assert track.pattern == expected, f"Rebuild script integration failed"
    print("✓ rebuild() executes pattern script")

    return True

def test_load_calls_script():
    """Test that load() calls execute_script()."""
    print("\nTesting load integration...")

    track = Track(2)

    # Setup scripts library
    track.scripts_lib = {
        'rotate_test': {'code': 'pattern = rotate(pattern, 1)'},
    }

    # Create a snapshot with the new script_id field
    snap = {
        "channel": 2,
        "steps": 8,
        "pulses": 3,
        "pattern": [True, False, True, False, False, False, False, False],
        "script_id": "rotate_test",
        # ... other fields as needed
    }

    track.load(snap)

    expected_pattern = rotate([True, False, True, False, False, False, False, False], 1)
    assert track.pattern == expected_pattern, f"Load script integration failed"
    print("✓ load() executes pattern script")

    return True

if __name__ == "__main__":
    try:
        success = (
            test_basic_script() and
            test_rebuild_calls_script() and
            test_load_calls_script()
        )
        if success:
            print("\n✓ All tests passed!")
            sys.exit(0)
        else:
            print("\n✗ Some tests failed")
            sys.exit(1)
    except Exception as e:
        print(f"\n✗ Test error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
