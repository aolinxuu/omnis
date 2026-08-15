# Sighting feed contract — FROZEN

`data/sightings.json` is the contract. Frontend builds against it and never touches the pipeline.
Pipeline's only integration requirement is to emit these exact shapes (over WebSocket, one message per object, `{"type": "sighting"|"event"|"prediction"|"camera", ...}`).
Changes after the freeze require all three of us to agree. Expect zero.

## sighting  (fast lane — YOLO, sub-second)
| field | type | notes |
|---|---|---|
| `id` | string | unique, e.g. `S-0019` |
| `t` | ISO-8601 with offset | wall clock, `2026-08-15T18:05:02-07:00` |
| `camera_id` | string | SDOT `CMR-0302` or WSDOT numeric as string |
| `lat`, `lon` | number | camera location (we do not geolocate within the frame) |
| `class` | `scooter` \| `bike` | |
| `state` | `detected` \| `unverified` \| `confirmed` \| `linked` \| `lost` | marker state machine, in this order |
| `conf` | 0–1 | detector confidence; `lost` carries the decayed value |
| `track_id` | string, optional | present when `state` is `linked` or `lost` |
| `bbox` | `[x, y, w, h]` px, optional | in the source frame; drawn on the camera tile |
| `frame_url` | string, optional | still of the frame with the box, if saved |
| `note` | string, optional | free text, debugging only, never shown |

## event  (slow lane — VSS, seconds late; goes to the ticker)
`t`, `kind` (`vss` \| `system`), `text`, optional `camera_id`.

## prediction  (routing engine)
`id`, `t`, `track_id`, `at` `[lat, lon]`, `at_camera`, `branches[]` of `{label, p, path[[lat,lon],...]}` (p sums to 1), then later `actual` + `resolved_at` once known.

## camera  (ingestion health)
`id`, `name`, `lat`, `lon`, `kind` (`sdot` \| `wsdot`), `alive` bool.

## track
`id`, `label`, `class`, optional `color` (`amber` = the consenting subject).

## ground_truth  (eval only — additive, added at minute ~40 with all three aware)
`camera_id`, `t`, `kind` (`wave`), optional `note`. The presenter waves at each camera; the eval panel compares these to linked subject sightings per camera → precision / recall.
