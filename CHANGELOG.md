# Changelog

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
