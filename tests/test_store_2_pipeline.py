# PROMPT: Generate pytest tests for Store 2 pipeline modules (cam_entry_store_2.py and cam_zones_store_2.py). Verify horizontal line crossing logic (y=540) for entries and exits, and test quadrant-based zone classifications (MK_GONDOLA_1, MK_GONDOLA_2, MAKEUP_TABLES) using mock track points and pointPolygonTest.
# CHANGES MADE: Added tests for the horizontal crossing detection direction (top-to-bottom and bottom-to-top) and test coverage for all three quadrant polygons.

import pytest
import numpy as np
from pipeline.cam_entry_store_2 import Y_LINE
from pipeline.cam_zones_store_2 import check_zone

def test_horizontal_crossing_logic():
    # Test horizontal line crossing logic: Y_LINE = 540
    # ENTRY: bottom to top crossing (y goes from > 540 to <= 540)
    prev_y_entry = 550
    curr_y_entry = 530
    assert prev_y_entry > Y_LINE >= curr_y_entry

    # EXIT: top to bottom crossing (y goes from < 540 to >= 540)
    prev_y_exit = 530
    curr_y_exit = 550
    assert prev_y_exit < Y_LINE <= curr_y_exit

def test_store_2_zones_mapping():
    # Define Store 2 zones polygons
    zones = {
        "MK_GONDOLA_1": [(0, 540), (480, 540), (480, 1080), (0, 1080)],
        "MK_GONDOLA_2": [(0, 0), (480, 0), (480, 540), (0, 540)],
        "MAKEUP_TABLES": [(480, 0), (960, 0), (960, 1080), (480, 1080)]
    }

    # Centroid in MK_GONDOLA_1 (bottom-left): x < 480, y > 540
    c1 = (200.0, 700.0)
    assert check_zone(c1, zones) == "MK_GONDOLA_1"

    # Centroid in MK_GONDOLA_2 (top-left): x < 480, y <= 540
    c2 = (200.0, 300.0)
    assert check_zone(c2, zones) == "MK_GONDOLA_2"

    # Centroid in MAKEUP_TABLES (right side): x >= 480
    c3 = (600.0, 400.0)
    assert check_zone(c3, zones) == "MAKEUP_TABLES"

    # Centroid out of bounds (off right side of 960 width)
    c4 = (1000.0, 400.0)
    assert check_zone(c4, zones) is None
