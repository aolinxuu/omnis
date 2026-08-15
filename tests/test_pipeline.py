"""Stdlib-only tests. No pytest, no GPU, no clips required.

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import detect  # noqa: E402
from pipeline.build_payload import derive_lost, validate  # noqa: E402
from pipeline.common import (DATA, ROOT, distance_m, from_iso,  # noqa: E402
                             load_cameras, load_json, load_t0, to_iso)
from pipeline.reachability import reachable  # noqa: E402


class TestGeo(unittest.TestCase):
    def test_known_distance(self):
        # 2nd & Battery -> 2nd & Pike, ~0.8 km down the corridor.
        cams = load_cameras()
        idx = {c["id"]: c for c in cams["cameras"]}
        d = distance_m(idx["CMR-0185"], idx["CMR-0302"])
        self.assertGreater(d, 600)
        self.assertLess(d, 1100)

    def test_symmetric_and_zero(self):
        a = {"lat": 47.6, "lon": -122.33}
        b = {"lat": 47.61, "lon": -122.34}
        self.assertAlmostEqual(distance_m(a, a), 0.0)
        self.assertAlmostEqual(distance_m(a, b), distance_m(b, a), delta=1.0)


class TestTime(unittest.TestCase):
    def test_roundtrip(self):
        t0 = load_t0(load_cameras())
        for sec in (0, 61.5, -30, 3600):
            self.assertAlmostEqual(from_iso(t0, to_iso(t0, sec)), round(sec), delta=1.0)

    def test_iso_has_offset(self):
        t0 = load_t0(load_cameras())
        self.assertRegex(to_iso(t0, 12), r"[+-]\d{2}:\d{2}$")


class TestReachability(unittest.TestCase):
    def setUp(self):
        self.cams = load_cameras()

    def test_grows_monotonically(self):
        counts = [reachable(self.cams["cameras"], self.cams["seed"], m)["count"]
                  for m in (1, 3, 6, 10)]
        self.assertEqual(counts, sorted(counts))

    def test_seed_camera_always_reachable(self):
        r = reachable(self.cams["cameras"], self.cams["seed"], 1)
        self.assertEqual(r["cameras"][0]["id"], self.cams["seed"]["camera"])
        self.assertEqual(r["cameras"][0]["distance_m"], 0.0)

    def test_sorted_by_distance_only(self):
        r = reachable(self.cams["cameras"], self.cams["seed"], 8)
        d = [c["distance_m"] for c in r["cameras"]]
        self.assertEqual(d, sorted(d))

    def test_too_wide_refuses_to_bluff(self):
        r = reachable(self.cams["cameras"], self.cams["seed"], 40, 20)
        self.assertEqual(r["state"], "too_wide")
        self.assertEqual(r["cameras"], [])
        self.assertEqual(r["count"], r["total"])

    def test_no_too_wide_without_uncertainty(self):
        # With zero slop the answer is a real (if large) set, not a refusal.
        r = reachable(self.cams["cameras"], self.cams["seed"], 40, 0)
        self.assertEqual(r["state"], "ok")


class TestColourScoring(unittest.TestCase):
    def test_colour_never_filters(self):
        """A total colour mismatch must not zero out a strong detection."""
        worst = detect.colour_match(detect.RIDER_HUE + 90)
        self.assertGreaterEqual(worst, detect.COLOUR_FLOOR)
        self.assertGreater(0.9 * worst, 0.0)

    def test_exact_match_is_one(self):
        self.assertAlmostEqual(detect.colour_match(detect.RIDER_HUE), 1.0, places=6)

    def test_missing_hue_falls_back_to_floor(self):
        self.assertEqual(detect.colour_match(None), detect.COLOUR_FLOOR)

    def test_hue_wraps_around_the_wheel(self):
        self.assertAlmostEqual(detect.hue_distance(2, 178), 4.0)

    def test_monotonic_falloff(self):
        a = detect.colour_match(detect.RIDER_HUE + 5)
        b = detect.colour_match(detect.RIDER_HUE + 25)
        self.assertGreater(a, b)


class TestClustering(unittest.TestCase):
    def make(self, times):
        return [{"t_clip": t, "score": 0.5, "cls": "scooter", "bbox": [0, 0, 1, 1],
                 "hue": 14} for t in times]

    def test_one_pass_is_one_cluster(self):
        groups = detect.cluster(self.make([10.0, 10.3, 10.7, 11.2]), gap_s=2.0)
        self.assertEqual(len(groups), 1)

    def test_separate_passes_split(self):
        groups = detect.cluster(self.make([10.0, 10.5, 40.0, 40.4]), gap_s=2.0)
        self.assertEqual(len(groups), 2)

    def test_empty(self):
        self.assertEqual(detect.cluster([]), [])

    def test_summary_uses_centre_and_peak(self):
        dets = self.make([10.0, 12.0])
        dets[1]["score"] = 0.9
        s = detect.summarise(dets)
        self.assertAlmostEqual(s["t_clip"], 11.0)
        self.assertAlmostEqual(s["score"], 0.9)
        self.assertEqual(s["n_frames"], 2)


class TestContractBoundary(unittest.TestCase):
    def test_states_are_contract_legal(self):
        cam = {"id": "CMR-0185", "lat": 47.6, "lon": -122.3}
        t0 = load_t0(load_cameras())
        for score, expect in ((0.9, "linked"), (0.45, "confirmed"), (0.1, "detected")):
            rec = detect.to_contract(
                cam, {"t_clip": 5.0, "score": score, "cls": "scooter",
                      "bbox": [1, 2, 3, 4], "hue": 14, "n_frames": 3, "span_s": 1.0},
                offset_s=60.0, t0=t0, seq=1, link=0.55, confirm=0.35)
            self.assertEqual(rec["state"], expect)
            self.assertIn("camera_id", rec)      # not "camera"
            self.assertIn("conf", rec)           # not "confidence"
            datetime.fromisoformat(rec["t"])     # not integer minutes

    def test_only_linked_carries_track_id(self):
        cam = {"id": "CMR-0185", "lat": 47.6, "lon": -122.3}
        t0 = load_t0(load_cameras())
        base = {"t_clip": 5.0, "cls": "scooter", "bbox": [1, 2, 3, 4], "hue": 14,
                "n_frames": 3, "span_s": 1.0}
        linked = detect.to_contract(cam, {**base, "score": 0.9}, 0, t0, 1, 0.55, 0.35)
        weak = detect.to_contract(cam, {**base, "score": 0.4}, 0, t0, 2, 0.55, 0.35)
        self.assertEqual(linked["track_id"], detect.SUBJECT_TRACK)
        self.assertNotIn("track_id", weak)

    def test_clip_offset_is_applied(self):
        cam = {"id": "CMR-0185", "lat": 47.6, "lon": -122.3}
        t0 = load_t0(load_cameras())
        rec = detect.to_contract(
            cam, {"t_clip": 10.0, "score": 0.9, "cls": "scooter", "bbox": [0, 0, 1, 1],
                  "hue": 14, "n_frames": 1, "span_s": 0.0},
            offset_s=100.0, t0=t0, seq=1, link=0.55, confirm=0.35)
        self.assertAlmostEqual(from_iso(t0, rec["t"]), 110.0, delta=1.0)


class TestNoCircularEval(unittest.TestCase):
    """The wave log is scoring only. A detector that reads it proves nothing."""

    def test_detect_never_references_the_wave_log(self):
        src = (ROOT / "pipeline" / "detect.py").read_text()
        code = "\n".join(
            line for line in src.splitlines()
            if not line.strip().startswith("#")
        )
        _, _, after_docstring = code.partition('"""')
        _, _, body = after_docstring.partition('"""')
        self.assertNotIn("wave-log", body)
        self.assertNotIn("wave_log", body)
        self.assertNotIn("ground_truth", body)

    def test_wave_log_is_not_importable_from_detect(self):
        self.assertFalse(hasattr(detect, "load_waves"))


