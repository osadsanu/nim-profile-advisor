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


class ModelListTests(unittest.TestCase):
    def test_models_carry_the_nvaie_tag(self):
        # GET /api/models: the Models tab draws the "NVIDIA AI Enterprise Supported" icon from this flag
        def doc(model_id, labels):
            return {"_id": model_id, "name": model_id, "display_name": model_id.upper(), "category": "LLM / VLM",
                    "image": f"nvcr.io/nim/x/{model_id}", "fetch": {"status": "ok"}, "labels": labels}
        store = mock.Mock(models={
            "b": doc("b", {"general": ["NVIDIA NIM"], "productNames": ["nv-ai-enterprise"]}),
            "a": doc("a", {"general": ["NVIDIA NIM", "NVIDIA AI Enterprise Supported"]}),
            "c": doc("c", None),
        })
        with mock.patch.object(main, "store", store):
            rows = main.models(q=None)
        self.assertEqual([(r["_id"], r["nvaie_supported"]) for r in rows], [("a", True), ("b", False), ("c", False)])
        self.assertNotIn("labels", rows[0])  # the list stays small: only the flag is added


if __name__ == "__main__":
    unittest.main()
