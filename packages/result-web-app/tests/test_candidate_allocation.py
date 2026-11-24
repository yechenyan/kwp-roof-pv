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


def test_a_merged_building_group_is_assigned_once():
    first = Choice(0, 0, (3500, 1200, 600), "roof_parallel", ("a", "b"))
    second = Choice(0, 0, (3400, 1200, 600), "roof_parallel", ("a", "b"))
    requests = (Request("one", "site-one", 2000, 100, (first,)),
                Request("two", "site-two", 2000, 100, (second,)))
    selected, _ = assign(requests, (RoofBudget(4500, 250, (0, 0)),), (("a", "b"),))
    assert selected == {"one": first}