class TestPayloadValidation(unittest.TestCase):
    def base(self):
        return {
            "meta": {}, "tracks": [], "predictions": [], "events": [],
            "cameras": [{"id": "CMR-0185", "name": "x", "lat": 47.6, "lon": -122.3,
                         "kind": "sdot", "alive": True}],
            "sightings": [{"id": "S-0001", "t": "2026-08-15T18:02:04-07:00",
                           "camera_id": "CMR-0185", "lat": 47.6, "lon": -122.3,
                           "class": "scooter", "state": "linked", "conf": 0.9,
                           "track_id": "T-SUBJ"}],
            "ground_truth": [{"camera_id": "CMR-0185",
                              "t": "2026-08-15T18:02:02-07:00", "kind": "wave"}],
        }

    def test_clean_payload_passes(self):
        self.assertEqual(validate(self.base()), [])

    def test_missing_key_caught(self):
        p = self.base(); del p["events"]
        self.assertTrue(any("events" in m for m in validate(p)))

    def test_bad_state_caught(self):
        p = self.base(); p["sightings"][0]["state"] = "confirmedish"
        self.assertTrue(any("bad state" in m for m in validate(p)))

    def test_unknown_camera_caught(self):
        p = self.base(); p["sightings"][0]["camera_id"] = "CMR-9999"
        self.assertTrue(any("unknown camera" in m for m in validate(p)))

    def test_no_linked_sighting_is_flagged(self):
        """evalPanel() scores only `linked` - silently scoring 0 is the trap."""
        p = self.base()
        p["sightings"][0]["state"] = "confirmed"
        p["sightings"][0].pop("track_id")
        self.assertTrue(any("linked" in m for m in validate(p)))

    def test_derive_lost_on_long_gap(self):
        t0 = load_t0(load_cameras())
        s = [{"id": "S-0001", "t": to_iso(t0, 0), "camera_id": "CMR-0185",
              "lat": 47.6, "lon": -122.3, "class": "scooter", "state": "linked",
              "conf": 0.9, "track_id": "T-SUBJ"},
             {"id": "S-0002", "t": to_iso(t0, 400), "camera_id": "CMR-0302",
              "lat": 47.6, "lon": -122.3, "class": "scooter", "state": "linked",
              "conf": 0.8, "track_id": "T-SUBJ"}]
        lost = derive_lost(s, t0, gap_s=150)
        self.assertEqual(len(lost), 1)
        self.assertEqual(lost[0]["state"], "lost")
        self.assertLess(lost[0]["conf"], 0.9)      # decayed

    def test_derive_lost_quiet_on_short_gap(self):
        t0 = load_t0(load_cameras())
        s = [{"id": "S-0001", "t": to_iso(t0, 0), "camera_id": "CMR-0185",
              "lat": 47.6, "lon": -122.3, "class": "scooter", "state": "linked",
              "conf": 0.9, "track_id": "T-SUBJ"},
             {"id": "S-0002", "t": to_iso(t0, 40), "camera_id": "CMR-0302",
              "lat": 47.6, "lon": -122.3, "class": "scooter", "state": "linked",
              "conf": 0.8, "track_id": "T-SUBJ"}]
        self.assertEqual(derive_lost(s, t0, gap_s=150), [])


