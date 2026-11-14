"""API behaviour while MongoDB is unreachable (no MongoDB needed)."""

import asyncio
import json
import unittest
from unittest import mock

from pymongo import MongoClient
from pymongo.errors import ServerSelectionTimeoutError

from app import main


def tearDownModule():
    main.client.close()


class DatabaseDownTests(unittest.TestCase):
    def test_health_is_503_instead_of_a_traceback(self):
        # readiness probe while the MongoDB pod is still starting
        down = MongoClient("mongodb://127.0.0.1:9", serverSelectionTimeoutMS=500, connect=False)
        self.addCleanup(down.close)
        with mock.patch.object(main, "client", down):
            response = main.health()
        self.assertEqual(response.status_code, 503)
        body = json.loads(response.body)
        self.assertEqual(body["status"], "unavailable")
        self.assertNotIn("Topology Description", body["detail"])

    def test_connection_errors_become_503_with_one_log_line(self):
        exc = ServerSelectionTimeoutError("nim-profile-advisor-mongodb:27017: [Errno 111] Connection refused, "
                                          "Timeout: 10.0s, Topology Description: <TopologyDescription ...>")
        with self.assertLogs("uvicorn.error", level="WARNING") as logs:
            response = asyncio.run(main.database_unavailable(None, exc))
        self.assertEqual(response.status_code, 503)
        self.assertEqual(len(logs.records), 1)
        self.assertIn("Connection refused", logs.output[0])
        self.assertNotIn("Topology Description", logs.output[0])


if __name__ == "__main__":
    unittest.main()
