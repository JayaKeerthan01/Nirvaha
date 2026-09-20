import unittest
from database.db import init_db, get_zones, query
from agents.auto_assignment_engine import auto_assignment_engine


class TestAutoAssignmentEngine(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db(seed=True)

    def test_compute_all_zone_assignments(self):
        assignments = auto_assignment_engine.compute_all_zone_assignments()
        self.assertIsInstance(assignments, dict)
        zones = get_zones()
        for z in zones:
            self.assertIn(z["name"], assignments)
            entry = assignments[z["name"]]
            self.assertEqual(entry["zone"], z["name"])
            self.assertIn("risk_level", entry)

            # Check hospital auto-routing
            hosp = entry.get("hospital")
            if hosp:
                self.assertIn("hospital_name", hosp)
                self.assertIn("distance_km", hosp)
                self.assertIn("duration_min", hosp)
                self.assertIn("path", hosp)
                self.assertGreater(hosp["distance_km"], 0)

            # Check rescue team auto-assignment
            team = entry.get("rescue_team")
            if team and team["status"] == "recommended":
                self.assertIn("team_name", team)
                self.assertIn("distance_km", team)
                self.assertIn("duration_min", team)

    def test_no_duplicate_team_assignment_in_batch(self):
        assignments = auto_assignment_engine.compute_all_zone_assignments()
        assigned_team_ids = []
        for item in assignments.values():
            team = item.get("rescue_team")
            if team and team.get("status") == "recommended":
                assigned_team_ids.append(team["team_id"])

        # All assigned teams must be unique
        self.assertEqual(len(assigned_team_ids), len(set(assigned_team_ids)))


if __name__ == "__main__":
    unittest.main()
