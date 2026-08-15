"""Road graph for the routing engine. Stdlib only.

Source: OpenStreetMap ways (highway=* drivable/cycleway) for downtown Seattle +
South Lake Union, fetched once from Overpass and cached as data/road_graph.json
so nothing at demo time depends on the internet. Rebuild with:

    python -m pipeline.roadgraph --build overpass.json      # raw Overpass output
    python -m pipeline.roadgraph --stats

Graph model: nodes = OSM nodes with lat/lon; edges = consecutive nodes along a
way, directed, respecting oneway=yes/-1. Edge weight = metres. That is all
Dijkstra needs. VSS has no concept of any of this; this file is the seam
between "what the cameras saw" and "where the road can take you next".
"""

from __future__ import annotations

import argparse
import heapq
import json
import math
import sys
from pathlib import Path
from typing import Any

from .common import DATA, distance_m, load_json, write_json

ROAD_GRAPH = DATA / "road_graph.json"

KEEP = {"motorway", "trunk", "primary", "secondary", "tertiary", "unclassified",
        "residential", "living_street", "cycleway", "primary_link",
        "secondary_link", "tertiary_link", "trunk_link", "motorway_link"}


def bearing_deg(a: dict[str, float], b: dict[str, float]) -> float:
    """Initial compass bearing from a to b, degrees clockwise from north."""
    la1, la2 = math.radians(a["lat"]), math.radians(b["lat"])
    dlon = math.radians(b["lon"] - a["lon"])
    x = math.sin(dlon) * math.cos(la2)
    y = math.cos(la1) * math.sin(la2) - math.sin(la1) * math.cos(la2) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0


def turn_deg(from_bearing: float, to_bearing: float) -> float:
    """Smallest signed turn from one heading to another, in (-180, 180]."""
    d = (to_bearing - from_bearing + 180.0) % 360.0 - 180.0
    return 180.0 if d == -180.0 else d


def build(overpass_json: Path) -> dict[str, Any]:
    raw = load_json(overpass_json)
    nodes: dict[int, tuple[float, float]] = {}
    ways = []
    for el in raw["elements"]:
        if el["type"] == "node":
            nodes[el["id"]] = (el["lat"], el["lon"])
        elif el["type"] == "way" and el.get("tags", {}).get("highway") in KEEP:
            ways.append(el)
    used: set[int] = set()
    edges: list[list[Any]] = []       # [from_id, to_id, metres, way_name]
    for w in ways:
        tags = w.get("tags", {})
        oneway = tags.get("oneway", "no")
        if tags.get("junction") == "roundabout":
            oneway = "yes"
        name = tags.get("name", "")
        nds = [n for n in w["nodes"] if n in nodes]
        for a, b in zip(nds, nds[1:]):
            la, lo = nodes[a]; lb, lb2 = nodes[b]
            m = distance_m({"lat": la, "lon": lo}, {"lat": lb, "lon": lb2})
            used.update((a, b))
            if oneway == "-1":
                edges.append([b, a, round(m, 1), name])
            else:
                edges.append([a, b, round(m, 1), name])
                if oneway not in ("yes", "true", "1"):
                    edges.append([b, a, round(m, 1), name])
    return {
        "meta": {"source": "OpenStreetMap via Overpass (ODbL)", "highway_types": sorted(KEEP),
                 "note": "directed edges [from, to, metres, name]; ids are OSM node ids"},
        "nodes": {str(n): [round(nodes[n][0], 6), round(nodes[n][1], 6)] for n in used},
        "edges": edges,
    }


class RoadGraph:
    def __init__(self, data: dict[str, Any]):
        self.pos: dict[int, dict[str, float]] = {
            int(k): {"lat": v[0], "lon": v[1]} for k, v in data["nodes"].items()}
        self.adj: dict[int, list[tuple[int, float, str]]] = {}
        for a, b, m, name in data["edges"]:
            self.adj.setdefault(a, []).append((b, m, name))
        self._grid: dict[tuple[int, int], list[int]] = {}
        for n, p in self.pos.items():
            self._grid.setdefault(self._cell(p), []).append(n)

    @staticmethod
    def _cell(p: dict[str, float]) -> tuple[int, int]:
        return (int(p["lat"] * 500), int(p["lon"] * 500))  # ~220 m cells

    @classmethod
    def load(cls, path: Path = ROAD_GRAPH) -> "RoadGraph":
        return cls(load_json(path))

    def nearest(self, p: dict[str, float], max_m: float = 120.0) -> int | None:
        """Nearest graph node with an outgoing edge, within max_m metres."""
        cx, cy = self._cell(p)
        best, best_d = None, max_m
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for n in self._grid.get((cx + dx, cy + dy), []):
                    if n not in self.adj:
                        continue
                    d = distance_m(p, self.pos[n])
                    if d < best_d:
                        best, best_d = n, d
        return best

    def shortest_path(self, src: int, dst: int, max_m: float = 3000.0
                      ) -> tuple[float, list[int]] | None:
        """Dijkstra. Returns (metres, [node ids]) or None if unreachable within max_m."""
        dist = {src: 0.0}
        prev: dict[int, int] = {}
        heap = [(0.0, src)]
        while heap:
            d, u = heapq.heappop(heap)
            if d > max_m:
                return None          # heap is ordered: nothing cheaper is coming
            if u == dst:
                path = [u]
                while u in prev:
                    u = prev[u]; path.append(u)
                return d, path[::-1]
            if d > dist.get(u, math.inf):
                continue
            for v, m, _ in self.adj.get(u, []):
                nd = d + m
                if nd < dist.get(v, math.inf):
                    dist[v] = nd; prev[v] = u
                    heapq.heappush(heap, (nd, v))
        return None

    def coords(self, path: list[int]) -> list[list[float]]:
        return [[self.pos[n]["lat"], self.pos[n]["lon"]] for n in path]

    def initial_bearing(self, path: list[int], lookahead_m: float = 60.0) -> float:
        """Bearing of the first ~60 m of a path - what a driver 'commits' to."""
        if len(path) < 2:
            return 0.0
        start = self.pos[path[0]]
        acc, end = 0.0, self.pos[path[1]]
        for a, b in zip(path, path[1:]):
            acc += distance_m(self.pos[a], self.pos[b]); end = self.pos[b]
            if acc >= lookahead_m:
                break
        return bearing_deg(start, end)

    def street_names(self, path: list[int]) -> list[str]:
        out: list[str] = []
        for a, b in zip(path, path[1:]):
            for v, _, name in self.adj.get(a, []):
                if v == b and name and (not out or out[-1] != name):
                    out.append(name); break
        return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--build", metavar="OVERPASS_JSON", help="raw Overpass output to compile")
    ap.add_argument("--stats", action="store_true")
    a = ap.parse_args()
    if a.build:
        g = build(Path(a.build))
        write_json(ROAD_GRAPH, g)
        print(f"wrote {ROAD_GRAPH}: {len(g['nodes'])} nodes, {len(g['edges'])} directed edges")
    if a.stats or not a.build:
        if not ROAD_GRAPH.exists():
            print(f"missing {ROAD_GRAPH} - run --build", file=sys.stderr); sys.exit(2)
        g = RoadGraph.load()
        print(f"{len(g.pos)} nodes, {sum(len(v) for v in g.adj.values())} edges")


if __name__ == "__main__":
    main()
