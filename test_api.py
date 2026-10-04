"""Run with:  python -m unittest test_api -v"""
import os
import tempfile
import unittest

import app as codic


class ApiTests(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.remove(self.path)
        codic.app.config["DATABASE"] = self.path
        codic.init_db()
        self.c = codic.app.test_client()

    def tearDown(self):
        if os.path.exists(self.path):
            os.remove(self.path)

    def test_seed_data(self):
        stats = self.c.get("/api/stats").get_json()
        self.assertEqual(stats["total_assets"], 6)
        self.assertEqual(stats["open_tickets"], 2)
        self.assertEqual(stats["resolved_tickets"], 1)

    def test_create_asset_and_reject_duplicate(self):
        body = {"name": "Dell Latitude", "tag": "codic-lp-104", "category": "Laptop", "dept": "HR"}
        r = self.c.post("/api/assets", json=body)
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.get_json()["status"], "In Storage")
        self.assertEqual(self.c.post("/api/assets", json=body).status_code, 409)

    def test_invalid_tag_rejected(self):
        body = {"name": "X", "tag": "bad tag", "category": "Laptop", "dept": "HR"}
        self.assertEqual(self.c.post("/api/assets", json=body).status_code, 400)

    def test_toggle_requires_employee(self):
        storage_id = next(a["id"] for a in self.c.get("/api/assets").get_json() if a["status"] == "In Storage")
        self.assertEqual(self.c.patch(f"/api/assets/{storage_id}/toggle", json={}).status_code, 400)
        r = self.c.patch(f"/api/assets/{storage_id}/toggle", json={"employee": "Sam"})
        self.assertEqual(r.get_json()["status"], "Assigned")

    def test_ticket_flow(self):
        r = self.c.post("/api/tickets", json={"requester": "A", "summary": "Screen broken", "tag": "CODIC-LP-101", "priority": "High"})
        self.assertEqual(r.get_json()["id"], "TKT-1004")
        r = self.c.patch("/api/tickets/TKT-1004", json={"status": "Resolved"})
        self.assertEqual(r.get_json()["status"], "Resolved")

    def test_ticket_validation(self):
        r = self.c.post("/api/tickets", json={"requester": "A", "summary": "bad", "priority": "High"})
        self.assertEqual(r.status_code, 400)
        r = self.c.post("/api/tickets", json={"requester": "A", "summary": "Valid summary", "tag": "NOPE-1", "priority": "High"})
        self.assertEqual(r.status_code, 404)


if __name__ == "__main__":
    unittest.main()
