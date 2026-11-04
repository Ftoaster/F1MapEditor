# Changelog

## 0.3.4

- Smoother mouse wheel zoom on the map: zooms around the point under the cursor, one wheel notch is always the same step
  (touchpads and fine-step wheels zoomed far too fast), no jump at the zoom limits

## 0.3.3

- World maps: the ground blend map no longer puts dirt patches on asphalt, kerbs and the pit lane (tracks built with
  world maps showed brown-green blotches on the road). Rebuild a track to get the fix

## 0.3.2

- Replays work on your own tracks: the build moves the carrier's replay, TV and pit stop cameras and their trigger zones
  to the new track (before they stayed where the Portimão track runs). A new map layer shows them, and under
  Properties → Cameras (replays) you can switch a camera group off, shift it along the track or move its cameras
- New Cameras tool (K) in the top bar: drag cameras freely and trigger zones along the track, or let the editor
  calculate them for the current track; lines show which cameras each zone switches to (read from the game's event graph)

## 0.3.1

- English is now the default language: a first start opens in English, and texts without a German translation show in
  English. A language chosen before (View → Language) is kept. Messages of the track tools are English first too
- The German interface also translates the import notes in the status bar and the texts of background tasks
- Load a track from the game: New from template lists all F1 25 tracks (read-only). Besides centre line, heights and widths
  it can take over the pit lane, DRS and Straight Mode zones, the starting grid, kerbs, gravel and barrier distances from the
  driving physics, and the scenery (trees, buildings, tents) as environment objects. Packages made from an original track are
  for your own use only
- Templates that do not fit into the game world are rotated first and only scaled down if rotating is not enough; the game
  world offset is set when a track only fits shifted into the hole of the outer terrain
- Tight hairpins of imported tracks no longer fold the track edge (Bahrain, Shanghai, Austin)

## 0.3.0

- Trace a track from an image: load a map picture or paste a screenshot (Ctrl+V), measure the scale on its scale bar, click a few support points in driving direction, and the editor finds the track between them. It only takes over a lap that is complete, does not cross itself and drives no part twice; red rings show where the lap is wrong, an orange "B" offers a second possible way
- Scale track: Edit → Scale track … makes the whole track larger or smaller by a factor or brings it to a target length in metres; sections, zones and the environment move along, heights and widths stay

## 0.2.2

- Combine tracks (issue #2): copy and paste track points with Ctrl+C / Ctrl+X / Ctrl+V, also between two editor windows. A pasted piece joins the open end of an open track with its fitting end, red rings show where it overlaps the track, and new commands open a lap at the selection, cut an overlap and reverse a piece
- Pit lane speed limit 60 or 80 km/h (the values of the game's tracks); race data writes it to the game with the time a pit stop costs
- Race data also sets the leaderboard minimum lap time from the track, so short tracks no longer inherit 66 s from the base track
- White lines no longer show holes: the track now lies under the painted lines as on the original tracks, so worn paint shows tarmac instead of a see-through gap
- Known issue: through Swofty Launcher the gate to your pit crew is missing when you drive into the pits yourself

## 0.2.1

- Pit lane on the left works: the build sets the track's mirror flags (`pitMirrored` and friends) like the original left-side tracks
- Shared carrier for Swofty Launcher: the Portimão textures sit once in a carrier addon, each track is 0.45 GB instead of 3 GB
- AI cars leave the pit lane on an exit lane and join the racing line before the next corner, instead of cutting across the track

## 0.2.0

- New build target Swofty Launcher: an addon with its own track code and its own track name in the game's menus, no installer slots
- New check KI-07: an AI route on which the passing space never changes crashes the game as soon as an AI car drives it
- Laps, fuel, tyres and AI lap times are worked out from the track, for both build targets
- Buttons to install an addon in Swofty Launcher, and to start the game from there, with a summary of crashes afterwards
- Grid option for 26 cars: grid slots, garages and pit boxes for the track (the cars themselves need a grid addon)
- The build dialog only shows the buttons you can use right now, Clean up moved to the Tools menu

## 0.1.3

- Works with the Portimão mod 1.0.2 (the current download) as well as 1.0.0 (see issue #1)
- On mod 1.0.2 the carrier's trees, old distance boards, zone signs and sponsor areas no longer float over the new track, and the road, kerbs and
  lines use the generated ground maps (the mod keeps copies of them under hashed names)
- Garages of the 2026, story and MyTeam variants move to the new pit boxes
- The AI timing tables keep the carrier's resolution on long tracks when they would otherwise exceed the size the game loads (300 MB estimate);
  with mod 1.0.0 nothing changes
- The build checks the carrier right after unpacking and names the mod version when it does not fit
- The build warns about carrier layers it has no rule for, so a future mod version does not go unnoticed

## 0.1.2

- Fill gaps now also works for two selected neighbouring points when the lap has no large gap
- Fill gaps stays off until the lap is closed and says why, instead of failing after the click
- Error messages, warnings and build phases now appear in the selected language
- Builds work when the output folder is on a different drive than the program
- Large rings with no tight corner can be built: corners are found from the strongest curvature and long corners take all braking lines
- The check reports a track outside the outer terrain hole and names the game world offset that fixes it

## 0.1.1

- Close/Open lap is back in the toolbar, next to the drawing tools, and lights up while the lap is open
- With fewer than four points the button is greyed out and says why
- A closed lap with fewer than four points can be opened again

## 0.1.0

First public version.

### Editor

- Track editing with control points or segments, height and banking profiles, cross-section per range
- Edge angle beside the road: level, following the track, or a fixed angle, shown live in the cross-section view
- Connected selections become the cross-section range automatically
- Racing line, braking points, corners, sectors, DRS and Straight Mode zones
- Pit lane, grid, environment objects, hills and mountains
- Rule check with messages linked to the map
- Build dialog, quick swap with backup, playtest notes
- German and English interface, built-in help

### Build

- Own ground, racing line and surroundings maps, landscape (grass, desert) and colour tone options
- Garage modules of the carrier track moved to the new pit boxes
- Grid markings as on the original tracks
- Game folder detected automatically on all drives

### Known limitations

See "Known limitations" in the README, above all: pit lane on the right only.