class TestDataFiles(unittest.TestCase):
    def test_cameras_use_lon_not_lng(self):
        for c in load_cameras()["cameras"]:
            self.assertIn("lon", c)
            self.assertNotIn("lng", c)

    def test_seed_is_a_real_camera(self):
        cams = load_cameras()
        ids = {c["id"] for c in cams["cameras"]}
        self.assertIn(cams["seed"]["camera"], ids)

    def test_wave_log_cameras_all_exist(self):
        cams = {c["id"] for c in load_cameras()["cameras"]}
        for w in load_json(DATA / "wave-log.json")["waves"]:
            self.assertIn(w["camera_id"], cams)

    def test_wave_log_is_chronological(self):
        ts = [w["t"] for w in load_json(DATA / "wave-log.json")["waves"]]
        self.assertEqual(ts, sorted(ts))


class TestRouting(unittest.TestCase):
    """pipeline/roadgraph.py + pipeline/predict.py: classical, no LLM."""

    def test_bearing_and_turn(self):
        from pipeline.roadgraph import bearing_deg, turn_deg
        n = bearing_deg({"lat": 47.60, "lon": -122.33}, {"lat": 47.61, "lon": -122.33})
        e = bearing_deg({"lat": 47.60, "lon": -122.33}, {"lat": 47.60, "lon": -122.32})
        self.assertAlmostEqual(n, 0.0, places=0)
        self.assertAlmostEqual(e, 90.0, places=0)
        self.assertAlmostEqual(turn_deg(350.0, 10.0), 20.0)
        self.assertAlmostEqual(turn_deg(10.0, 350.0), -20.0)
        self.assertEqual(turn_deg(0.0, 180.0), 180.0)

    def _grid(self):
        # 3x3 grid, 100 m spacing, two-way, node ids r*3+c
        from pipeline.roadgraph import RoadGraph
        nodes, edges = {}, []
        for r in range(3):
            for c in range(3):
                nodes[str(r * 3 + c)] = [47.600 + r * 0.0009, -122.330 + c * 0.00133]
        for r in range(3):
            for c in range(3):
                i = r * 3 + c
                if c < 2: edges += [[i, i + 1, 100.0, "EW"], [i + 1, i, 100.0, "EW"]]
                if r < 2: edges += [[i, i + 3, 100.0, "NS"], [i + 3, i, 100.0, "NS"]]
        return RoadGraph({"nodes": nodes, "edges": edges})

    def test_dijkstra_on_grid(self):
        g = self._grid()
        d, path = g.shortest_path(0, 8)
        self.assertAlmostEqual(d, 400.0)
        self.assertEqual(path[0], 0); self.assertEqual(path[-1], 8); self.assertEqual(len(path), 5)
        self.assertIsNone(g.shortest_path(0, 8, max_m=300))

    def test_prediction_prefers_straight_on_and_sums_to_one(self):
        from pipeline.predict import predict
        g = self._grid()
        pos = lambda n: {"lat": g.pos[n]["lat"], "lon": g.pos[n]["lon"]}
        cams = [dict(id=f"C{n}", name=f"cam{n}", alive=True, **pos(n)) for n in range(9)]
        # travelling east along the bottom row: node 0 -> node 1 seen; candidates 2 (straight), 4 (turn), ...
        seen = [dict(id="S1", t="2026-08-15T18:00:00-07:00", camera_id="C0", state="linked", track_id="T-SUBJ", **pos(0)),
                dict(id="S2", t="2026-08-15T18:00:20-07:00", camera_id="C1", state="linked", track_id="T-SUBJ", **pos(1))]
        p = predict(cams, seen, g, horizon_s=120)
        self.assertIsNotNone(p)
        self.assertEqual(p["at_camera"], "C1")
        self.assertAlmostEqual(sum(b["p"] for b in p["branches"]), 1.0, places=3)
        self.assertEqual(p["branches"][0]["camera_id"], "C2", "straight on should rank first")
        self.assertTrue(all(len(b["path"]) >= 2 for b in p["branches"]))
        for b in p["branches"]:
            self.assertEqual(b["path"][0], [round(g.pos[1]["lat"], 6), round(g.pos[1]["lon"], 6)])

    def test_resolve_marks_actual(self):
        from pipeline.predict import predict, resolve
        g = self._grid()
        pos = lambda n: {"lat": g.pos[n]["lat"], "lon": g.pos[n]["lon"]}
        cams = [dict(id=f"C{n}", name=f"cam{n}", alive=True, **pos(n)) for n in range(9)]
        seen = [dict(id="S1", t="2026-08-15T18:00:00-07:00", camera_id="C0", state="linked", track_id="T-SUBJ", **pos(0)),
                dict(id="S2", t="2026-08-15T18:00:20-07:00", camera_id="C1", state="linked", track_id="T-SUBJ", **pos(1))]
        later = [dict(id="S3", t="2026-08-15T18:00:40-07:00", camera_id="C4", state="linked", track_id="T-SUBJ", **pos(4))]
        p = resolve(predict(cams, seen, g, horizon_s=120), later)
        self.assertIn("actual", p); self.assertEqual(p["resolved_at"], later[0]["t"])

    @unittest.skipUnless((ROOT / "data" / "road_graph.json").exists(), "road graph not built")
    def test_real_graph_routes_along_second_avenue(self):
        from pipeline.roadgraph import RoadGraph
        g = RoadGraph.load()
        a = g.nearest({"lat": 47.609268, "lon": -122.338888})   # 2nd & Pike
        b = g.nearest({"lat": 47.607347, "lon": -122.337128})   # 2nd & University
        d, path = g.shortest_path(a, b)
        self.assertLess(d, 320); self.assertGreater(d, 200)
        self.assertIn("2nd Avenue", g.street_names(path))


