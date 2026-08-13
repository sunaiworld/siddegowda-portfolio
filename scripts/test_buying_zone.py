import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))
from score_engine import calculate_buying_zone

test_cases = [
    # symbol, q_sc, v_sc, expected_zone (conceptual)
    ("Stock A (Extremely cheap but broken fundamentals)", 10, 26, "INVESTIGATE"),
    ("Stock B (High quality, very cheap)", 28, 24, "ADD AGGRESSIVELY"),
    ("Stock C (High quality, reasonable valuation)", 28, 16, "ACCUMULATE"),
    ("Stock D (High quality but slightly expensive)", 26, 6, "SMALL BUY"),
    ("Stock E (Expensive valuation, medium quality)", 20, 2, "WAIT"),
    ("Stock F (Exceptional quality, slightly premium)", 35, 12, "ACCUMULATE"),
]

print("Example classifications:")
for name, q, v, concept in test_cases:
    zone = calculate_buying_zone(q, v, None)
    print(f"- {name}: Q={q}, V={v} -> {zone}")
