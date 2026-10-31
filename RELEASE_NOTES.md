Mapeditor is a Windows editor for designing your own tracks for F1 25.

## New in 0.1.3

- **Works with the Portimão mod 1.0.2** (the current download) as well as 1.0.0 (issue #1)
- On 1.0.2 the old trees and signs no longer hang over the new track, and the road uses the generated ground maps
- Garages of the 2026, story and MyTeam variants move to the new pit boxes
- AI timing tables are kept small enough for the game to load on long tracks; with 1.0.0 nothing changes
- The build names the carrier's mod version when it does not fit, and warns about layers it does not know

**Unofficial fan project, not affiliated with EA, Codemasters, Formula One Group or the FIA. Contains no game files.**

## Download

`Mapeditor-0.1.3-win64.zip`: unpack the whole folder and start `Mapeditor.exe`. The program is not signed, so Windows may
show a warning.

## What you need

- Windows 10 or 11, 64-bit
- [F1 25 on Steam](https://store.steampowered.com/app/3059520/F1_25/), to build packages
- [Algarve International Circuit (Portimao) for F1 25](https://www.overtake.gg/downloads/algarve-international-circuit-portimao-for-f1-25.87333/) (`portimao.zip`), **version 1.0.0 or 1.0.2**, **not included**, as the base for building
- [EGO GameDatabases Editor](https://apps.sztefek.eu/app/ego-gamedatabases-editor) 1.6.0 or newer, to install a track and activate it once

Drawing and checking tracks works without the game.

## Highlights

- Draw tracks from control points or segments, edit height, banking and cross-section
- Racing line, braking points, corners, sectors, DRS and Straight Mode zones
- Environment objects, hills and mountains
- Own ground and surroundings textures generated from your track
- Rule check before building, quick swap with backup for testing

## Known limitations

- **Pit lane on the right only.** On the left, cars and pit crews stand on the wall side until the car leaves.
- Lighting comes from the carrier track.
- Version 1.0.0 of the Portimão mod can crash on AMD graphics cards, which its version 1.0.2 fixes. Tracks built on 1.0.2 are tested in Time Trial only; races with AI on 1.0.2 are less tested, and on long tracks the AI timing tables there have a coarser resolution.

Use offline and at your own risk. Back up your save data and game database first.

## Licence

GPL-3.0-only for this project's code, see `LICENSE`. The source code of this release is in this repository at tag `v0.1.3`.