if __name__ == "__main__":
    unittest.main()


class TestVSSIdentity(unittest.TestCase):
    """A live port is not proof it is VSS. See vss/client.health()."""

    def setUp(self):
        from vss.client import identify, looks_like_vss
        self.looks_like_vss, self.identify = looks_like_vss, identify

    def test_real_321_surface_accepted(self):
        """The 3.2.1 agent on gn100-223b:8010 (verified live 2026-08-15)."""
        live = {"/openapi.json", "/generate", "/chat", "/v1/chat/completions",
                "/api/v1/videos", "/api/v1/videos/{sensor_id}/complete",
                "/api/v1/videos-for-search/{filename}", "/api/v1/videos/{video_id}"}
        self.assertTrue(self.looks_like_vss(live))

    def test_legacy_surface_still_accepted(self):
        self.assertTrue(self.looks_like_vss(
            {"/files", "/summarize", "/chat/completions", "/health/ready"}))

    def test_vllm_surface_rejected(self):
        """Observed on gn100-223b:8000 - answers /health 200, shares
        /v1/chat/completions, but has no video ingest."""
        vllm = {"/health", "/ping", "/v1/models", "/v1/chat/completions",
                "/tokenize", "/detokenize", "/v1/completions", "/metrics"}
        self.assertFalse(self.looks_like_vss(vllm))
        self.assertIn("vLLM", self.identify(vllm))

    def test_chat_completions_alone_is_not_enough(self):
        self.assertFalse(self.looks_like_vss({"/v1/chat/completions"}))
        self.assertFalse(self.looks_like_vss({"/generate", "/chat"}))


