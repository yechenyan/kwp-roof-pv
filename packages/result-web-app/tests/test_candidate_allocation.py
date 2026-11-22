"""Exercise joint selection on small competing roof options."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from candidate_allocation import Choice, Request, RoofBudget, assign


def test_joint_proposals_move_the_first_choice_to_resolve_shared_array_competition():
    first = Choice(0, 0, (3500, 1200, 600), "roof_parallel", ("a",))
    alternate = Choice(1, 0, (3300, 1200, 600), "roof_parallel", ("b",))
    other = Choice(0, 0, (3400, 1200, 600), "roof_parallel", ("a",))
    requests = (Request("one", "site-one", 2000, 100, (first, alternate)),
                Request("two", "site-two", 2000, 100, (other,)))
    roofs = (RoofBudget(5000, 400, (0, 0)), RoofBudget(5000, 400, (20, 0)))
    proposals, stats = assign(requests, roofs, ())
    assert proposals == {"one": alternate, "two": other}
    assert stats["optimal_components"] == stats["components"] == 1


def test_distinct_groups_can_share_one_roof_within_its_capacity():
    first = Choice(0, 0, (3500, 1200, 600), "roof_parallel", ("a",))
    second = Choice(0, 1, (3400, 1200, 600), "roof_parallel", ("b",))
    requests = (Request("one", "site-one", 2000, 100, (first,)),
                Request("two", "site-two", 2000, 100, (second,)))
    roomy, _ = assign(requests, (RoofBudget(4500, 250, (0, 0)),), ())
    full, _ = assign(requests, (RoofBudget(3500, 250, (0, 0)),), ())
    assert roomy == {"one": first, "two": second}
    assert full == {"one": first}
