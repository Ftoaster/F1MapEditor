Mapeditor is a Windows editor for designing your own tracks for F1 25.

## New in 0.1.2

- Builds work when the output folder is on a different drive than the program
- Error messages, warnings and build phases now appear in the selected language
- Large rings with no tight corner can be built
- The check reports a track outside the outer terrain hole and names the game world offset that fixes it
- Fill gaps also works for two selected neighbouring points, and stays off with a reason until the lap is closed

**Unofficial fan project, not affiliated with EA, Codemasters, Formula One Group or the FIA. Contains no game files.**

## Download

`Mapeditor-0.1.2-win64.zip`: unpack the whole folder and start `Mapeditor.exe`. The program is not signed, so Windows may
show a warning.

## What you need

- Windows 10 or 11, 64-bit
- [F1 25 on Steam](https://store.steampowered.com/app/3059520/F1_25/), to build packages
- [Algarve International Circuit (Portimao) for F1 25](https://www.overtake.gg/downloads/algarve-international-circuit-portimao-for-f1-25.87333/) (`portimao.zip`), **not included**, as the base for building
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
- The Portimão mod's page warns of game crashes on AMD graphics cards. Tracks built on it may be affected as well.

Use offline and at your own risk. Back up your save data and game database first.

## Licence

GPL-3.0-only for this project's code, see `LICENSE`. The source code of this release is in this repository at tag `v0.1.2`.