class TestVSSAnswerParsing(unittest.TestCase):
    """The agent wraps its trace in <agent-think>; callers want the answer and
    the VLM tool's own words. Sample is a real reply from the box."""

    RAW = ('<agent-think><agent-think-step title="1 - Thought">Plan: 1. Call '
           '`video_understanding` with sensor_id=\'westlake-22s\'</agent-think-step>\n'
           '<agent-think-step title="2 - Tool Call">Tool: video_understanding Args: '
           "{'sensor_id': 'westlake-22s'} Result: Yes, a person on the sidewalk is "
           'waving. They are wearing a grey shirt.</agent-think-step></agent-think>'
           '\n\nThe video analysis confirms that a person is waving at the camera.')

    def test_strip_think(self):
        from vss.client import strip_think
        self.assertEqual(strip_think(self.RAW),
                         "The video analysis confirms that a person is waving at the camera.")

    def test_think_steps_extracted(self):
        from vss.client import think_steps
        steps = think_steps(self.RAW)
        self.assertEqual(len(steps), 2)
        self.assertIn("video_understanding", steps[1])

    def test_failure_is_detectable(self):
        from vss.client import strip_think
        failed = ('<agent-think><agent-think-step title="1 - Error">Error: Cannot connect'
                  '</agent-think-step></agent-think>\n\nSorry, I wasn\'t able to '
                  'complete your request. Please try again.')
        self.assertTrue(strip_think(failed).startswith("Sorry, I wasn't able"))


class TestRouting(unittest.TestCase):
    """pipeline/roadgraph.py + pipeline/predict.py: classical, no LLM."""

    def test_bearing_and_turn(self):
        from pipeline.roadgraph import bearing_deg, turn_deg
        n = bearing_deg({"lat": 47.60, "lon": -122.33}, {"lat": 47.61, "lon": -122.33})
        e = bearing_deg({"lat": 47.60, "lon": -122.33}, {"lat": 47.60, "lon": -122.32})
        self.assertAlmostEqual(n, 0.0, places=0)
        self.assertAlmostEqual(e, 90.0, places=0)
        self.assertAlmostEqual(turn_deg(350.0, 10.0), 20.0)
        self.assertAlmostEqual(turn_deg(10.0, 350.0), -20.0)
        self.assertEqual(turn_deg(0.0, 180.0), 180.0)

    def _grid(self):
        # 3x3 grid, 100 m spacing, two-way, node ids r*3+c
        from pipeline.roadgraph import RoadGraph
        nodes, edges = {}, []
        for r in range(3):
            for c in range(3):
                nodes[str(r * 3 + c)] = [47.600 + r * 0.0009, -122.330 + c * 0.00133]
        for r in range(3):
            for c in range(3):
                i = r * 3 + c
                if c < 2: edges += [[i, i + 1, 100.0, "EW"], [i + 1, i, 100.0, "EW"]]
                if r < 2: edges += [[i, i + 3, 100.0, "NS"], [i + 3, i, 100.0, "NS"]]
        return RoadGraph({"nodes": nodes, "edges": edges})

    def test_dijkstra_on_grid(self):
        g = self._grid()
        d, path = g.shortest_path(0, 8)
        self.assertAlmostEqual(d, 400.0)
        self.assertEqual(path[0], 0); self.assertEqual(path[-1], 8); self.assertEqual(len(path), 5)
        self.assertIsNone(g.shortest_path(0, 8, max_m=300))

    def test_prediction_prefers_straight_on_and_sums_to_one(self):
        from pipeline.predict import predict
        g = self._grid()
        pos = lambda n: {"lat": g.pos[n]["lat"], "lon": g.pos[n]["lon"]}
        cams = [dict(id=f"C{n}", name=f"cam{n}", alive=True, **pos(n)) for n in range(9)]
        # travelling east along the bottom row: node 0 -> node 1 seen; candidates 2 (straight), 4 (turn), ...
        seen = [dict(id="S1", t="2026-08-15T18:00:00-07:00", camera_id="C0", state="linked", track_id="T-SUBJ", **pos(0)),
                dict(id="S2", t="2026-08-15T18:00:20-07:00", camera_id="C1", state="linked", track_id="T-SUBJ", **pos(1))]
        p = predict(cams, seen, g, horizon_s=120)
        self.assertIsNotNone(p)
        self.assertEqual(p["at_camera"], "C1")
        self.assertAlmostEqual(sum(b["p"] for b in p["branches"]), 1.0, places=3)
        self.assertEqual(p["branches"][0]["camera_id"], "C2", "straight on should rank first")
        self.assertTrue(all(len(b["path"]) >= 2 for b in p["branches"]))
        for b in p["branches"]:
            self.assertEqual(b["path"][0], [round(g.pos[1]["lat"], 6), round(g.pos[1]["lon"], 6)])

    def test_resolve_marks_actual(self):
        from pipeline.predict import predict, resolve
        g = self._grid()
        pos = lambda n: {"lat": g.pos[n]["lat"], "lon": g.pos[n]["lon"]}
        cams = [dict(id=f"C{n}", name=f"cam{n}", alive=True, **pos(n)) for n in range(9)]
        seen = [dict(id="S1", t="2026-08-15T18:00:00-07:00", camera_id="C0", state="linked", track_id="T-SUBJ", **pos(0)),
                dict(id="S2", t="2026-08-15T18:00:20-07:00", camera_id="C1", state="linked", track_id="T-SUBJ", **pos(1))]
        later = [dict(id="S3", t="2026-08-15T18:00:40-07:00", camera_id="C4", state="linked", track_id="T-SUBJ", **pos(4))]
        p = resolve(predict(cams, seen, g, horizon_s=120), later)
        self.assertIn("actual", p); self.assertEqual(p["resolved_at"], later[0]["t"])

    @unittest.skipUnless((ROOT / "data" / "road_graph.json").exists(), "road graph not built")
    def test_real_graph_routes_along_second_avenue(self):
        from pipeline.roadgraph import RoadGraph
        g = RoadGraph.load()
        a = g.nearest({"lat": 47.609268, "lon": -122.338888})   # 2nd & Pike
        b = g.nearest({"lat": 47.607347, "lon": -122.337128})   # 2nd & University
        d, path = g.shortest_path(a, b)
        self.assertLess(d, 320); self.assertGreater(d, 200)
        self.assertIn("2nd Avenue", g.street_names(path))


if __name__ == "__main__":
    unittest.main()
